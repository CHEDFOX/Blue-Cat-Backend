"""Fetches the United States Reports (Supreme Court of the United States) from GovInfo, one volume at a time.

For each volume in the manifest (``config/manifests/us-govinfo-usreports.yaml``) it requests two files from the keyless
routes of www.govinfo.gov: the package metadata ``/metadata/pkg/USREPORTS-<n>/mods.xml`` (title, citation, decision
date and first page of every case) and the volume's "Text rendition" that this MODS links
(``/content/pkg/USREPORTS-<n>/text/USREPORTS-<n>.txt``). Both are yielded together, exactly as received, as one JSON
bundle (``ulis-us-govinfo-usreports/1``); the parser cuts the cases out of the text.

Access rules (see ``config/sources/us-govinfo-usreports.yaml``): ``robots.txt`` is read first and every URL is checked
against it; sequential requests at least 5 s apart; identifying User-Agent; refusals stop the run; a missing volume
(404) or one without a text rendition is skipped and reported (``fetch_errors``), not fatal; cache under
``<cache_dir>/www.govinfo.gov/`` so runs can be repeated offline. The JSON API (api key) is not used.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govinfo import BASE_URL, GovInfoClient, load_manifest
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.parsers.govinfo_usreports import BUNDLE_SCHEMA, bundle_bytes

MODS = "{http://www.loc.gov/mods/v3}"

__all__ = ["FetchError", "UsReportsGovInfoConnector", "mods_url", "text_url"]


def mods_url(volume: int) -> str:
    return f"{BASE_URL}/metadata/pkg/USREPORTS-{volume}/mods.xml"


def text_url(volume: int) -> str:
    return f"{BASE_URL}/content/pkg/USREPORTS-{volume}/text/USREPORTS-{volume}.txt"


class UsReportsGovInfoConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        # Volumes skipped on purpose (404, no text rendition, an answer that is not text): reported as run errors by the
        # pipeline so one bad entry among hundreds does not abort the source. A refusal still stops the run.
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        volumes = manifest.get("volumes") or []
        if not volumes:
            raise ManifestError("the manifest lists no volumes")
        seen: set[int] = set()
        for entry in volumes:
            volume = entry.get("volume")
            if not isinstance(volume, int) or not 1 <= volume <= 999 or volume in seen:
                raise ManifestError(f"invalid or repeated volume {volume!r}")
            seen.add(volume)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        client = GovInfoClient(manifest, self.cache_dir, offline=self.offline, sleep=self._sleep)
        for entry in manifest["volumes"]:
            volume = entry["volume"]
            tag = f"govinfo:USREPORTS-{volume}"
            got = client.get(mods_url(volume), f"metadata/USREPORTS-{volume}.mods.xml")
            if got is None:
                self.fetch_errors.append(f"{tag}: {mods_url(volume)}: not found (404)")
                continue
            mods, mods_meta = got
            problem = self._check_mods(volume, mods, mods_meta)
            if problem:
                self.fetch_errors.append(f"{tag}: {problem}")
                continue
            got = client.get(text_url(volume), f"content/USREPORTS-{volume}.txt")
            if got is None:
                self.fetch_errors.append(f"{tag}: {text_url(volume)}: not found (404)")
                continue
            text, text_meta = got
            if "text/plain" not in str(text_meta.get("content_type") or "").lower() or not text.strip():
                self.fetch_errors.append(
                    f"{tag}: {text_url(volume)}: expected text/plain, got "
                    f"{text_meta.get('content_type')!r}; not ingested"
                )
                continue
            try:
                decoded = text.decode("utf-8")
            except UnicodeDecodeError:
                self.fetch_errors.append(f"{tag}: {text_url(volume)}: not UTF-8 text; not ingested")
                continue
            yield RawDocument(
                external_id=tag,
                uri=text_url(volume),
                media_type="application/json",
                content=bundle_bytes(
                    {
                        "schema": BUNDLE_SCHEMA,
                        "volume": volume,
                        "mods_url": mods_url(volume),
                        "mods": mods.decode("utf-8"),
                        "text_url": text_url(volume),
                        "text": decoded,
                    }
                ),
                retrieved_at=datetime.fromisoformat(text_meta["retrieved_at"]),
                metadata={"volume": volume, "mods_fetch": mods_meta, "text_fetch": text_meta},
            )

    @staticmethod
    def _check_mods(volume: int, mods: bytes, meta: dict[str, Any]) -> str | None:
        """The metadata must be the package MODS of exactly this volume and must link the text rendition."""
        if "xml" not in str(meta.get("content_type") or "").lower():
            return f"{mods_url(volume)}: expected XML, got {meta.get('content_type')!r}; not ingested"
        try:
            root = fromstring(mods, forbid_dtd=True)
        except Exception as exc:  # defusedxml raises several exception types
            return f"{mods_url(volume)}: not well-formed XML ({exc}); not ingested"
        ids = {e.text for e in root.iter(f"{MODS}accessId")}
        if root.tag != f"{MODS}mods" or f"USREPORTS-{volume}" not in ids:
            return f"{mods_url(volume)}: the response is not the package MODS of USREPORTS-{volume}; not ingested"
        links = [
            (u.text or "").strip()
            for u in root.iter(f"{MODS}url")
            if u.get("displayLabel") == "Text rendition" and re.fullmatch(r"https://[^ ]+", (u.text or "").strip())
        ]
        if text_url(volume) not in links:
            return f"{mods_url(volume)}: the package links no text rendition at {text_url(volume)}; not ingested"
        return None
