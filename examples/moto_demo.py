"""Run the listing mode of s3-du against moto, a local fake of S3.

Creates a versioned bucket with a few hundred small objects, an old version of
some of them and one abandoned multipart upload, then runs s3-du on it. No
real AWS account is contacted.

    pip install "moto[s3]"
    python examples/moto_demo.py                                  # default arguments below
    python examples/moto_demo.py --bucket acme-demo --depth 1 --format json
"""

import os
import sys

import boto3
from moto import mock_aws

from s3_du.cli import main

DEFAULT_ARGS = ["--bucket", "acme-demo", "--depth", "2", "--by-class", "--multipart", "--versions"]


def populate(s3) -> None:
    s3.create_bucket(Bucket="acme-demo")
    s3.put_bucket_versioning(Bucket="acme-demo", VersioningConfiguration={"Status": "Enabled"})
    for day in range(1, 31):
        for hour in (0, 6, 12, 18):
            s3.put_object(Bucket="acme-demo", Key=f"logs/2024-03/{day:02d}-{hour:02d}.json.gz", Body=b"l" * (9000 + 137 * day))
    for month in range(1, 13):
        s3.put_object(
            Bucket="acme-demo", Key=f"logs/2023-archive/{month:02d}.tar", Body=b"a" * (400_000 + 9_000 * month),
            StorageClass="GLACIER",
        )
    for index in range(40):
        s3.put_object(
            Bucket="acme-demo", Key=f"exports/daily/report-{index:02d}.parquet", Body=b"e" * 52_000,
            StorageClass="STANDARD_IA",
        )
    for index in range(40):  # overwrite half of the exports, leaving noncurrent versions behind
        if index % 2 == 0:
            s3.put_object(
                Bucket="acme-demo", Key=f"exports/daily/report-{index:02d}.parquet", Body=b"E" * 54_000,
                StorageClass="STANDARD_IA",
            )
    s3.put_object(Bucket="acme-demo", Key="exports/latest.csv", Body=b"c" * 7_300)
    s3.put_object(Bucket="acme-demo", Key="README.txt", Body=b"r" * 812)
    s3.put_object(Bucket="acme-demo", Key="tmp/scratch.bin", Body=b"t" * 1_000)
    s3.delete_object(Bucket="acme-demo", Key="tmp/scratch.bin")
    upload = s3.create_multipart_upload(Bucket="acme-demo", Key="uploads/video.mp4")["UploadId"]
    for part in (1, 2, 3):
        s3.upload_part(Bucket="acme-demo", Key="uploads/video.mp4", UploadId=upload, PartNumber=part, Body=b"v" * 600_000)


def run() -> int:
    for var in [name for name in os.environ if name.startswith("AWS_")]:
        del os.environ[var]
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_DEFAULT_REGION="us-east-1")
    with mock_aws():
        populate(boto3.client("s3"))
        return main(sys.argv[1:] or DEFAULT_ARGS)


if __name__ == "__main__":
    sys.exit(run())
