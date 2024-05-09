from datetime import datetime, timezone

import boto3
from moto import mock_aws

from s3_du import metrics
from s3_du.cli import main

NOW = datetime(2030, 1, 3, 12, 0, 0, tzinfo=timezone.utc)


def dims(bucket, storage_type):
    return [{"Name": "StorageType", "Value": storage_type}, {"Name": "BucketName", "Value": bucket}]


def size_metric(bucket, storage_type):
    return {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes", "Dimensions": dims(bucket, storage_type)}


def result(index, *values):
    return {"Id": f"m{index}", "Label": "x", "Values": list(values), "StatusCode": "Complete"}


def test_storage_type_to_class_mapping():
    cases = {
        "StandardStorage": "STANDARD",
        "StandardIAStorage": "STANDARD_IA",
        "StandardIASizeOverhead": "STANDARD_IA",
        "IntelligentTieringFAStorage": "INTELLIGENT_TIERING",
        "OneZoneIAStorage": "ONEZONE_IA",
        "GlacierInstantRetrievalStorage": "GLACIER_IR",
        "GlacierStorage": "GLACIER",
        "GlacierObjectOverhead": "GLACIER",
        "DeepArchiveStorage": "DEEP_ARCHIVE",
        "ReducedRedundancyStorage": "REDUCED_REDUNDANCY",
        "SomethingNewStorage": "SomethingNewStorage",
    }
    for storage_type, expected in cases.items():
        assert metrics.storage_class_for(storage_type) == expected


def test_all_buckets_across_regions_and_storage_types(stubbed):
    stubbed.stub("s3").add_response(
        "list_buckets",
        {"Buckets": [
            {"Name": "logs", "BucketRegion": "us-east-1"},
            {"Name": "backups", "BucketRegion": "eu-west-1"},
            {"Name": "empty", "BucketRegion": "us-east-1"},
        ]},
    )
    stubbed.stub("cloudwatch", "eu-west-1").add_response(
        "list_metrics",
        {"Metrics": [size_metric("backups", "GlacierStorage"), size_metric("backups", "GlacierObjectOverhead")]},
        {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes"},
    )
    stubbed.stub("cloudwatch", "eu-west-1").add_response(
        "get_metric_data", {"MetricDataResults": [result(0, 32.0), result(1, 4000.0, 3900.0), result(2, 7.0)]}
    )
    stubbed.stub("cloudwatch", "us-east-1").add_response(
        "list_metrics",
        {"Metrics": [size_metric("logs", "StandardStorage"), size_metric("logs", "StandardIAStorage")]},
    )
    stubbed.stub("cloudwatch", "us-east-1").add_response(
        "get_metric_data", {"MetricDataResults": [result(0, 1000.0), result(1, 500.0), result(2, 42.0)]}
    )
    report = metrics.collect(stubbed, NOW)
    stubbed.assert_all_used()
    rows = {row.name: row for row in report.rows}
    # sorted series for eu-west-1: GlacierObjectOverhead (m0), GlacierStorage (m1), then object count (m2)
    assert rows["backups"].classes == {"GLACIER": [4032, 0]}
    assert rows["backups"].objects == 7
    assert rows["logs"].classes == {"STANDARD_IA": [1000, 0], "STANDARD": [500, 0]}
    assert rows["logs"].size == 1500 and rows["logs"].objects == 42
    assert rows["empty"].size == 0 and rows["empty"].objects == 0
    assert "no storage metrics" in rows["empty"].note
    assert [row.name for row in report.sorted_rows()] == ["backups", "logs", "empty"]


def test_single_bucket_uses_location_and_dimension_filter(stubbed):
    stubbed.stub("s3").add_response("get_bucket_location", {"LocationConstraint": "EU"}, {"Bucket": "old-eu"})
    stubbed.stub("cloudwatch", "eu-west-1").add_response(
        "list_metrics",
        {"Metrics": [size_metric("old-eu", "StandardStorage")]},
        {"Namespace": "AWS/S3", "MetricName": "BucketSizeBytes",
         "Dimensions": [{"Name": "BucketName", "Value": "old-eu"}]},
    )
    stubbed.stub("cloudwatch", "eu-west-1").add_response(
        "get_metric_data", {"MetricDataResults": [result(0, 2048.0), result(1)]}
    )
    report = metrics.collect(stubbed, NOW, "old-eu")
    stubbed.assert_all_used()
    assert report.scope == "s3://old-eu"
    assert report.rows[0].size == 2048
    assert report.rows[0].objects is None  # size known, object count missing


def test_null_location_means_us_east_1_and_denied_location_is_noted(stubbed):
    s3 = stubbed.stub("s3")
    s3.add_response("list_buckets", {"Buckets": [{"Name": "classic"}, {"Name": "locked"}]})
    s3.add_response("get_bucket_location", {}, {"Bucket": "classic"})
    s3.add_client_error("get_bucket_location", service_error_code="AccessDenied")
    cloudwatch = stubbed.stub("cloudwatch", "us-east-1")
    cloudwatch.add_response("list_metrics", {"Metrics": [size_metric("classic", "StandardStorage")]})
    cloudwatch.add_response("get_metric_data", {"MetricDataResults": [result(0, 10.0), result(1, 1.0)]})
    rows = {row.name: row for row in metrics.collect(stubbed, NOW).rows}
    stubbed.assert_all_used()
    assert rows["classic"].size == 10
    assert rows["locked"].note == "region unknown (GetBucketLocation denied)"


def test_more_than_500_series_are_split_into_several_calls(stubbed):
    buckets = [f"bucket-{i:03d}" for i in range(300)]
    stubbed.stub("s3").add_response(
        "list_buckets", {"Buckets": [{"Name": name, "BucketRegion": "us-east-1"} for name in buckets]}
    )
    cloudwatch = stubbed.stub("cloudwatch", "us-east-1")
    cloudwatch.add_response("list_metrics", {"Metrics": [size_metric(name, "StandardStorage") for name in buckets]})
    # 300 size series + 300 object counts = 600 queries -> calls of 500 and 100
    cloudwatch.add_response("get_metric_data", {"MetricDataResults": [result(i, float(i)) for i in range(500)]})
    cloudwatch.add_response("get_metric_data", {"MetricDataResults": [result(i, 9.0) for i in range(500, 600)]})
    rows = {row.name: row for row in metrics.collect(stubbed, NOW).rows}
    stubbed.assert_all_used()
    assert rows["bucket-007"].size == 7
    assert rows["bucket-299"].size == 299
    assert rows["bucket-000"].objects == 300  # index 300 is the first object count
    assert rows["bucket-250"].objects == 9


@mock_aws
def test_cloudwatch_mode_end_to_end_against_moto(capsys):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="acme-logs")
    s3.create_bucket(Bucket="acme-eu", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    for index in range(3):
        s3.put_object(Bucket="acme-logs", Key=f"logs/{index}.gz", Body=b"x" * 100)
    s3.put_object(Bucket="acme-eu", Key="a", Body=b"x")
    assert main(["--format", "csv"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "name,storage_class,bytes,objects"
    parsed = {line.split(",")[0]: line.split(",") for line in lines[1:]}
    # moto invents its own byte sizes for these metrics, so only check what is meaningful.
    assert set(parsed) == {"acme-logs", "acme-eu"}
    assert parsed["acme-logs"][3] == "3" and parsed["acme-eu"][3] == "1"
    assert int(parsed["acme-logs"][2]) > int(parsed["acme-eu"][2]) > 0
    assert main(["--bucket", "acme-eu", "--by-class"]) == 0
    out = capsys.readouterr().out
    assert "s3://acme-eu  (source: CloudWatch daily storage metrics)" in out
    assert "STANDARD" in out and "acme-logs" not in out


@mock_aws
def test_missing_bucket_is_an_error(capsys):
    assert main(["--bucket", "does-not-exist"]) == 1
    assert "NoSuchBucket" in capsys.readouterr().err
