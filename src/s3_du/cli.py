"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, listing, metrics, output
from .model import Extras, Report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3-du",
        description=(
            "Show how much storage S3 buckets and prefixes use. Without --depth, sizes come "
            "from CloudWatch storage metrics (fast, about a day old). With --depth, the "
            "bucket is listed and sizes are exact."
        ),
    )
    parser.add_argument("--bucket", "-b", help="bucket to inspect (default: all buckets)")
    parser.add_argument("--depth", "-d", type=int, help="list objects and group them this many folders deep")
    parser.add_argument("--prefix", "-p", default="", help="only look under this key prefix (listing modes)")
    parser.add_argument("--by-class", action="store_true", help="split each row by storage class")
    parser.add_argument("--multipart", action="store_true", help="also report incomplete multipart uploads")
    parser.add_argument("--versions", action="store_true", help="also report noncurrent versions and delete markers")
    parser.add_argument("--format", "-f", choices=["table", "json", "csv"], default="table")
    parser.add_argument("--top", type=int, metavar="N", help="show only the N largest rows")
    parser.add_argument("--workers", type=int, default=8, help="parallel listing threads (default: 8)")
    parser.add_argument("--from-file", metavar="JSON", help="read saved `aws s3api list-objects-v2` output instead of AWS")
    parser.add_argument("--profile", help="AWS profile to use")
    parser.add_argument("--region", help="region for the S3 client (bucket regions are detected automatically)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def build_report(args, session, now: datetime) -> Report:
    if args.from_file:
        return listing.from_file(args.from_file, args.prefix, args.depth or 1)
    if args.depth is None:
        report = metrics.collect(session, now, args.bucket)
        report.notes.append(
            "CloudWatch sizes are daily values and include noncurrent versions and incomplete multipart uploads."
        )
    else:
        s3 = session.client("s3")
        report = Report(source="listing", scope=f"s3://{args.bucket}/{args.prefix}")
        report.rows = listing.list_prefixes(s3, args.bucket, args.prefix, args.depth, args.workers)
        report.notes.append("Listing counts current objects only.")
    if args.multipart or args.versions:
        s3 = session.client("s3")
        report.extras = Extras()
        if args.multipart:
            listing.multipart_usage(s3, args.bucket, args.prefix, report.extras)
        if args.versions:
            listing.version_usage(s3, args.bucket, args.prefix, report.extras)
    return report


def main(argv: Optional[Sequence[str]] = None, *, session=None, now: Optional[datetime] = None) -> int:
    """Run the tool. `session` (a boto3.Session) and `now` exist so tests can replace them."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.depth is not None and args.depth < 1:
        parser.error("--depth must be 1 or more")
    if args.top is not None and args.top < 1:
        parser.error("--top must be 1 or more")
    if args.from_file:
        if args.bucket or args.multipart or args.versions:
            parser.error("--from-file cannot be combined with --bucket, --multipart or --versions")
    elif not args.bucket and (args.depth is not None or args.multipart or args.versions or args.prefix):
        parser.error("--depth, --prefix, --multipart and --versions need --bucket")
    elif args.prefix and args.depth is None and not (args.multipart or args.versions):
        parser.error("--prefix needs --depth (CloudWatch metrics are per bucket, not per prefix)")

    try:
        if session is None and not args.from_file:
            session = boto3.Session(profile_name=args.profile, region_name=args.region)
        report = build_report(args, session, now or datetime.now(timezone.utc))
    except (BotoCoreError, ClientError, OSError, ValueError, KeyError) as exc:
        print(f"s3-du: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        sys.stdout.write(output.render_json(report, args.top))
    elif args.format == "csv":
        sys.stdout.write(output.render_csv(report, args.by_class, args.top))
    else:
        sys.stdout.write(output.render_table(report, args.by_class, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
