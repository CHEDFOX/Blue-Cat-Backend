"""Connector protocol: a connector only *retrieves* raw documents; it never interprets them."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RawDocument:
    external_id: str
    uri: str | None
    media_type: str
    content: bytes
    retrieved_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    def fetch(self) -> Iterator[RawDocument]: ...
