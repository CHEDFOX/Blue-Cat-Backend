"""Write the Federal Register of Legislation manifests from the Register's own title lists.

    python scripts/build_frl_manifest.py                      # Acts -> config/manifests/au-federal-register.yaml
    python scripts/build_frl_manifest.py --collection LegislativeInstrument
                                                              # -> config/manifests/au-federal-register-instruments.yaml
    python scripts/build_frl_manifest.py --out /tmp/m.yaml --limit 30    # a slice, for trials (cached pages are reused)

The API (https://api.prod.legislation.gov.au/v1/, no key) lists titles with their status. A title is listed when

* its status is "in force" (``isInForce``), and
* it is principal (``isPrincipal``): amending Acts and instruments stay in force as law but have no text of their own
  to fetch, because their amendments are written into the principal title's compilation.

**Acts** (``--collection Act``, the default): the latest registered version of each principal Act is checked, 40
titles per request. An Act that was amended has a registered compilation; an Act that was never amended has only its
"as made" version (compilation number 0, register id = title id), which the connector fetches instead (flagged
``as_made: true`` here for information; the connector decides from the Register's listing at run time). The EPUB
documents of the as-made titles are listed too (40 per request), so the header can say how many have none. The
Constitution and the seven Acts of the original sample keep the history settings they had (four with every registered
compilation, the others latest only); every other title is the latest registered version only. About 10 requests for
the title list, 32 for the version check and 10 for the as-made documents at the 10 s crawl delay.

**Legislative instruments** (``--collection LegislativeInstrument``): the title list only (about 24,000 titles in
force, 48 requests at 510 rows each); no version check (it would take 600 requests), since the connector lists the
latest version of 40 titles per request anyway and fetches the compilation or the text as made accordingly. The
titles themselves are fetched later by the connector, one request each.

The API's ``@odata.nextLink`` drops the ``$filter`` (verified 2026-10-15), so the pages are asked for with ``$skip``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
API_BASE = "https://api.prod.legislation.gov.au/v1"
HOST = "api.prod.legislation.gov.au"
CACHE = REPO_ROOT / "var" / "sources"
OUT = {
    "Act": REPO_ROOT / "config" / "manifests" / "au-federal-register.yaml",
    "LegislativeInstrument": REPO_ROOT / "config" / "manifests" / "au-federal-register-instruments.yaml",
}
SOURCE_ID = {"Act": "au-federal-register", "LegislativeInstrument": "au-federal-register-instruments"}
DELAY = 10.0
JSON = {"Accept": "application/json"}
# The original sample: (title id, every registered compilation?, name)
SAMPLE = [
    ("C2004Q00685", True, "Commonwealth of Australia Constitution Act"),
    ("C1901A00002", True, "Acts Interpretation Act 1901"),
    ("C2004A00553", True, "Electronic Transactions Act 1999"),
    ("C2004A02944", True, "Insurance Contracts Act 1984"),
    ("C1914A00012", False, "Crimes Act 1914"),
    ("C2004A03712", False, "Privacy Act 1988"),
    ("C2004A00275", False, "Family Law Act 1975"),
    ("C2004A04868", False, "Criminal Code Act 1995"),
]
BATCH = 40
LICENCE = """\
# Licence: CC BY 4.0 (www.legislation.gov.au/terms-of-use). Automated downloading is allowed if robots.txt's
# Crawl-delay (10 s) is respected (help page "Data share and reuse"). The help page prefers incremental crawls
# outside 08:00-20:00 Australian time (UTC+10): `avoid_hours` makes the connector wait for the end of that window.
# `batch_listings` asks the Register for the versions and documents of 40 titles per request.
"""


def _titles(http: PoliteHttp, collection: str) -> list[dict[str, Any]]:
    """Every title of the collection that is in force (the API pages at 510 rows)."""
    base = (
        f"{API_BASE}/Titles?$filter=isInForce%20eq%20true%20and%20collection%20eq%20'{collection}'"
        "&$select=id,name,year,number,isPrincipal&$orderby=id"
    )
    prefix = "acts" if collection == "Act" else "instruments"
    rows: list[dict[str, Any]] = []
    total = None
    while total is None or len(rows) < total:
        url = f"{base}&$count=true&$skip={len(rows)}"
        got = http.get(url, cache_key=f"titles/{prefix}-in-force-skip{len(rows)}.json", headers=JSON)
        if got is None:
            raise SystemExit(f"{url}: not found")
        body = json.loads(got[0])
        total = int(body["@odata.count"])
        if not body.get("value"):
            break
        rows.extend(body["value"])
        print(f"{len(rows)} of {total} titles", flush=True)
    # A title can straddle two pages (7 duplicates in 24,078 instrument rows on 2026-10-18); the first row is kept.
    unique: dict[str, dict[str, Any]] = {}
    for r in rows:
        unique.setdefault(r["id"], r)
    if len(unique) != len(rows):
        print(f"{len(rows) - len(unique)} duplicate title(s) in the listing dropped", flush=True)
    return list(unique.values())


def _in_list(ids: list[str]) -> str:
    return "(" + ",".join(f"'{t}'" for t in ids) + ")"


def _latest_versions(http: PoliteHttp, ids: list[str]) -> dict[str, dict[str, Any]]:
    url = (
        f"{API_BASE}/Versions?$filter=titleId%20in%20{_in_list(ids)}%20and%20isLatest%20eq%20true"
        "&$select=titleId,registerId,compilationNumber,start"
    )
    got = http.get(url, cache_key=f"versions/latest-{ids[0]}-{ids[-1]}-{len(ids)}.json", headers=JSON)
    if got is None:
        raise SystemExit(f"{url}: not found")
    return {v["titleId"]: v for v in json.loads(got[0])["value"]}


def _epub_titles(http: PoliteHttp, register_ids: list[str]) -> set[str]:
    """Titles among ``register_ids`` (as-made versions: register id = title id) that have an EPUB document."""
    url = (
        f"{API_BASE}/Documents?$filter=registerId%20in%20{_in_list(register_ids)}%20and%20type%20eq%20'Primary'"
        "%20and%20format%20eq%20'Epub'&$select=titleId,registerId,start"
    )
    key = f"documents/asmade-epub-{register_ids[0]}-{register_ids[-1]}-{len(register_ids)}.json"
    got = http.get(url, cache_key=key, headers=JSON)
    if got is None:
        raise SystemExit(f"{url}: not found")
    return {d["titleId"] for d in json.loads(got[0])["value"]}


def _line(row: dict[str, Any], history: bool = False, as_made: bool = False) -> str:
    title = json.dumps(row["name"], ensure_ascii=False)
    flag = ", as_made: true" if as_made else ""
    return f"  - {{title_id: {row['id']}, history: {str(history).lower()}{flag}, title: {title}}}"


def _head(source_id: str) -> str:
    return f"""schema_version: 1
source_id: {source_id}
api_base: {API_BASE}
user_agent: "{USER_AGENT}"
crawl_delay_seconds: 10
batch_listings: true
avoid_hours: {{utc_offset: 10, start: 8, end: 20}}
works:
"""


def build_acts(http: PoliteHttp, limit: int) -> tuple[str, int]:
    rows = _titles(http, "Act")
    principal = [r for r in rows if r.get("isPrincipal")]
    print(f"{len(rows)} Acts in force, {len(principal)} principal Acts")
    lines = []
    seen: set[str] = set()
    for title_id, history, name in SAMPLE:
        seen.add(title_id)
        lines.append(_line({"id": title_id, "name": name}, history))
    candidates = [r for r in principal if r["id"] not in seen]
    if limit:
        candidates = candidates[:limit]
    compiled: list[dict[str, Any]] = []
    as_made: list[dict[str, Any]] = []
    no_version: list[str] = []
    for i in range(0, len(candidates), BATCH):
        chunk = candidates[i : i + BATCH]
        latest = _latest_versions(http, [r["id"] for r in chunk])
        for r in chunk:
            v = latest.get(r["id"])
            if v is None or not v.get("registerId"):
                no_version.append(r["id"])
            elif v["registerId"] == r["id"] or v.get("compilationNumber") == "0":
                as_made.append(r)  # never amended: the text as made is the only version
            else:
                compiled.append(r)  # a registered compilation (numbered, or an unnumbered older reprint)
        print(f"checked {min(i + BATCH, len(candidates))} of {len(candidates)} principal Acts", flush=True)
    with_epub: set[str] = set()
    for i in range(0, len(as_made), BATCH):
        with_epub |= _epub_titles(http, [r["id"] for r in as_made[i : i + BATCH]])
    without_epub = [r["id"] for r in as_made if r["id"] not in with_epub]
    lines += [_line(r) for r in compiled]
    lines += [_line(r, as_made=True) for r in as_made]
    listed = len(lines)
    today = datetime.now(UTC).date().isoformat()
    in_sample = sum(1 for r in principal if r["id"] in {t for t, _, _ in SAMPLE})
    history_docs, history_listings = 109, 8  # the four history titles (compilations and their per-title listings)
    requests = listed + 2 * math.ceil((listed - 4) / BATCH) + history_docs + history_listings
    header = f"""# Commonwealth Acts fetched from the Federal Register of Legislation API (EPUB): every principal Act in
# force, in its latest registered version (the latest compilation, or the text as made for an Act that was never
# amended: `as_made: true`), plus the full compilation history of four titles (`history: true`).
# Generated {today} by scripts/build_frl_manifest.py from {API_BASE}/Titles (isInForce, isPrincipal):
# {len(rows)} Acts in force, {len(principal)} principal Acts ({in_sample} of them in the original sample);
# {listed} titles listed: {len(compiled) + len(SAMPLE)} whose latest version is a registered compilation and
# {len(as_made)} never amended (as made only; {len(without_epub)} of them have no EPUB document and are reported as
# skipped by the connector); {len(no_version)} principal Acts have no registered version and are not listed.
{LICENCE}# Scale: about one request per title at 10 s = {requests * 10 / 3600:.1f} hours of crawl delay ({requests}
# requests: documents, batched listings, the four history titles).
# Legislative instruments are a separate source (au-federal-register-instruments, not loaded by default).
"""
    return header + _head(SOURCE_ID["Act"]) + "\n".join(lines) + "\n", listed


def build_instruments(http: PoliteHttp, limit: int) -> tuple[str, int]:
    rows = _titles(http, "LegislativeInstrument")
    principal = [r for r in rows if r.get("isPrincipal")]
    print(f"{len(rows)} legislative instruments in force, {len(principal)} principal")
    if limit:
        principal = principal[:limit]
    lines = [_line(r) for r in principal]
    today = datetime.now(UTC).date().isoformat()
    requests = len(lines) + 2 * math.ceil(len(lines) / BATCH)
    header = f"""# Commonwealth legislative instruments fetched from the Federal Register of Legislation API (EPUB):
# every principal legislative instrument in force, in its latest registered version (the latest compilation, or the
# text as made for an instrument that was never amended; the connector decides from the Register's listing at run
# time). Generated {today} by scripts/build_frl_manifest.py --collection LegislativeInstrument from
# {API_BASE}/Titles (isInForce, collection = 'LegislativeInstrument', isPrincipal):
# {len(rows)} instruments in force, {len(principal)} principal ones listed; amending instruments (no text of their
# own to fetch) are not.
{LICENCE}# Scale: about one request per title at 10 s = {requests * 10 / 3600:.0f} hours of crawl delay ({requests}
# requests: documents and batched listings). Not loaded by default (source descriptor: load_by_default: false).
"""
    return header + _head(SOURCE_ID["LegislativeInstrument"]) + "\n".join(lines) + "\n", len(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", choices=sorted(OUT), default="Act")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=0, help="keep only the first N titles after the sample (trials)")
    args = ap.parse_args()
    out = args.out or OUT[args.collection]
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=DELAY)
    if args.collection == "Act":
        text, listed = build_acts(http, args.limit)
    else:
        text, listed = build_instruments(http, args.limit)
    out.write_text(text, encoding="utf-8")
    print(f"{listed} titles written to {out} ({http.requests_made} network requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
