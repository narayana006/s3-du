"""Plain data structures shared by the collectors and the output code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Row:
    """One line of the report: a bucket or a prefix."""

    name: str
    # storage class -> [bytes, objects]
    classes: dict[str, list[int]] = field(default_factory=dict)
    objects_override: Optional[int] = None  # CloudWatch reports one object count per bucket
    objects_known: bool = True
    note: str = ""

    def add(self, storage_class: str, size: int, count: int = 1) -> None:
        entry = self.classes.setdefault(storage_class, [0, 0])
        entry[0] += size
        entry[1] += count

    def merge(self, other: "Row") -> None:
        for storage_class, (size, count) in other.classes.items():
            self.add(storage_class, size, count)

    @property
    def size(self) -> int:
        return sum(entry[0] for entry in self.classes.values())

    @property
    def objects(self) -> Optional[int]:
        if not self.objects_known:
            return None
        if self.objects_override is not None:
            return self.objects_override
        return sum(entry[1] for entry in self.classes.values())


@dataclass
class Extras:
    """Storage that a plain listing of current objects does not show."""

    multipart_uploads: Optional[int] = None
    multipart_parts: int = 0
    multipart_bytes: int = 0
    multipart_oldest: Optional[str] = None
    noncurrent_versions: Optional[int] = None
    noncurrent_bytes: int = 0
    delete_markers: int = 0


@dataclass
class Report:
    source: str  # "cloudwatch", "listing" or "file"
    scope: str  # what the rows describe, e.g. "all buckets" or "s3://bucket/prefix"
    rows: list[Row] = field(default_factory=list)
    extras: Optional[Extras] = None
    notes: list[str] = field(default_factory=list)

    def sorted_rows(self) -> list[Row]:
        return sorted(self.rows, key=lambda row: (-row.size, row.name))
