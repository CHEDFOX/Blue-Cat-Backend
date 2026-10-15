"""Polite fetcher for individual instruments of the OECD Compendium of Legal Instruments
(https://legalinstruments.oecd.org).

What the OECD states, read on 2026-10-15 (see config/sources/int-oecd-legal-instruments.yaml):

* the Compendium's robots.txt is "User-agent: * / Disallow:" (nothing is disallowed);
* every instrument's PDF carries: "This document is provided free of charge. It may be reproduced and distributed free
  of charge
  without requiring any further permissions, as long as it is not altered in any way. It may not be sold.";
* the Compendium's web page says: "The present Compendium of OECD Legal Instruments is owned by the OECD. It represents
  significant time and resource investment and shall not be reproduced in whole or significant part without express
  permission
  from the OECD. For use, reproduction and translation of individual Legal Instruments, please consult the Terms and
  Conditions." (those terms, www.oecd.org/termsandconditions/, answered HTTP 403 and could not be read).

So ULIS takes individual instruments only, a short curated selection, never the Compendium in whole or in significant
part:
``load_manifest`` refuses a manifest of more than ``MAX_INSTRUMENTS`` instruments (about 5% of the 504 the Compendium
lists).
The connector reads no PDF; for every instrument in the manifest it fetches

* ``/api/instruments/<id>``: the instrument's record (JSON: key ``OECD/LEGAL/nnnn``, titles in English and French, dates
  of
  adoption, entry into force, last update and abrogation, the change history, and the address of the text), and
* the text in each language the manifest asks for (``bodyText.ref`` of the record: an HTML file the OECD exports from
  Word),

and yields one bundle (JSON) per instrument and language, holding the record and the HTML exactly as received. The
numeric ids
come from the Compendium's own index (``/api/instruments/keys?lang=en``), see ``scripts/build_oecd_manifest.py``.

Manifest (``config/manifests/int-oecd-legal-instruments.yaml``)::

    schema_version: 1
    source_id: int-oecd-legal-instruments
    host: legalinstruments.oecd.org
    user_agent: "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    crawl_delay_seconds: 5
    languages: [en, fr]
    instruments:
      - {id: 187, key: OECD/LEGAL/0162, title: "Recommendation of the Council concerning ..."}
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.constitution_pages_b import RobotsPolicy, RobotsRefusal, _is_robots_file
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "legalinstruments.oecd.org"
BUNDLE_SCHEMA = "ulis-oecd-legal-instrument-bundle/1"
MIN_DELAY = 5.0
# The Compendium lists 504 instruments (2026-10-15) and says it "shall not be reproduced in whole or significant part
# without
# express permission": ULIS takes a short selection of individual instruments, never more than about 5% of it.
MAX_INSTRUMENTS = 25
KEY = re.compile(r"OECD/LEGAL/\d{4}")
TEXT_URI = re.compile(r"/public/doc/\d+/[A-Za-z0-9._-]+\.html?")
LANGUAGES = frozenset({"en", "fr"})


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_manifest(path: Path, expected_source_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ManifestError("the manifest is not a mapping")
    if manifest.get("schema_version") != 1 or manifest.get("source_id") != expected_source_id:
        raise ManifestError("manifest schema or source_id mismatch")
    if manifest.get("host") != HOST:
        raise ManifestError(f"host must be {HOST}")
    if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
        raise ManifestError("an identifying user_agent with contact details is required")
    if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
        raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
    languages = manifest.get("languages")
    if not isinstance(languages, list) or not languages or not set(languages) <= LANGUAGES:
        raise ManifestError(f"languages must be a non-empty subset of {sorted(LANGUAGES)}")
    instruments = manifest.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        raise ManifestError("the manifest lists no instruments")
    if len(instruments) > MAX_INSTRUMENTS:
        raise ManifestError(
            f"{len(instruments)} instruments listed; at most {MAX_INSTRUMENTS} are taken, because the Compendium says "
            "it shall not be reproduced in whole or significant part without express permission from the OECD"
        )
    seen_ids: set[int] = set()
    seen_keys: set[str] = set()
    for entry in instruments:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), int) or not 0 < entry["id"] < 100000:
            raise ManifestError(f"invalid instrument entry {entry!r}")
        if not KEY.fullmatch(str(entry.get("key", ""))):
            raise ManifestError(f"invalid key {entry.get('key')!r} (expected OECD/LEGAL/nnnn)")
        if entry["id"] in seen_ids or entry["key"] in seen_keys:
            raise ManifestError(f"{entry['key']} is listed twice")
        seen_ids.add(entry["id"])
        seen_keys.add(entry["key"])
    return manifest


class OecdLegalInstrumentsConnector:
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
        self.fetch_errors: list[str] = []
        self.requests_made = 0

    def _client(self, manifest: dict[str, Any]) -> tuple[PoliteHttp, RobotsPolicy]:
        client = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        url = f"https://{HOST}/robots.txt"
        try:
            got = client.get(url)
        except (FetchError, OSError, HTTPException) as exc:
            raise RobotsRefusal(f"{url}: {exc}; the rules could not be read, so nothing is fetched") from exc
        policy = (
            RobotsPolicy("")
            if got is None or not _is_robots_file(got[0])
            else RobotsPolicy(got[0].decode("utf-8", "replace"))
        )
        if policy.closes_site:
            raise RobotsRefusal(f"{url} disallows this crawler; nothing is fetched")
        if policy.crawl_delay and policy.crawl_delay > client.crawl_delay:
            client.crawl_delay = policy.crawl_delay
        return client, policy

    def fetch(self) -> Iterator[RawDocument]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        self.fetch_errors = []
        try:
            client, policy = self._client(manifest)
        except RobotsRefusal as exc:
            self.fetch_errors.append(str(exc))
            return
        for entry in manifest["instruments"]:
            key, ident = str(entry["key"]), int(entry["id"])
            api_path = f"/api/instruments/{ident}"
            if not policy.allowed(api_path):
                self.fetch_errors.append(f"{key}: {api_path} is disallowed by robots.txt; not fetched")
                continue
            try:
                got = client.get(f"https://{HOST}{api_path}", cache_key=f"api/instruments/{ident}.json")
            except (OSError, HTTPException) as exc:
                self.fetch_errors.append(f"{key}: {exc}")
                continue
            self.requests_made = client.requests_made
            if got is None:
                self.fetch_errors.append(f"{key}: {api_path} not found (404)")
                continue
            try:
                record = json.loads(got[0])
            except ValueError:
                self.fetch_errors.append(f"{key}: {api_path} is not JSON")
                continue
            if not isinstance(record, dict) or record.get("id") != ident or record.get("key") != key:
                self.fetch_errors.append(
                    f"{key}: the record at {api_path} is {record.get('key') if isinstance(record, dict) else None!r}"
                )
                continue
            # the record names the staff member who last published the instrument (name, e-mail): personal data that the
            # law does not need, so it is not kept; nothing else of the record is changed
            if isinstance(record.get("statusSummary"), dict):
                record["statusSummary"].pop("lastPublishedBy", None)
            refs = (record.get("bodyText") or {}).get("ref") or []
            for lang in manifest["languages"]:
                ref = next((r for r in refs if r.get("lang") == lang and r.get("format") == "html"), None)
                if ref is None or not TEXT_URI.fullmatch(str(ref.get("uri", ""))):
                    self.fetch_errors.append(f"{key}: no HTML text in {lang}")
                    continue
                path = str(ref["uri"])
                if not policy.allowed(path):
                    self.fetch_errors.append(f"{key}: {path} is disallowed by robots.txt; not fetched")
                    continue
                try:
                    text = client.get(f"https://{HOST}{path}", cache_key=f"doc/{ident}.{lang}.html")
                except (OSError, HTTPException) as exc:
                    self.fetch_errors.append(f"{key}: {exc}")
                    continue
                self.requests_made = client.requests_made
                if text is None or not text[0].strip():
                    self.fetch_errors.append(f"{key}: the {lang} text {path} is missing or empty")
                    continue
                data, meta = text
                retrieved = datetime.fromisoformat(meta["retrieved_at"])
                yield RawDocument(
                    external_id=f"{key}/{lang}",
                    uri=f"https://{HOST}{path}",
                    media_type="application/json",
                    content=bundle_bytes(
                        {
                            "schema": BUNDLE_SCHEMA,
                            "key": key,
                            "id": ident,
                            "language": lang,
                            "url": f"https://{HOST}{path}",
                            "record_url": f"https://{HOST}{api_path}",
                            "retrieved_on": retrieved.date().isoformat(),
                            "content_type": meta.get("content_type"),
                            "record": record,
                            "html_b64": base64.b64encode(data).decode("ascii"),
                        }
                    ),
                    retrieved_at=retrieved,
                    metadata={"key": key, "language": lang, "cached_fetch": meta},
                )
