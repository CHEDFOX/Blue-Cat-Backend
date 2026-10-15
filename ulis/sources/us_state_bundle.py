"""The raw-document envelope shared by the US state-law connectors and parsers.

The state sources are plain web files (an HTML page, a zip, an XML file) that carry no edition
date of their own, while the parsers receive only the bytes of a raw document. A connector
therefore wraps the response exactly as received (byte for byte, base64 when it is not valid UTF-8)
together with the URL it came from and the time it was retrieved; the parser reads the retrieval
date as the date the publisher presented the text as current (``attested_at_edition``). The
original bytes are recoverable from the envelope (``body_bytes``) and their SHA-256 is recorded.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

BUNDLE_FORMAT = "ulis-us-state-bundle/1"


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class StateBundle:
    state: str
    url: str
    retrieved_at: datetime
    content_type: str | None
    body_bytes: bytes
    key: str

    @property
    def retrieved_on(self) -> date:
        return self.retrieved_at.astimezone(UTC).date()


def make_bundle(state: str, key: str, body: bytes, meta: dict[str, Any]) -> bytes:
    """Wrap one response. ``key`` identifies the document inside its source (chapter, code, title)."""
    try:
        text, encoding = body.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = base64.b64encode(body).decode("ascii"), "base64"
    envelope = {
        "format": BUNDLE_FORMAT,
        "state": state,
        "key": key,
        "url": meta.get("final_url") or meta["url"],
        "retrieved_at": meta["retrieved_at"],
        "content_type": meta.get("content_type"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "encoding": encoding,
        "body": text,
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


def read_bundle(content: bytes, state: str) -> StateBundle:
    try:
        envelope = json.loads(content)
    except ValueError as exc:
        raise BundleError(f"not a {state} bundle: {exc}") from exc
    if not isinstance(envelope, dict) or envelope.get("format") != BUNDLE_FORMAT:
        raise BundleError(f"unsupported bundle (expected format {BUNDLE_FORMAT!r})")
    if envelope.get("state") != state:
        raise BundleError(f"bundle is for state {envelope.get('state')!r}, not {state!r}")
    try:
        body = (
            base64.b64decode(envelope["body"], validate=True)
            if envelope["encoding"] == "base64"
            else envelope["body"].encode("utf-8")
        )
        retrieved = datetime.fromisoformat(envelope["retrieved_at"])
        url = envelope["url"]
        key = envelope["key"]
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise BundleError(f"incomplete bundle: {exc}") from exc
    if hashlib.sha256(body).hexdigest() != envelope.get("sha256"):
        raise BundleError("bundle body does not match its recorded SHA-256")
    if retrieved.tzinfo is None:
        retrieved = retrieved.replace(tzinfo=UTC)
    return StateBundle(state, url, retrieved, envelope.get("content_type"), body, str(key))
