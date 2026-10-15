"""Write config/manifests/au-federal-register.yaml from the Federal Register of Legislation's own title list.

    python scripts/build_frl_manifest.py                  # every principal Act in force (cached pages are reused)
    python scripts/build_frl_manifest.py --out /tmp/m.yaml --limit 30    # a slice, for trials

The API (https://api.prod.legislation.gov.au/v1/, no key) lists titles with their status. An Act is listed when

* its status is "in force" (``isInForce``), and
* it is a principal Act (``isPrincipal``): amending Acts stay in force as law but have no compilation, because their
  amendments are written into the principal Act's compilation, so there is no current text of their own to fetch, and
* its latest version is a registered compilation. A principal Act that was never amended has only its "as made"
  version (compilation 0, register id = title id), which the connector does not ingest (the Register dates it from the
  making date, not from commencement), so it is counted in the header and not listed. 40 titles are checked per
  request.

The Constitution and the seven Acts of the original sample keep the history settings they had (four with every
registered compilation, the others latest only); every other Act is the latest registered compilation only.
Legislative instruments (about 24,000 in force) are not listed. The listing takes about 10 requests at the 10 s
crawl delay plus one request per 40 principal Acts; the Acts themselves are fetched later by the connector, one
request each.
"""

from __future__ import annotations

import argparse
import json
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
OUT = REPO_ROOT / "config" / "manifests" / "au-federal-register.yaml"
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
TITLES = (
    f"{API_BASE}/Titles?$filter=isInForce%20eq%20true%20and%20collection%20eq%20'Act'"
    "&$select=id,name,year,number,isPrincipal&$orderby=id"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--limit", type=int, default=0, help="keep only the first N Acts after the sample (trials)")
    args = ap.parse_args()
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=DELAY)
    rows: list[dict[str, Any]] = []
    total = None
    while total is None or len(rows) < total:
        # The API's @odata.nextLink drops the $filter (verified 2026-10-15), so the pages are asked for with $skip.
        url = f"{TITLES}&$count=true&$skip={len(rows)}"
        key = f"titles/acts-in-force-skip{len(rows)}.json"
        got = http.get(url, cache_key=key, headers={"Accept": "application/json"})
        if got is None:
            print(f"{url}: not found", file=sys.stderr)
            return 1
        body = json.loads(got[0])
        total = int(body["@odata.count"])
        if not body.get("value"):
            break
        rows.extend(body["value"])
        print(f"{len(rows)} of {total} titles", flush=True)
    principal = [r for r in rows if r.get("isPrincipal")]
    print(f"{len(rows)} Acts in force, {len(principal)} principal Acts")

    lines = []
    seen: set[str] = set()
    for title_id, history, name in SAMPLE:
        seen.add(title_id)
        lines.append(f"  - {{title_id: {title_id}, history: {str(history).lower()}, title: {json.dumps(name)}}}")
    candidates = [r for r in principal if r["id"] not in seen]
    if args.limit:
        candidates = candidates[: args.limit]
    as_made_only: list[str] = []
    no_version: list[str] = []
    for i in range(0, len(candidates), BATCH):
        chunk = candidates[i : i + BATCH]
        ids = [r["id"] for r in chunk]
        in_list = ",".join(repr(t) for t in ids)
        url = (
            f"{API_BASE}/Versions?$filter=titleId%20in%20({in_list})%20and%20isLatest%20eq%20true"
            "&$select=titleId,registerId,compilationNumber,start"
        )
        got = http.get(url, cache_key=f"versions/latest-{ids[0]}-{ids[-1]}-{len(ids)}.json", headers=JSON)
        if got is None:
            print(f"{url}: not found", file=sys.stderr)
            return 1
        latest = {v["titleId"]: v for v in json.loads(got[0])["value"]}
        for r in chunk:
            v = latest.get(r["id"])
            if v is None or not v.get("registerId"):
                no_version.append(r["id"])
            elif v["registerId"] == r["id"] or v.get("compilationNumber") in (None, "0"):
                as_made_only.append(r["id"])
            else:
                seen.add(r["id"])
                title = json.dumps(r["name"], ensure_ascii=False)
                lines.append(f"  - {{title_id: {r['id']}, history: false, title: {title}}}")
        print(f"checked {min(i + BATCH, len(candidates))} of {len(candidates)} principal Acts", flush=True)
    today = datetime.now(UTC).date().isoformat()
    in_sample = sum(1 for r in principal if r["id"] in {t for t, _, _ in SAMPLE})
    header = f"""# Commonwealth Acts fetched from the Federal Register of Legislation API (EPUB compilations): every
# principal Act in force, latest registered compilation, plus the full compilation history of four titles
# (`history: true`).
# Generated {today} by scripts/build_frl_manifest.py from {API_BASE}/Titles (isInForce, isPrincipal):
# {len(rows)} Acts in force, {len(principal)} principal Acts ({in_sample} of them in the original sample);
# {len(seen)} titles listed. {len(as_made_only)} principal Acts have only their "as made" version (never amended:
# no compilation) and {len(no_version)} have no registered version; neither is listed.
# Licence: CC BY 4.0 (www.legislation.gov.au/terms-of-use). Automated downloading is allowed if robots.txt's
# Crawl-delay (10 s) is respected (help page "Data share and reuse"). The help page prefers incremental crawls outside
# 08:00-20:00 Australian time (UTC+10): `avoid_hours` makes the connector wait for the end of that window.
# `batch_listings` asks the Register for the versions and documents of 40 titles per request.
# Scale: about one request per title at 10 s = {(len(seen) + len(seen) // 20 + 130) * 10 / 3600:.1f} hours of crawl
# delay (documents, batched listings, the four history titles).
# Legislative instruments (about 24,000 in force) are not included (docs/jurisdictions/au.md).
"""
    head = f"""schema_version: 1
source_id: au-federal-register
api_base: {API_BASE}
user_agent: "{USER_AGENT}"
crawl_delay_seconds: 10
batch_listings: true
avoid_hours: {{utc_offset: 10, start: 8, end: 20}}
works:
"""
    args.out.write_text(header + head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(seen)} titles written to {args.out} ({http.requests_made} network requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
