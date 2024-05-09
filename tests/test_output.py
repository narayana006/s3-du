from s3_du.model import Extras, Report, Row
from s3_du.output import human_size, render_csv, render_table, to_dict


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1023) == "1023 B"
    assert human_size(1024) == "1.0 KiB"
    assert human_size(1536 * 1024) == "1.5 MiB"
    assert human_size(5 * 1024**4) == "5.0 TiB"


def cloudwatch_report():
    big = Row("big", objects_override=10)
    big.add("STANDARD", 3 * 1024**3, 0)
    big.add("GLACIER", 1024**3, 0)
    unknown = Row("uncounted", objects_known=False)
    unknown.add("STANDARD", 1024, 0)
    return Report(source="cloudwatch", scope="all buckets", rows=[unknown, big])


def test_cloudwatch_table_hides_per_class_counts_and_unknown_totals():
    lines = render_table(cloudwatch_report(), by_class=True).splitlines()
    assert lines[1].split() == ["SIZE", "OBJECTS", "SHARE", "BUCKET"]
    assert lines[2].split() == ["4.0", "GiB", "10", "100.0%", "big"]
    assert lines[3].split() == ["3.0", "GiB", "STANDARD"]
    assert lines[4].split() == ["1.0", "GiB", "GLACIER"]
    assert lines[5].split() == ["1.0", "KiB", "-", "0.0%", "uncounted"]
    assert lines[-1].split() == ["4.0", "GiB", "-", "TOTAL"]


def test_json_and_csv_do_not_invent_per_class_counts():
    report = cloudwatch_report()
    data = to_dict(report)
    assert data["rows"][0]["storage_classes"] == {"GLACIER": {"bytes": 1024**3}, "STANDARD": {"bytes": 3 * 1024**3}}
    assert data["rows"][1]["objects"] is None
    assert render_csv(report).splitlines() == [
        "name,storage_class,bytes,objects", f"big,ALL,{4 * 1024**3},10", "uncounted,ALL,1024,",
    ]
    assert render_csv(report, by_class=True).splitlines()[1] == f"big,GLACIER,{1024**3},"


def test_empty_report_and_extras_lines():
    report = Report(source="listing", scope="s3://b/", extras=Extras(
        multipart_uploads=2, multipart_parts=5, multipart_bytes=10 * 1024**2,
        multipart_oldest="2029-12-25T08:00:00+00:00",
    ))
    text = render_table(report)
    assert "0 B        0         TOTAL" in text
    assert "Incomplete multipart uploads: 10.0 MiB in 5 parts across 2 uploads, oldest started 2029-12-25" in text
    assert "Noncurrent" not in text
