"""Write config/manifests/gb-legislation-gov-uk.yaml from legislation.gov.uk's own listing feeds.

    python scripts/build_legislation_gov_uk_manifest.py   # all in-force Acts/Measures (cached feeds are reused)
    python scripts/build_legislation_gov_uk_manifest.py --out /tmp/m.yaml --types asc,mwa   # a slice, for trials

For each legislation type the site publishes an Atom listing (``/<type>/data.feed?results-count=1000&page=N``; the
robots.txt of the site does not disallow it, and the developer pages document it). Each entry carries the item's title
and the link to its latest version: a dated link (``.../ukpga/1998/42/2026-04-01``) means the site holds a revised
text, ``.../enacted`` means only the text as enacted. An Act is listed when

* the site holds a revised text (dated link), and
* its title does not carry the editors' label "(repealed ...)", which the site adds to Acts repealed in full.

Acts for which only the as-enacted text exists are not listed (before 1991 the site revises only Acts still in force).
The nine works of the original sample keep their full point-in-time history; every other work is the current revised
text only. The listing takes 40 requests at the robots.txt crawl delay (5 s); the Acts themselves are fetched later by
the connector, one request each.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from defusedxml.ElementTree import fromstring

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "www.legislation.gov.uk"
CACHE = REPO_ROOT / "var" / "sources"
OUT = REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk.yaml"
DELAY = 5.0
TYPES = {
    "ukpga": "UK Public General Acts",
    "ukla": "UK Local Acts",
    "asp": "Acts of the Scottish Parliament",
    "anaw": "Acts of the National Assembly for Wales",
    "asc": "Acts of Senedd Cymru",
    "mwa": "Measures of the National Assembly for Wales",
}
# The original sample: whole point-in-time history kept.
HISTORY = [
    ("ukpga/1998/42", "Human Rights Act 1998"),
    ("ukpga/1978/30", "Interpretation Act 1978"),
    ("ukpga/1968/60", "Theft Act 1968 (extends to England and Wales)"),
    ("ukpga/2018/12", "Data Protection Act 2018"),
    ("ukpga/2015/15", "Consumer Rights Act 2015"),
    ("uksi/2018/480", "Data Protection (Charges and Information) Regulations 2018"),
    ("asp/2003/2", "Land Reform (Scotland) Act 2003"),
    ("mwa/2011/1", "Welsh Language (Wales) Measure 2011"),
    ("anaw/2016/1", "Renting Homes (Wales) Act 2016"),
]
ATOM = "{http://www.w3.org/2005/Atom}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
REPEALED_LABEL = re.compile(r"\(\s*repealed", re.I)
PATH = re.compile(r"^[a-z]+/(?:\d{4}|[A-Za-z0-9]+/\d+(?:-\d+)*)/\d+$")
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def entry_title(entry) -> str:  # type: ignore[no-untyped-def]
    """The English title (Welsh legislation has titles in both languages as <span xml:lang>)."""
    title = entry.find(f"{ATOM}title")
    if title is None:
        return ""
    spans = [s for s in title.iter() if s.tag.endswith("}span") and s.get(XML_LANG) == "en"]
    text = "".join((spans[0] if spans else title).itertext())
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(data: bytes) -> tuple[list[dict[str, str]], bool]:
    """(entries, has_next_page); each entry has path, version (the dated link or 'enacted'/'made') and title."""
    root = fromstring(data)
    entries = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = (entry.findtext(f"{ATOM}id") or "").strip()
        path = entry_id.split("/id/", 1)[-1]
        version = ""
        for link in entry.findall(f"{ATOM}link"):
            if link.get("rel") is None and link.get("href"):  # the item's own page, with its latest version
                version = link.get("href", "").split(path, 1)[-1].strip("/")
        entries.append({"path": path, "version": version, "title": entry_title(entry)})
    has_next = any(link.get("rel") == "next" for link in root.findall(f"{ATOM}link"))
    return entries, has_next


def selected(entry: dict[str, str]) -> bool:
    return (
        bool(DATED.match(entry["version"]))
        and not REPEALED_LABEL.search(entry["title"])
        and bool(PATH.match(entry["path"]))
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--types", default=",".join(TYPES), help="comma separated legislation types")
    args = ap.parse_args()
    types = [t for t in args.types.split(",") if t]
    unknown = [t for t in types if t not in TYPES]
    if unknown:
        print(f"unknown types {unknown}; known: {list(TYPES)}", file=sys.stderr)
        return 2
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=DELAY)
    robots = http.get(f"https://{HOST}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    listed: dict[str, list[dict[str, str]]] = {}
    totals: dict[str, int] = {}
    for leg_type in types:
        keep: list[dict[str, str]] = []
        total = 0
        page = 1
        while True:
            url = f"https://{HOST}/{leg_type}/data.feed?results-count=1000&page={page}"
            if not robots_allows(robots_txt, USER_AGENT, url):
                print(f"robots.txt disallows {url}", file=sys.stderr)
                return 1
            got = http.get(url, cache_key=f"feeds/{leg_type}-p{page}.xml")
            if got is None:
                print(f"{url}: not found", file=sys.stderr)
                return 1
            entries, has_next = parse_feed(got[0])
            total += len(entries)
            keep.extend(e for e in entries if selected(e))
            if not has_next:
                break
            page += 1
        listed[leg_type] = keep
        totals[leg_type] = total
        print(f"{leg_type}: {total} listed, {len(keep)} in force with a revised text", flush=True)

    history_paths = {p for p, _ in HISTORY}
    lines = []
    seen: set[str] = set()
    for path, title in HISTORY:
        lines.append(f"  - {{path: {path}, history: true}}    # {title}")
        seen.add(path)
    for leg_type in types:
        for e in listed[leg_type]:
            if e["path"] in seen or e["path"] in history_paths:
                continue
            seen.add(e["path"])
            lines.append(
                f"  - {{path: {e['path']}, history: false, title: {json.dumps(e['title'], ensure_ascii=False)}}}"
            )
    counts = Counter(p.split("/", 1)[0] for p in seen)
    today = datetime.now(UTC).date().isoformat()
    summary = ", ".join(f"{t} {counts[t]}" for t in types if counts[t])
    header = f"""# Works fetched from legislation.gov.uk: every Act and Measure with a revised (in-force) text, current
# version only, except the nine works marked `history: true`, which keep their full point-in-time history.
# Licence: Open Government Licence v3.0. Fair use: identifying User-Agent, robots Crawl-delay 5 s, far below the
# 1,500 requests / 5 minutes limit (https://www.legislation.gov.uk/fair-use-policy).
# Generated {today} by scripts/build_legislation_gov_uk_manifest.py from the site's listing feeds
# (https://www.legislation.gov.uk/<type>/data.feed): {len(seen)} works ({summary}).
# An Act is listed when the site holds a revised text for it (dated link in the feed, not "enacted") and its title
# carries no "(repealed ...)" label; Acts available only as enacted (13,253 UK Public General Acts, 20,154 Local Acts)
# are not listed. Statutory instruments are not included (see docs/jurisdictions/gb.md).
# Scale: one request per work at 5 s = {len(seen) * 5 / 3600:.1f} hours for the first run (history works need more).
# `path` is the canonical path on the site; Acts before 1963 use the regnal-year form (ukpga/Eliz2/10-11/58).
"""
    manifest_head = f"""schema_version: 1
source_id: gb-legislation-gov-uk
base_url: https://www.legislation.gov.uk
user_agent: "{USER_AGENT}"
crawl_delay_seconds: 5
works:
"""
    args.out.write_text(header + manifest_head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(seen)} works written to {args.out} ({http.requests_made} network requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
