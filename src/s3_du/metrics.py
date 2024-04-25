"""Bucket sizes from the daily S3 storage metrics in CloudWatch.

S3 publishes two free metrics per bucket, once a day, in the bucket's own
region (namespace AWS/S3):

* BucketSizeBytes, one time series per StorageType (StandardStorage,
  StandardIAStorage, GlacierStorage, ...)
* NumberOfObjects, with StorageType=AllStorageTypes

Reading them costs a handful of API calls regardless of how many objects the
bucket holds, which is what makes this mode fast.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from botocore.exceptions import ClientError

from .model import Report, Row

NAMESPACE = "AWS/S3"
MAX_QUERIES_PER_CALL = 500
LOOKBACK_DAYS = 3  # the daily data point can lag by a day or more

# StorageType prefix -> storage class, most specific prefix first.
_CLASS_PREFIXES = [
    ("StandardIA", "STANDARD_IA"),
    ("Standard", "STANDARD"),
    ("IntelligentTiering", "INTELLIGENT_TIERING"),
    ("OneZoneIA", "ONEZONE_IA"),
    ("ReducedRedundancy", "REDUCED_REDUNDANCY"),
    ("GlacierInstantRetrieval", "GLACIER_IR"),
    ("GlacierIR", "GLACIER_IR"),
    ("DeepArchive", "DEEP_ARCHIVE"),
    ("Glacier", "GLACIER"),
    ("ExpressOneZone", "EXPRESS_ONEZONE"),
]


def storage_class_for(storage_type: str) -> str:
    """Map a CloudWatch StorageType to the storage class it belongs to.

    Several StorageTypes roll up into one class (for example GlacierStorage,
    GlacierObjectOverhead and GlacierS3ObjectOverhead are all GLACIER). An
    unrecognised StorageType is returned unchanged rather than guessed at.
    """
    for prefix, storage_class in _CLASS_PREFIXES:
        if storage_type.startswith(prefix):
            return storage_class
    return storage_type


def bucket_regions(s3, only: Optional[str] = None) -> dict[str, Optional[str]]:
    """Map bucket name -> region. A region of None means it could not be determined."""
    if only:
        names_with_region = [(only, None)]
    else:
        names_with_region = []
        for page in s3.get_paginator("list_buckets").paginate():
            for bucket in page.get("Buckets", []):
                names_with_region.append((bucket["Name"], bucket.get("BucketRegion")))
    regions: dict[str, Optional[str]] = {}
    for name, region in names_with_region:
        if not region:
            try:
                constraint = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
            except ClientError:
                if only:
                    raise
                regions[name] = None
                continue
            # Historical quirks: us-east-1 is reported as null and eu-west-1 may be "EU".
            region = {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(constraint, constraint)
        regions[name] = region
    return regions


def _dimension(metric: dict, name: str) -> Optional[str]:
    for dimension in metric.get("Dimensions", []):
        if dimension["Name"] == name:
            return dimension["Value"]
    return None


def _size_series(cloudwatch, only: Optional[str]) -> dict[tuple[str, str], list[dict]]:
    """Every BucketSizeBytes series in this region: (bucket, StorageType) -> its dimensions."""
    params = {"Namespace": NAMESPACE, "MetricName": "BucketSizeBytes"}
    if only:
        params["Dimensions"] = [{"Name": "BucketName", "Value": only}]
    series: dict[tuple[str, str], list[dict]] = {}
    for page in cloudwatch.get_paginator("list_metrics").paginate(**params):
        for metric in page.get("Metrics", []):
            bucket, storage_type = _dimension(metric, "BucketName"), _dimension(metric, "StorageType")
            if bucket and storage_type:
                series[(bucket, storage_type)] = metric["Dimensions"]
    return series


def _latest_values(cloudwatch, series: list[tuple[str, list[dict]]], now: datetime) -> dict[int, float]:
    """Latest daily average for each (metric name, dimensions) pair; index -> value."""
    values: dict[int, float] = {}
    for offset in range(0, len(series), MAX_QUERIES_PER_CALL):
        chunk = series[offset : offset + MAX_QUERIES_PER_CALL]
        queries = [
            {
                "Id": f"m{offset + index}",
                "MetricStat": {
                    "Metric": {"Namespace": NAMESPACE, "MetricName": metric_name, "Dimensions": dimensions},
                    "Period": 86400,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
            for index, (metric_name, dimensions) in enumerate(chunk)
        ]
        pages = cloudwatch.get_paginator("get_metric_data").paginate(
            MetricDataQueries=queries,
            StartTime=now - timedelta(days=LOOKBACK_DAYS),
            EndTime=now,
            ScanBy="TimestampDescending",
        )
        for page in pages:
            for result in page.get("MetricDataResults", []):
                index = int(result["Id"][1:])
                if result.get("Values") and index not in values:
                    values[index] = result["Values"][0]
    return values


def collect(session, now: datetime, bucket: Optional[str] = None) -> Report:
    """Build a report of bucket sizes from CloudWatch for one bucket or all of them."""
    regions = bucket_regions(session.client("s3"), bucket)
    report = Report(source="cloudwatch", scope=f"s3://{bucket}" if bucket else "all buckets")
    rows = {name: Row(name) for name in regions}
    by_region: dict[str, list[str]] = {}
    for name, region in regions.items():
        if region is None:
            rows[name].note = "region unknown (GetBucketLocation denied)"
        else:
            by_region.setdefault(region, []).append(name)

    for region, names in sorted(by_region.items()):
        cloudwatch = session.client("cloudwatch", region_name=region)
        wanted = set(names)
        sizes = {key: dims for key, dims in sorted(_size_series(cloudwatch, bucket).items()) if key[0] in wanted}
        # (metric name, bucket, storage type or None for the object count, dimensions)
        series = [("BucketSizeBytes", b, t, dims) for (b, t), dims in sizes.items()]
        counted = set()
        for (b, _), dims in sizes.items():
            if b not in counted:
                counted.add(b)
                # Same dimensions, in the order CloudWatch listed them, with the StorageType swapped.
                all_types = [
                    {"Name": d["Name"], "Value": "AllStorageTypes" if d["Name"] == "StorageType" else d["Value"]}
                    for d in dims
                ]
                series.append(("NumberOfObjects", b, None, all_types))
        values = _latest_values(cloudwatch, [(name, dims) for name, _, _, dims in series], now)
        for index, (metric_name, name, storage_type, _) in enumerate(series):
            if index not in values:
                continue
            if metric_name == "NumberOfObjects":
                rows[name].objects_override = int(values[index])
            else:
                rows[name].add(storage_class_for(storage_type), int(values[index]), 0)
    for row in rows.values():
        if not row.classes:
            row.objects_override = row.objects_override or 0
            row.note = row.note or "no storage metrics (empty, or created in the last day or two)"
        elif row.objects_override is None:
            row.objects_known = False
    report.rows = list(rows.values())
    return report
