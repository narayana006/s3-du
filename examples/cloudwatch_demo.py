"""Show the CloudWatch mode of s3-du without an AWS account.

The real s3-du code runs, but its S3 and CloudWatch clients are wrapped in
botocore Stubbers that answer from examples/cloudwatch-sample.json (made-up
numbers). No network request is made and no credentials are needed.

    python examples/cloudwatch_demo.py              # same as: s3-du
    python examples/cloudwatch_demo.py --by-class   # same as: s3-du --by-class
"""

import json
import sys
from pathlib import Path

import boto3
from botocore.stub import Stubber

from s3_du.cli import main

SAMPLE = json.loads((Path(__file__).parent / "cloudwatch-sample.json").read_text())["buckets"]


class SampleSession:
    """Looks like a boto3.Session to s3-du; answers come from the sample file."""

    def client(self, service, region_name=None):
        client = boto3.client(
            service, region_name=region_name or "us-east-1",
            aws_access_key_id="testing", aws_secret_access_key="testing",
        )
        stub = Stubber(client)
        if service == "s3":
            stub.add_response(
                "list_buckets",
                {"Buckets": [{"Name": name, "BucketRegion": info["region"]} for name, info in SAMPLE.items()]},
            )
        else:
            local = {name: info for name, info in SAMPLE.items() if info["region"] == client.meta.region_name}
            series = sorted((name, kind) for name, info in local.items() for kind in info["bytes_by_storage_type"])
            stub.add_response("list_metrics", {"Metrics": [
                {
                    "Namespace": "AWS/S3",
                    "MetricName": "BucketSizeBytes",
                    "Dimensions": [{"Name": "StorageType", "Value": kind}, {"Name": "BucketName", "Value": name}],
                }
                for name, kind in series
            ]})
            values = [float(local[name]["bytes_by_storage_type"][kind]) for name, kind in series]
            values += [float(local[name]["objects"]) for name in sorted({name for name, _ in series})]
            stub.add_response("get_metric_data", {"MetricDataResults": [
                {"Id": f"m{index}", "Values": [value], "StatusCode": "Complete"} for index, value in enumerate(values)
            ]})
        stub.activate()
        return client


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], session=SampleSession()))
