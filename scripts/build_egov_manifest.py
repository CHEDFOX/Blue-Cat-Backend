"""Write config/manifests/jp-egov-laws.yaml from the e-Gov Law API's own list endpoint.

    python -m scripts.build_egov_manifest            # every law in force, all types
    python -m scripts.build_egov_manifest --pages 1  # first list page only (a test; the manifest is not written)

``GET /api/2/laws?repeal_status=None&limit=1000&offset=N`` lists the laws that are not repealed or expired
(Constitution, Acts, Cabinet Orders, Imperial Orders, Ministerial Ordinances and Rules: 現行法令). A law is kept
when its latest revision on or before today (``current_revision_info``) has been enforced (``CurrentEnforced``,
or ``PreviousEnforced`` when two revisions share the day); laws whose revisions are all still to take effect
are counted and left out (the connector would refuse them). Pages are cached under
``var/sources/laws.e-gov.go.jp/`` (one request per 5 s, User-Agent set).

The hand-picked laws above the ``# --- generated`` marker of the existing manifest are kept as they are
(including their ``history`` flags); every other law is appended with ``history: false`` (only the revision in
force, plus the revision list as the change report for amendments not yet in force).
"""

from __future__ import annotations

import sys
import time
from collections import Counter

from defusedxml.ElementTree import fromstring

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.polite_http import PoliteHttp

HOST = "laws.e-gov.go.jp"
LIST = f"https://{HOST}/api/2/laws"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
OUT = REPO_ROOT / "config" / "manifests" / "jp-egov-laws.yaml"
CACHE = REPO_ROOT / "var" / "sources"
MARKER = "# --- generated from the list endpoint by scripts/build_egov_manifest.py (do not edit below) ---"
PAGE = 1000
TYPE_ORDER = ["Constitution", "Act", "CabinetOrder", "ImperialOrder", "MinisterialOrdinance", "Rule", "Misc"]


def head_and_curated() -> tuple[list[str], list[str]]:
    lines = OUT.read_text(encoding="utf-8").splitlines()
    cut = lines.index(MARKER) if MARKER in lines else len(lines)
    start = lines.index("laws:") + 1
    return lines[:start], lines[start:cut]


def list_laws(max_pages: int | None) -> tuple[list[tuple[str, str, str]], Counter[str], int, int]:
    """(law_id, law_type, title) of every law in force, a count of what was left out, total_count, requests."""
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=5.0, sleep=time.sleep)
    kept: list[tuple[str, str, str]] = []
    left_out: Counter[str] = Counter()
    offset = 0
    total = 0
    pages = 0
    while max_pages is None or pages < max_pages:
        url = f"{LIST}?repeal_status=None&limit={PAGE}&offset={offset}&response_format=xml"
        got = http.get(url, cache_key=f"api2/laws/inforce-cur-offset{offset:06d}.xml")
        if got is None:
            raise SystemExit(f"{url}: not found")
        root = fromstring(got[0], forbid_dtd=True)
        total = int(root.findtext("total_count") or 0)
        count = 0
        for law in root.iter("law"):
            count += 1
            info, rev = law.find("law_info"), law.find("current_revision_info")
            law_id = (info.findtext("law_id") or "").strip() if info is not None else ""
            law_type = (info.findtext("law_type") or "").strip() if info is not None else ""
            status = (rev.findtext("current_revision_status") or "").strip() if rev is not None else ""
            title = " ".join((rev.findtext("law_title") or "").split()) if rev is not None else ""
            if not law_id or status not in ("CurrentEnforced", "PreviousEnforced"):
                left_out[f"{law_type}/{status or 'no revision'}"] += 1
                continue
            kept.append((law_id, law_type, title))
        pages += 1
        offset += count
        if count == 0 or offset >= total:
            break
    return kept, left_out, total, http.requests_made


def main() -> int:
    max_pages = int(sys.argv[sys.argv.index("--pages") + 1]) if "--pages" in sys.argv else None
    head, curated = head_and_curated()
    taken = {line.split("law_id:")[1].split(",")[0].strip() for line in curated if "law_id:" in line}
    kept, left_out, total, requests = list_laws(max_pages)
    seen: set[str] = set()
    rows = []
    for law_id, law_type, title in kept:
        if law_id in taken or law_id in seen:
            continue
        seen.add(law_id)
        rows.append(
            (TYPE_ORDER.index(law_type) if law_type in TYPE_ORDER else len(TYPE_ORDER), law_id, law_type, title)
        )
    rows.sort()
    lines = [f"  - {{law_id: {law_id}, history: false}}  # {law_type}: {title}" for _, law_id, law_type, title in rows]
    if max_pages is None:  # a partial listing must not overwrite the committed manifest
        OUT.write_text("\n".join(head + curated + [MARKER] + lines) + "\n", encoding="utf-8")
    by_type = Counter(t for _, _, t, _ in rows)
    print(f"list total_count {total}; kept {len(kept)} in force; {len(taken)} curated, {len(lines)} generated")
    print(f"generated by type: {dict(by_type)}")
    print(f"left out: {dict(left_out)}; {requests} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
