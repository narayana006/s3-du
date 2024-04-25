"""Per-prefix sizes from object listings, plus the storage a listing hides.

Listing is exact and current but costs one ListObjectsV2 request per 1,000
objects, so it is only done for the bucket and prefix you ask for. Top-level
prefixes are listed in parallel.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

from .model import Extras, Report, Row

DEFAULT_CLASS = "STANDARD"


def group_for(key: str, base: str, depth: int) -> str:
    """The prefix a key is counted under: its first `depth` folders below `base`.

    group_for("logs/2024/01/a.gz", "", 2) -> "logs/2024/"
    group_for("logs/readme.txt", "", 2)   -> "logs/"
    group_for("readme.txt", "", 2)        -> ""   (objects directly under base)
    """
    relative = key[len(base):] if key.startswith(base) else key
    folders = relative.split("/")[:-1][:depth]
    return base + "".join(folder + "/" for folder in folders)


class Aggregator:
    """Sums objects into rows keyed by prefix group."""

    def __init__(self, base: str, depth: int):
        self.base, self.depth = base, depth
        self.rows: dict[str, Row] = {}

    def add(self, key: str, size: int, storage_class: Optional[str]) -> None:
        group = group_for(key, self.base, self.depth)
        row = self.rows.get(group)
        if row is None:
            row = self.rows[group] = Row(group)
        row.add(storage_class or DEFAULT_CLASS, size)

    def merge(self, other: "Aggregator") -> None:
        for group, row in other.rows.items():
            if group in self.rows:
                self.rows[group].merge(row)
            else:
                self.rows[group] = row


def _list_all(s3, bucket: str, prefix: str, base: str, depth: int) -> Aggregator:
    aggregator = Aggregator(base, depth)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            aggregator.add(obj["Key"], obj["Size"], obj.get("StorageClass"))
    return aggregator


def list_prefixes(s3, bucket: str, prefix: str = "", depth: int = 1, workers: int = 8) -> list[Row]:
    """Sizes of everything under s3://bucket/prefix, grouped `depth` folders deep."""
    total = Aggregator(prefix, depth)
    children: list[str] = []
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")
    for page in pages:
        for obj in page.get("Contents", []):
            total.add(obj["Key"], obj["Size"], obj.get("StorageClass"))
        children += [item["Prefix"] for item in page.get("CommonPrefixes", [])]
    # boto3 clients are safe to share between threads.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for part in pool.map(lambda child: _list_all(s3, bucket, child, prefix, depth), children):
            total.merge(part)
    return list(total.rows.values())


def multipart_usage(s3, bucket: str, prefix: str, extras: Extras) -> None:
    """Add up the parts of multipart uploads that were started but never completed."""
    extras.multipart_uploads = 0
    pages = s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket, Prefix=prefix)
    for page in pages:
        for upload in page.get("Uploads", []):
            extras.multipart_uploads += 1
            started = upload["Initiated"].isoformat()
            if extras.multipart_oldest is None or started < extras.multipart_oldest:
                extras.multipart_oldest = started
            parts = s3.get_paginator("list_parts").paginate(
                Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
            for part_page in parts:
                for part in part_page.get("Parts", []):
                    extras.multipart_parts += 1
                    extras.multipart_bytes += part["Size"]


def _count_versions(versions: Iterable[dict], markers: Iterable[dict], extras: Extras) -> None:
    extras.noncurrent_versions = extras.noncurrent_versions or 0
    for version in versions:
        if not version.get("IsLatest"):
            extras.noncurrent_versions += 1
            extras.noncurrent_bytes += version.get("Size", 0)
    extras.delete_markers += sum(1 for _ in markers)


def version_usage(s3, bucket: str, prefix: str, extras: Extras) -> None:
    """Add up noncurrent (old) object versions and count delete markers."""
    extras.noncurrent_versions = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
        _count_versions(page.get("Versions", []), page.get("DeleteMarkers", []), extras)


def from_file(path: str, prefix: str, depth: int) -> Report:
    """Build a report from saved AWS CLI output instead of calling AWS.

    Accepts the JSON printed by `aws s3api list-objects-v2` (a "Contents"
    list) or by `aws s3api list-object-versions` ("Versions" and
    "DeleteMarkers"). With a versions file, current versions make up the rows
    and noncurrent versions are reported separately.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not ({"Contents", "Versions", "DeleteMarkers"} & set(data)):
        raise ValueError(f"{path}: expected JSON with a Contents list (list-objects-v2) or Versions (list-object-versions)")
    report = Report(source="file", scope=f"{path} prefix '{prefix}'" if prefix else path)
    aggregator = Aggregator(prefix, depth)
    for obj in data.get("Contents") or []:
        if obj["Key"].startswith(prefix):
            aggregator.add(obj["Key"], int(obj["Size"]), obj.get("StorageClass"))
    if "Versions" in data or "DeleteMarkers" in data:
        versions = [v for v in data.get("Versions") or [] if v["Key"].startswith(prefix)]
        markers = [m for m in data.get("DeleteMarkers") or [] if m["Key"].startswith(prefix)]
        for version in versions:
            if version.get("IsLatest"):
                aggregator.add(version["Key"], int(version["Size"]), version.get("StorageClass"))
        report.extras = Extras()
        _count_versions(versions, markers, report.extras)
    report.rows = list(aggregator.rows.values())
    return report
