import json

import boto3
import pytest
from moto import mock_aws

from s3_du import listing
from s3_du.cli import main
from s3_du.model import Extras


def fill(s3, bucket="acme-data"):
    s3.create_bucket(Bucket=bucket)
    objects = {
        "logs/2024/01/a.gz": (1000, "STANDARD"),
        "logs/2024/02/b.gz": (2000, "STANDARD"),
        "logs/2023/12/c.gz": (4000, "GLACIER"),
        "logs/readme.txt": (10, "STANDARD"),
        "images/raw/x.png": (50000, "STANDARD_IA"),
        "images/thumb/x.png": (500, "STANDARD"),
        "top.txt": (7, "STANDARD"),
    }
    for key, (size, storage_class) in objects.items():
        s3.put_object(Bucket=bucket, Key=key, Body=b"x" * size, StorageClass=storage_class)
    return s3


@pytest.mark.parametrize(
    "key, base, depth, expected",
    [
        ("logs/2024/01/a.gz", "", 2, "logs/2024/"),
        ("logs/2024/01/a.gz", "", 1, "logs/"),
        ("logs/readme.txt", "", 2, "logs/"),
        ("readme.txt", "", 2, ""),
        ("logs/2024/01/a.gz", "logs/", 1, "logs/2024/"),
        ("logs/2024/01/a.gz", "logs/20", 1, "logs/2024/"),
        ("a//b", "", 3, "a//"),
    ],
)
def test_group_for(key, base, depth, expected):
    assert listing.group_for(key, base, depth) == expected


@mock_aws
def test_depth_one_and_two():
    s3 = fill(boto3.client("s3"))
    one = {row.name: (row.size, row.objects) for row in listing.list_prefixes(s3, "acme-data", depth=1)}
    assert one == {"images/": (50500, 2), "logs/": (7010, 4), "": (7, 1)}
    two = {row.name: (row.size, row.objects) for row in listing.list_prefixes(s3, "acme-data", depth=2)}
    assert two == {
        "images/raw/": (50000, 1), "images/thumb/": (500, 1),
        "logs/2024/": (3000, 2), "logs/2023/": (4000, 1), "logs/": (10, 1), "": (7, 1),
    }


@mock_aws
def test_prefix_limits_the_listing_and_classes_are_split():
    s3 = fill(boto3.client("s3"))
    rows = {row.name: row for row in listing.list_prefixes(s3, "acme-data", prefix="logs/", depth=1, workers=2)}
    assert set(rows) == {"logs/2024/", "logs/2023/", "logs/"}
    assert rows["logs/2023/"].classes == {"GLACIER": [4000, 1]}
    whole = listing.list_prefixes(s3, "acme-data", prefix="logs/", depth=5)
    assert sum(row.size for row in whole) == 7010


@mock_aws
def test_listing_handles_more_than_one_page():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="big")
    for index in range(1105):
        s3.put_object(Bucket="big", Key=f"data/part-{index % 3}/{index:05d}", Body=b"ab")
    rows = {row.name: row for row in listing.list_prefixes(s3, "big", depth=2)}
    assert sum(row.objects for row in rows.values()) == 1105
    assert rows["data/part-0/"].objects == 369 and rows["data/part-0/"].size == 738


@mock_aws
def test_incomplete_multipart_uploads_are_summed():
    s3 = fill(boto3.client("s3"))
    first = s3.create_multipart_upload(Bucket="acme-data", Key="uploads/big.bin")["UploadId"]
    s3.upload_part(Bucket="acme-data", Key="uploads/big.bin", UploadId=first, PartNumber=1, Body=b"p" * 3000)
    s3.upload_part(Bucket="acme-data", Key="uploads/big.bin", UploadId=first, PartNumber=2, Body=b"p" * 2000)
    second = s3.create_multipart_upload(Bucket="acme-data", Key="other/file.bin")["UploadId"]
    s3.upload_part(Bucket="acme-data", Key="other/file.bin", UploadId=second, PartNumber=1, Body=b"p" * 100)
    extras = Extras()
    listing.multipart_usage(s3, "acme-data", "", extras)
    assert (extras.multipart_uploads, extras.multipart_parts, extras.multipart_bytes) == (2, 3, 5100)
    assert extras.multipart_oldest is not None
    scoped = Extras()
    listing.multipart_usage(s3, "acme-data", "uploads/", scoped)
    assert (scoped.multipart_uploads, scoped.multipart_bytes) == (1, 5000)


@mock_aws
def test_noncurrent_versions_and_delete_markers(capsys):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="versioned")
    s3.put_bucket_versioning(Bucket="versioned", VersioningConfiguration={"Status": "Enabled"})
    s3.put_object(Bucket="versioned", Key="doc/report.txt", Body=b"1" * 300)
    s3.put_object(Bucket="versioned", Key="doc/report.txt", Body=b"2" * 500)
    s3.put_object(Bucket="versioned", Key="doc/report.txt", Body=b"3" * 800)
    s3.put_object(Bucket="versioned", Key="gone.txt", Body=b"4" * 50)
    s3.delete_object(Bucket="versioned", Key="gone.txt")
    extras = Extras()
    listing.version_usage(s3, "versioned", "", extras)
    assert (extras.noncurrent_versions, extras.noncurrent_bytes, extras.delete_markers) == (3, 850, 1)

    assert main(["--bucket", "versioned", "--depth", "1", "--versions", "--multipart"]) == 0
    out = capsys.readouterr().out
    assert "800 B        1  100.0%  doc/" in out
    assert "Noncurrent versions: 850 B in 3 versions; 1 delete marker" in out
    assert "Incomplete multipart uploads: 0 B in 0 parts across 0 uploads" in out


@mock_aws
def test_cli_listing_table_json_and_csv(capsys):
    fill(boto3.client("s3"))
    assert main(["--bucket", "acme-data", "--depth", "1", "--by-class", "--top", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "s3://acme-data/  (source: object listing)"
    assert out[2].split() == ["49.3", "KiB", "2", "87.8%", "images/"]
    assert out[3].split() == ["48.8", "KiB", "1", "STANDARD_IA"]
    assert out[5].split() == ["6.9", "KiB", "5", "12.2%", "(2", "more", "not", "shown)"]
    assert out[6].split() == ["56.2", "KiB", "7", "TOTAL"]

    assert main(["-b", "acme-data", "-d", "1", "-p", "logs/", "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "s3://acme-data/logs/" and data["total_bytes"] == 7010
    assert data["rows"][0] == {
        "name": "logs/2023/", "bytes": 4000, "objects": 1,
        "storage_classes": {"GLACIER": {"bytes": 4000, "objects": 1}},
    }

    assert main(["-b", "acme-data", "-d", "1", "-f", "csv", "--by-class"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "name,storage_class,bytes,objects",
        "images/,STANDARD,500,1",
        "images/,STANDARD_IA,50000,1",
        "logs/,GLACIER,4000,1",
        "logs/,STANDARD,3010,3",
        ",STANDARD,7,1",
    ]


def test_from_file_listing_and_versions(tmp_path, capsys):
    saved = tmp_path / "listing.json"
    saved.write_text(json.dumps({"Contents": [
        {"Key": "a/1", "Size": 100, "StorageClass": "STANDARD"},
        {"Key": "a/2", "Size": 200, "StorageClass": "GLACIER"},
        {"Key": "b/1", "Size": 50},
    ]}))
    assert main(["--from-file", str(saved), "--format", "csv"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == ["a/,ALL,300,2", "b/,ALL,50,1"]

    versions = tmp_path / "versions.json"
    versions.write_text(json.dumps({
        "Versions": [
            {"Key": "a/1", "Size": 100, "IsLatest": True, "StorageClass": "STANDARD"},
            {"Key": "a/1", "Size": 90, "IsLatest": False, "StorageClass": "STANDARD"},
            {"Key": "z/9", "Size": 5, "IsLatest": False, "StorageClass": "STANDARD"},
        ],
        "DeleteMarkers": [{"Key": "z/9", "IsLatest": True}],
    }))
    assert main(["--from-file", str(versions), "--format", "json", "--prefix", "a/"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_bytes"] == 100
    assert data["noncurrent"] == {"versions": 1, "bytes": 90, "delete_markers": 0}


def test_from_file_errors(tmp_path, capsys):
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"Buckets": []}')
    assert main(["--from-file", str(wrong)]) == 1
    assert "expected JSON with a Contents list" in capsys.readouterr().err
    assert main(["--from-file", str(tmp_path / "missing.json")]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--depth", "2"],
        ["--bucket", "b", "--depth", "0"],
        ["--bucket", "b", "--prefix", "logs/"],
        ["--versions"],
        ["--from-file", "x.json", "--bucket", "b"],
        ["--bucket", "b", "--top", "0"],
    ],
)
def test_invalid_argument_combinations(argv):
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2
