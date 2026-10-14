"""Fetches Italian legislation from Normattiva as Akoma Ntoso ("esporta in Akoma ntoso").

The manifest (``config/manifests/it-normattiva.yaml``) lists, for each act, its Gazzetta Ufficiale
date, its redactional code, its URN and the dates for which the text in force is wanted. For each
date the connector requests the export Normattiva offers on every act page::

    https://www.normattiva.it/do/atto/caricaAKN?dataGU=YYYYMMDD&codiceRedaz=<code>&dataVigenza=YYYYMMDD

The server answers with the act as it applies on that date, labelled with the date of the version
that applies (``eli/id/.../CONSOLIDATED/<YYYYMMDD>``) or ``ORIGINAL`` for the text as first
published. Several requested dates may fall in the same version; each version is yielded once.

Access rules (see ``config/sources/it-normattiva.yaml``):

* robots.txt of www.normattiva.it disallows only ``/ricerca/`` and ``/mfr/ricerca/``; neither is
  used. It gives no Crawl-delay: PoliteHttp waits at least 5 s between requests;
* the export is served only inside the session that the act's own page starts (as when a person
  follows the "esporta in Akoma ntoso" link), so the connector first requests the act page by its
  URN (the documented way to link to an act), keeps the session cookie and sends the page as
  Referer. The page is read for the cookie only and is not kept;
* a response that is not an Akoma Ntoso document is an error and is not cached; any refusal
  (403, 409, a challenge page ...) stops the run: no other identity, no workaround;
* identifying User-Agent, back-off on 429/5xx, cache under ``var/sources/www.normattiva.it/``,
  offline mode for repeatable runs.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.normattiva.it"
BASE = f"https://{HOST}"
_CODE = re.compile(r"^\d{3}[A-Z]\d{4}$")
_URN = re.compile(r"^urn:nir:stato:[a-z.]+(?::\d{4}-\d{2}-\d{2}(?:;\d+)?)?$")
_LABEL = re.compile(rb"eli/id/\d{4}/\d{2}/\d{2}/\d{3}[A-Z]\d{4}/(ORIGINAL|CONSOLIDATED/(\d{8}))")

__all__ = ["FetchError", "NormattivaConnector", "akn_url", "page_url", "version_label"]


def page_url(urn: str) -> str:
    return f"{BASE}/uri-res/N2Ls?{urn}"


def akn_url(gazzetta: date, code: str, vigenza: date) -> str:
    return f"{BASE}/do/atto/caricaAKN?dataGU={gazzetta:%Y%m%d}&codiceRedaz={code}&dataVigenza={vigenza:%Y%m%d}"


def version_label(content: bytes) -> str | None:
    """``ORIGINAL`` or ``YYYYMMDD`` of the version an export contains, from its ELI; None if absent."""
    m = _LABEL.search(content[:20000])
    if not m:
        return None
    return "ORIGINAL" if m.group(1) == b"ORIGINAL" else m.group(2).decode()


class NormattivaConnector:
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
        self.http: PoliteHttp | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", 5)) < 5:
            raise ManifestError("crawl_delay_seconds must be at least 5")
        seen: set[str] = set()
        for work in manifest["works"]:
            if not _CODE.match(str(work.get("code", ""))) or work["code"] in seen:
                raise ManifestError(f"invalid or repeated work code {work.get('code')!r}")
            seen.add(work["code"])
            if not _URN.match(str(work.get("urn", ""))):
                raise ManifestError(f"{work['code']}: invalid URN {work.get('urn')!r}")
            date.fromisoformat(str(work["gazzetta_date"]))
            dates = [date.fromisoformat(str(d)) for d in work.get("vigenza", [])]
            if not dates or dates != sorted(set(dates)):
                raise ManifestError(f"{work['code']}: vigenza dates must be listed once each, oldest first")
        return manifest

    def _start_session(self, http: PoliteHttp, work: dict[str, Any]) -> None:
        """Request the act's page so that the server accepts the export (cookie kept by ``http``)."""
        url = page_url(work["urn"])
        cached = http.cache_path(url)
        for path in (cached, cached.with_name(cached.name + ".meta.json")):
            path.unlink(missing_ok=True)  # always a fresh session; the page itself is not kept
        if http.get(url) is None:
            raise FetchError(f"{url}: not found")
        cached.unlink(missing_ok=True)
        cached.with_name(cached.name + ".meta.json").unlink(missing_ok=True)

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        http = self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
            cookies=True,
        )
        for work in manifest["works"]:
            gazzetta = date.fromisoformat(str(work["gazzetta_date"]))
            wanted = [date.fromisoformat(str(d)) for d in work["vigenza"]]
            urls = {d: akn_url(gazzetta, work["code"], d) for d in wanted}
            if not self.offline and any(not http.cache_path(u).exists() for u in urls.values()):
                self._start_session(http, work)
            seen: list[str] = []
            for vigenza in wanted:
                url = urls[vigenza]
                for attempt in (1, 2):
                    got = http.get(url, headers={"Referer": page_url(work["urn"])})
                    if got is None:
                        raise FetchError(f"{url}: not found")
                    content, meta = got
                    label = version_label(content)
                    if content.lstrip().startswith(b"<?xml") and label is not None:
                        break
                    cached = http.cache_path(url)  # never keep an error page
                    cached.unlink(missing_ok=True)
                    cached.with_name(cached.name + ".meta.json").unlink(missing_ok=True)
                    if attempt == 2 or self.offline:
                        raise FetchError(f"{url}: not an Akoma Ntoso export (the server did not accept the session)")
                    self._start_session(http, work)  # the session may have expired: start it once more
                assert label is not None
                if label in seen:
                    continue  # the same version again
                if seen and label != "ORIGINAL" and seen[-1] != "ORIGINAL" and label < seen[-1]:
                    raise FetchError(f"{url}: version {label} is older than {seen[-1]}")
                seen.append(label)
                yield RawDocument(
                    external_id=f"normattiva:{work['code']}@{label}",
                    uri=url,
                    media_type="application/akn+xml",
                    content=content,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={
                        "codice_redazionale": work["code"],
                        "urn": work["urn"],
                        "gazzetta_date": str(work["gazzetta_date"]),
                        "requested_vigenza": vigenza.isoformat(),
                        "version": label,
                        "cached_fetch": meta,
                    },
                )
