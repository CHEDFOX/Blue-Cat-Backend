"""Per-job authority registry.

Every provision or judgment paragraph a tool returns is registered under a stable id (A1, A2, ...)
together with the exact record that was retrieved.  The model may only cite these ids; the verifier
later re-reads each cited record from the database and compares it with what was registered here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["provision", "judgment_paragraph"]


@dataclass
class AuthorityRecord:
    kind: Kind
    # provision_versions.id for provisions, judgment_paragraphs.id for judgment paragraphs.
    target_id: str
    uri: str
    citation: str
    jurisdiction: str
    text: str
    text_sha256: str
    valid_from: str | None
    valid_to: str | None
    authority_label: str
    limitations: list[dict[str, str]]
    source: dict[str, Any]
    language: str | None = None
    id: str = ""


@dataclass
class AuthorityRegistry:
    _records: dict[str, AuthorityRecord] = field(default_factory=dict)
    _by_target: dict[tuple[str, str], str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, record: AuthorityRecord) -> AuthorityRecord:
        """Add ``record`` (or return the already registered record for the same stored text)."""
        with self._lock:
            key = (record.kind, record.target_id)
            existing = self._by_target.get(key)
            if existing is not None:
                return self._records[existing]
            record.id = f"A{len(self._records) + 1}"
            self._records[record.id] = record
            self._by_target[key] = record.id
            return record

    def get(self, authority_id: str) -> AuthorityRecord | None:
        return self._records.get(authority_id)

    def all(self) -> list[AuthorityRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, authority_id: object) -> bool:
        return authority_id in self._records
