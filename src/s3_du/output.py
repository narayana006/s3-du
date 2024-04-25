"""Render a report as a table, JSON or CSV."""

from __future__ import annotations

import csv
import io
import json
from typing import Optional

from .model import Report, Row

UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]


def human_size(size: int) -> str:
    value = float(size)
    for unit in UNITS:
        if value < 1024 or unit == UNITS[-1]:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _count(value: Optional[int]) -> str:
    return "-" if value is None else f"{value:,}"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def _display_name(report: Report, row: Row) -> str:
    if report.source == "cloudwatch":
        return row.name
    return row.name or "(objects not under a folder)"


def _visible(report: Report, top: Optional[int]) -> tuple[list[Row], list[Row]]:
    rows = report.sorted_rows()
    if top is not None and top < len(rows):
        return rows[:top], rows[top:]
    return rows, []


def render_table(report: Report, by_class: bool = False, top: Optional[int] = None) -> str:
    shown, hidden = _visible(report, top)
    total = sum(row.size for row in report.rows)
    label = "BUCKET" if report.source == "cloudwatch" else "PREFIX"
    lines = [("SIZE", "OBJECTS", "SHARE", label)]
    for row in shown:
        share = f"{100 * row.size / total:.1f}%" if total else "-"
        name = _display_name(report, row) + (f"  [{row.note}]" if row.note else "")
        lines.append((human_size(row.size), _count(row.objects), share, name))
        if by_class:
            for storage_class, (size, count) in sorted(row.classes.items(), key=lambda kv: (-kv[1][0], kv[0])):
                objects = _count(count) if row.objects_override is None else ""
                lines.append((human_size(size), objects, "", f"    {storage_class}"))
    if hidden:
        hidden_objects = [row.objects for row in hidden]
        objects = None if None in hidden_objects else sum(hidden_objects)
        hidden_size = sum(row.size for row in hidden)
        share = f"{100 * hidden_size / total:.1f}%" if total else "-"
        lines.append((human_size(hidden_size), _count(objects), share, f"({len(hidden)} more not shown)"))
    all_objects = [row.objects for row in report.rows]
    lines.append((human_size(total), _count(None if None in all_objects else sum(all_objects)), "", "TOTAL"))
    widths = [max(len(line[i]) for line in lines) for i in range(3)]
    out = [f"{report.scope}  (source: {SOURCE_LABELS[report.source]})"]
    for size, objects, share, name in lines:
        out.append(f"{size:>{widths[0]}}  {objects:>{widths[1]}}  {share:>{widths[2]}}  {name}".rstrip())
    extras = report.extras
    if extras and extras.multipart_uploads is not None:
        detail = f", oldest started {extras.multipart_oldest[:10]}" if extras.multipart_oldest else ""
        out.append(
            f"Incomplete multipart uploads: {human_size(extras.multipart_bytes)} in "
            f"{_plural(extras.multipart_parts, 'part')} across {_plural(extras.multipart_uploads, 'upload')}{detail}"
        )
    if extras and extras.noncurrent_versions is not None:
        out.append(
            f"Noncurrent versions: {human_size(extras.noncurrent_bytes)} in "
            f"{_plural(extras.noncurrent_versions, 'version')}; {_plural(extras.delete_markers, 'delete marker')}"
        )
    out += [f"note: {note}" for note in report.notes]
    return "\n".join(out) + "\n"


SOURCE_LABELS = {
    "cloudwatch": "CloudWatch daily storage metrics",
    "listing": "object listing",
    "file": "saved listing file",
}


def to_dict(report: Report, top: Optional[int] = None) -> dict:
    shown, _ = _visible(report, top)
    data = {
        "source": report.source,
        "scope": report.scope,
        "total_bytes": sum(row.size for row in report.rows),
        "rows": [
            {
                "name": row.name,
                "bytes": row.size,
                "objects": row.objects,
                "storage_classes": {
                    storage_class: (
                        {"bytes": size} if row.objects_override is not None or not row.objects_known
                        else {"bytes": size, "objects": count}
                    )
                    for storage_class, (size, count) in sorted(row.classes.items())
                },
                **({"note": row.note} if row.note else {}),
            }
            for row in shown
        ],
        "notes": report.notes,
    }
    extras = report.extras
    if extras and extras.multipart_uploads is not None:
        data["incomplete_multipart"] = {
            "uploads": extras.multipart_uploads,
            "parts": extras.multipart_parts,
            "bytes": extras.multipart_bytes,
            "oldest_initiated": extras.multipart_oldest,
        }
    if extras and extras.noncurrent_versions is not None:
        data["noncurrent"] = {
            "versions": extras.noncurrent_versions,
            "bytes": extras.noncurrent_bytes,
            "delete_markers": extras.delete_markers,
        }
    return data


def render_json(report: Report, top: Optional[int] = None) -> str:
    return json.dumps(to_dict(report, top), indent=2) + "\n"


def render_csv(report: Report, by_class: bool = False, top: Optional[int] = None) -> str:
    """One line per row, or per row and storage class with by_class. Extras are not included."""
    shown, _ = _visible(report, top)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["name", "storage_class", "bytes", "objects"])
    for row in shown:
        if by_class:
            for storage_class, (size, count) in sorted(row.classes.items()):
                per_class_known = row.objects_override is None and row.objects_known
                writer.writerow([row.name, storage_class, size, count if per_class_known else ""])
        else:
            writer.writerow([row.name, "ALL", row.size, "" if row.objects is None else row.objects])
    return buffer.getvalue()
