# s3-du

A `du` for Amazon S3: see how much storage each bucket and prefix uses, split by storage class, without listing every object unless you ask for that depth.

**Status: first working version (0.1.0).** All five planned features are implemented: bucket sizes from CloudWatch storage metrics, per-prefix breakdown by parallel listing, storage class split, incomplete multipart uploads and noncurrent versions reported separately, and table / JSON / CSV output. Everything is tested against [moto](https://github.com/getmoto/moto) and botocore stubs. **It has not yet been run against a real AWS account**, so treat the first real run with some care and please report surprises.

## Two ways of measuring

| | `s3-du` / `s3-du --bucket B` | `s3-du --bucket B --depth N` |
| --- | --- | --- |
| Source | CloudWatch daily storage metrics (`AWS/S3` namespace) | `ListObjectsV2` |
| Speed | A few API calls per region, however many objects there are | One request per 1,000 objects |
| Freshness | Daily value, usually a day or so old | Current |
| Granularity | Whole bucket, by storage class | Any prefix depth, by storage class |
| What is counted | Current and noncurrent versions, plus parts of incomplete multipart uploads | Current objects only |

Start with the first to find the big buckets, then use the second on the one you care about.

## Install

```bash
pip install git+https://github.com/narayana006/s3-du
```

Python 3.10 or newer. The only runtime dependency is `boto3`. Credentials are found the usual boto3 way (`--profile`, environment, config files).

## Try it without an AWS account

`--from-file` reads the JSON that `aws s3api list-objects-v2 --bucket B --output json` prints. A made-up listing is in `examples/`. From a clone of this repository:

```bash
s3-du --from-file examples/listing.json --depth 2 --by-class --top 4
```

Real output of that command (from the example file; no AWS call is made):

```text
examples/listing.json  (source: saved listing file)
     SIZE  OBJECTS  SHARE  PREFIX
628.0 GiB        3  66.7%  backups/db/
622.0 GiB        2             DEEP_ARCHIVE
  6.0 GiB        1             STANDARD_IA
306.0 GiB        8  32.5%  logs/archive/
306.0 GiB        8             GLACIER
  6.2 GiB       24   0.7%  logs/app/
  6.2 GiB       24             STANDARD
720.0 MiB       12   0.1%  exports/daily/
720.0 MiB       12             STANDARD_IA
 63.0 MiB        5   0.0%  (2 more not shown)
940.9 GiB       52         TOTAL
```

## What the live modes look like

### All buckets, from CloudWatch

```bash
s3-du
```

`examples/cloudwatch_demo.py` runs exactly this code path with the S3 and CloudWatch clients answered by botocore stubs from `examples/cloudwatch-sample.json` (hand-written numbers, no network access). Real output of `python examples/cloudwatch_demo.py --by-class --top 3`:

```text
all buckets  (source: CloudWatch daily storage metrics)
     SIZE    OBJECTS  SHARE  BUCKET
 10.0 TiB        318  52.8%  acme-backups-eu
 10.0 TiB                        DEEP_ARCHIVE
 20.0 GiB                        STANDARD
  6.0 TiB     96,412  31.6%  acme-data-lake
  6.0 TiB                        INTELLIGENT_TIERING
  3.0 TiB  1,841,220  15.6%  acme-app-logs
  2.5 TiB                        GLACIER
384.0 GiB                        STANDARD
 90.0 GiB                        STANDARD_IA
179.0 MiB      2,204   0.0%  (2 more not shown)
 19.0 TiB  1,940,154         TOTAL
note: CloudWatch sizes are daily values and include noncurrent versions and incomplete multipart uploads.
```

CloudWatch reports one object count per bucket, not per storage class, so the class lines show sizes only.

### One bucket, by prefix

```bash
s3-du --bucket acme-demo --depth 2 --by-class --multipart --versions
```

`examples/moto_demo.py` builds a small versioned bucket inside moto (a local fake of S3) and runs that command against it. Real output of `python examples/moto_demo.py` (against moto, not real AWS):

```text
s3://acme-demo/  (source: object listing)
   SIZE  OBJECTS  SHARE  PREFIX
5.2 MiB       12  61.4%  logs/2023-archive/
5.2 MiB       12             GLACIER
2.0 MiB       40  23.6%  exports/daily/
2.0 MiB       40             STANDARD_IA
1.3 MiB      120  14.9%  logs/2024-03/
1.3 MiB      120             STANDARD
7.1 KiB        1   0.1%  exports/
7.1 KiB        1             STANDARD
  812 B        1   0.0%  (objects not under a folder)
  812 B        1             STANDARD
8.5 MiB      174         TOTAL
Incomplete multipart uploads: 1.7 MiB in 3 parts across 1 upload, oldest started 2010-11-10
Noncurrent versions: 1016.6 KiB in 21 versions; 1 delete marker
note: Listing counts current objects only.
```

(The 2010 date is the fixed timestamp moto gives every multipart upload; real S3 returns the actual start time.)

How to read it:

- A row such as `exports/` next to `exports/daily/` holds the objects that sit directly in `exports/`, not in a deeper folder.
- `(objects not under a folder)` is everything directly under the bucket root, or directly under `--prefix`.
- Incomplete multipart uploads and noncurrent versions are storage you pay for that an ordinary listing never shows. They are reported on their own lines and are not included in the rows or the total.

## Usage

```text
s3-du [--bucket B] [--depth N] [--prefix P] [--by-class] [--multipart] [--versions]
      [--format table|json|csv] [--top N] [--workers N] [--from-file JSON]
      [--profile NAME] [--region NAME]
```

| Option | Meaning |
| --- | --- |
| `--bucket`, `-b` | Bucket to inspect. Without it, every bucket in the account is shown from CloudWatch. |
| `--depth`, `-d` | List objects and group them this many folders deep. Switches from CloudWatch to listing. |
| `--prefix`, `-p` | Only look under this key prefix. Needs `--depth` (or `--multipart` / `--versions`). |
| `--by-class` | Add a line per storage class under each row. |
| `--multipart` | Also add up the parts of incomplete multipart uploads. Needs `--bucket`. |
| `--versions` | Also add up noncurrent object versions and count delete markers. Needs `--bucket`. |
| `--format`, `-f` | `table` (default), `json` or `csv`. |
| `--top N` | Show only the N largest rows. Totals still cover everything. |
| `--workers N` | Threads used to list top-level prefixes in parallel (default 8). |
| `--from-file JSON` | Read saved `aws s3api list-objects-v2` or `list-object-versions` JSON instead of calling AWS. |

Rows are always sorted largest first. Sizes use binary units (1 GiB = 1024³ bytes), which is the unit S3 bills in. Exit status is `0` on success, `1` on an AWS or file error, `2` on bad arguments.

### Machine-readable output

```bash
s3-du --from-file examples/listing.json --prefix logs/ --depth 2 --format csv
```

```text
name,storage_class,bytes,objects
logs/archive/2023/,ALL,178241142784,4
logs/archive/2022/,ALL,150323855360,4
logs/app/2024/,ALL,6609174528,24
```

With `--by-class` the CSV has one line per row and storage class. `--format json` includes everything, including the multipart and noncurrent figures; the CSV contains the rows only.

### Saved listings

With a `list-object-versions` file, current versions make up the rows and noncurrent versions are summarised:

```bash
s3-du --from-file examples/versions.json
```

```text
examples/versions.json  (source: saved listing file)
     SIZE  OBJECTS  SHARE  PREFIX
700.0 MiB        1  99.9%  media/
937.7 KiB        2   0.1%  site/
700.9 MiB        3         TOTAL
Noncurrent versions: 666.9 MiB in 4 versions; 1 delete marker
```

The file must be a single JSON document, so save it with the AWS CLI's default automatic pagination (do not pass `--max-items` or `--no-paginate`, which would truncate it).

## IAM permissions

`s3-du` only reads. It never creates, changes or deletes anything. The actions it calls:

| Mode | IAM actions |
| --- | --- |
| All buckets (`s3-du`) | `s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `cloudwatch:ListMetrics`, `cloudwatch:GetMetricData` |
| One bucket (`--bucket B`) | `s3:GetBucketLocation`, `cloudwatch:ListMetrics`, `cloudwatch:GetMetricData` |
| Listing (`--depth N`) | `s3:ListBucket` |
| `--multipart` | `s3:ListBucketMultipartUploads`, `s3:ListMultipartUploadParts` |
| `--versions` | `s3:ListBucketVersions` |

A minimal policy for everything:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketVersions",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

AWS charges for the API requests themselves: CloudWatch bills `GetMetricData` by the number of metrics requested, and S3 bills list requests. CloudWatch mode requests one metric per bucket and storage type plus one per bucket; listing mode makes one list request per 1,000 objects. Check the current CloudWatch and S3 pricing pages for rates.

## Features

- [x] Bucket sizes from CloudWatch storage metrics in seconds
- [x] Per-prefix breakdown using parallel listing when you ask for it
- [x] Split by storage class
- [x] Incomplete multipart uploads and old versions reported separately
- [x] Human-readable, JSON and CSV output

## Limitations

- Not yet run against a real AWS account. The behaviour is tested against moto and stubbed responses built from the documented API shapes.
- CloudWatch numbers are a daily snapshot. A bucket created or filled in the last day or two shows `no storage metrics` or an old size; use `--depth 1` for the current figure. The tool looks back three days for the latest data point.
- CloudWatch and listing figures for the same bucket legitimately differ, because the CloudWatch size includes noncurrent versions and incomplete multipart uploads and the listing does not.
- CloudWatch storage types are rolled up into storage classes by name (for example `GlacierStorage` and `GlacierObjectOverhead` both count as `GLACIER`). A storage type the tool does not recognise is shown under its CloudWatch name.
- Parallelism is across the top-level prefixes under `--prefix`. A bucket whose objects are all under one huge prefix is listed by a single thread unless you point `--prefix` at that folder.
- Listing a very large bucket takes time and list requests in proportion to its object count; `--versions` and `--multipart` add their own listings. There is no progress display yet.
- Only general purpose buckets have been considered. Directory buckets (S3 Express One Zone) and access point ARNs are untested.
- No cost estimate in money; sizes only.
- S3 Inventory reports and S3 Storage Lens are not used as sources.

## Development

```bash
git clone https://github.com/narayana006/s3-du
cd s3-du
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run against moto and botocore's `Stubber` with dummy credentials and empty config files. They never contact AWS.

## Contributing

Issues and pull requests are welcome. The most useful report right now is a comparison of `s3-du` output with what you see in the S3 console or Storage Lens for a real bucket (with names changed).

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
