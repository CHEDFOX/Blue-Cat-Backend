"""Write the legislation.gov.uk manifests from the site's own listing feeds.

    python scripts/build_legislation_gov_uk_manifest.py                 # both manifests (cached feeds are reused)
    python scripts/build_legislation_gov_uk_manifest.py --scope primary  # only config/manifests/gb-legislation-
    gov-uk.yaml
    python scripts/build_legislation_gov_uk_manifest.py --scope secondary --out /tmp/m.yaml --types ssi,nisr   # a slice

For each legislation type the site publishes an Atom listing (``/<type>/data.feed?results-count=1000&page=N``; the
robots.txt of the site does not disallow it, and the developer pages document it). Each entry carries the item's title,
the link to its latest version and the formats the site holds for it:

* an ``application/akn+xml`` alternate link means the text exists as Akoma Ntoso XML (what the parser reads). Items with
  only a PDF (the King's Printer scans of most Acts before 1988, Local Acts before 1991, Private Acts, Statutory Rules
  and Orders) have no machine-readable text on the site and are counted but not listed (robots.txt also disallows
  ``*/data.pdf``);
* a dated latest link (``.../ukpga/1998/42/2026-04-01``) means the item has a revised text with a version history; the
  work is listed with ``history: true`` and the connector fetches every version the lifecycle lists;
* ``.../enacted`` or ``.../made`` as the latest link means the item exists in one version only (as enacted / as made);
  the work is listed with ``version: enacted|made`` and one request.

Every item with XML is listed, repealed and revoked items included (their last in-force versions are history). The
listing takes one request per 1,000 items at the robots.txt crawl delay (5 s); the works themselves are fetched later by
the connector. Numbers per type go into the manifest header and, with ``--stats``, into a JSON file.
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
CACHE = Path(__import__("os").environ.get("ULIS_SOURCE_CACHE_DIR", REPO_ROOT / "var" / "sources"))
DELAY = 5.0
# type -> (description, scope). Scope "primary" is Acts, Measures and Orders in Council; "secondary" is the rest.
TYPES: dict[str, tuple[str, str]] = {
    "ukpga": ("UK Public General Acts (1801-)", "primary"),
    "ukla": ("UK Local Acts (1801-)", "primary"),
    "ukppa": ("UK Private and Personal Acts (1539-1987)", "primary"),
    "gbppa": ("Private and Personal Acts of the Parliament of Great Britain", "primary"),
    "gbla": ("Local Acts of the Parliament of Great Britain (1797-1800)", "primary"),
    "apgb": ("Acts of the Parliament of Great Britain (1707-1800)", "primary"),
    "aep": ("Acts of the English Parliament (1235-1706)", "primary"),
    "aosp": ("Acts of the Old Scottish Parliament (1424-1707)", "primary"),
    "aip": ("Acts of the Old Irish Parliament (1495-1800)", "primary"),
    "apni": ("Acts of the Parliament of Northern Ireland (1921-1972)", "primary"),
    "mnia": ("Measures of the Northern Ireland Assembly (1974)", "primary"),
    "nia": ("Acts of the Northern Ireland Assembly (2000-)", "primary"),
    "nisi": ("Northern Ireland Orders in Council (1972-)", "primary"),
    "ukcm": ("UK Church Measures (1920-)", "primary"),
    "asp": ("Acts of the Scottish Parliament (1999-)", "primary"),
    "anaw": ("Acts of the National Assembly for Wales (2012-2020)", "primary"),
    "asc": ("Acts of Senedd Cymru (2020-)", "primary"),
    "mwa": ("Measures of the National Assembly for Wales (2008-2011)", "primary"),
    "uksi": ("UK Statutory Instruments (1948-)", "secondary"),
    "ssi": ("Scottish Statutory Instruments (1999-)", "secondary"),
    "wsi": ("Welsh Statutory Instruments (1999-)", "secondary"),
    "nisr": ("Northern Ireland Statutory Rules (1974-)", "secondary"),
    "ukci": ("UK Church Instruments (1991-2016)", "secondary"),
}
MANIFESTS = {
    "primary": ("gb-legislation-gov-uk", REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk.yaml"),
    "secondary": (
        "gb-legislation-gov-uk-secondary",
        REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk-secondary.yaml",
    ),
    # The never-revised statutory instruments: one request each, as made. A source of its own (not loaded by default).
    "secondary_made": (
        "gb-legislation-gov-uk-secondary-made",
        REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk-secondary-made.yaml",
    ),
}
ATOM = "{http://www.w3.org/2005/Atom}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
REPEALED_LABEL = re.compile(r"\(\s*(?:repealed|revoked)", re.I)
PATH = re.compile(r"^[a-z]+/(?:\d{4}(?:/[A-Za-z][A-Za-z0-9]*)?|[A-Za-z0-9&]+/\d+(?:-\d+)*)/\d+$")
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def entry_title(entry) -> str:  # type: ignore[no-untyped-def]
    """The English title (Welsh legislation has titles in both languages as <span xml:lang>)."""
    title = entry.find(f"{ATOM}title")
    if title is None:
        return ""
    spans = [s for s in title.iter() if s.tag.endswith("}span") and s.get(XML_LANG) == "en"]
    text = "".join((spans[0] if spans else title).itertext())
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(data: bytes) -> tuple[list[dict[str, object]], bool]:
    """(entries, has_next_page); an entry has path, version (the dated link or 'enacted'/'made'), title, akn, xml."""
    root = fromstring(data)
    entries = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = (entry.findtext(f"{ATOM}id") or "").strip()
        path = entry_id.split("/id/", 1)[-1]
        version = ""
        types = set()
        for link in entry.findall(f"{ATOM}link"):
            if link.get("rel") is None and link.get("href"):  # the item's own page, with its latest version
                version = link.get("href", "").split(path, 1)[-1].strip("/")
            elif link.get("rel") == "alternate":
                types.add(link.get("type"))
        entries.append(
            {
                "path": path,
                "version": version,
                "title": entry_title(entry),
                "akn": "application/akn+xml" in types,
                "xml": "application/xml" in types,
            }
        )
    has_next = any(link.get("rel") == "next" for link in root.findall(f"{ATOM}link"))
    return entries, has_next


def classify(entry: dict[str, object]) -> str:
    """history (dated latest link), single (enacted or made only), pdf_only, xml_only, or other (own version name)."""
    if not entry["akn"]:
        return "pdf_only" if not entry["xml"] else "xml_only"
    version = str(entry["version"])
    if DATED.match(version):
        return "history"
    if version in ("enacted", "made"):
        return "single"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["primary", "secondary", "all"], default="all")
    ap.add_argument("--out", type=Path, help="output file (only with a single --scope)")
    ap.add_argument("--out-made", type=Path, help="output file of the never-revised instruments (secondary scope)")
    ap.add_argument("--types", help="comma separated legislation types (default: those of the scope)")
    ap.add_argument("--stats", type=Path, help="write per-type counts as JSON")
    args = ap.parse_args()
    scopes = ["primary", "secondary"] if args.scope == "all" else [args.scope]
    if args.out and len(scopes) != 1:
        print("--out needs a single --scope", file=sys.stderr)
        return 2
    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=DELAY)
    robots = http.get(f"https://{HOST}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    all_stats: dict[str, dict[str, dict[str, int]]] = {}
    for scope in scopes:
        types = [t for t in (args.types.split(",") if args.types else TYPES) if TYPES.get(t, ("", ""))[1] == scope]
        source_id, default_out = MANIFESTS[scope]
        out = args.out or default_out
        kept: dict[str, list[dict[str, object]]] = {t: [] for t in types}
        counters: dict[str, Counter[str]] = {t: Counter() for t in types}
        # A work is counted once, under the type in its path: the listing of one type can hold works of another (the
        # UK statutory instruments list holds Welsh statutory instruments, which carry a UK number as well).
        seen: set[str] = set()
        for leg_type in types:
            page = 1
            while True:
                url = f"https://{HOST}/{leg_type}/data.feed?results-count=1000&page={page}"
                if not robots_allows(robots_txt, USER_AGENT, url):
                    print(f"robots.txt disallows {url}", file=sys.stderr)
                    return 1
                got = http.get(url)
                if got is None:
                    print(f"{url}: not found", file=sys.stderr)
                    return 1
                entries, has_next = parse_feed(got[0])
                for e in entries:
                    owner = str(e["path"]).split("/", 1)[0]
                    if e["path"] in seen or owner not in counters:
                        continue  # a work of a type of the other scope (nisi in the uksi list): its own list
                    seen.add(str(e["path"]))
                    counts = counters[owner]
                    kind = classify(e)
                    counts["items"] += 1
                    counts[kind] += 1
                    if kind in ("history", "single", "other"):
                        if not PATH.match(str(e["path"])):
                            print(f"unusual path not listed: {e['path']}", file=sys.stderr)
                            counts["bad_path"] += 1
                            continue
                        if REPEALED_LABEL.search(str(e["title"])):
                            counts["listed_repealed_or_revoked"] += 1
                        kept[owner].append(e)
                if not has_next:
                    break
                page += 1
        stats = {t: dict(c) for t, c in counters.items()}
        for t in types:
            print(f"{t}: {stats[t]}", flush=True)
        all_stats[scope] = stats
        if scope == "secondary":
            # Revised instruments (with history) and never-revised ones (as made) are two sources.
            revised = {t: [e for e in kept[t] if classify(e) != "single"] for t in types}
            made = {t: [e for e in kept[t] if classify(e) == "single"] for t in types}
            made_id, made_out = MANIFESTS["secondary_made"]
            write_manifest(scope, source_id, out, types, revised, stats, http.requests_made)
            write_manifest("secondary_made", made_id, args.out_made or made_out, types, made, stats, http.requests_made)
            n = sum(len(v) for v in revised.values()) + sum(len(v) for v in made.values())
        else:
            write_manifest(scope, source_id, out, types, kept, stats, http.requests_made)
            n = sum(len(v) for v in kept.values())
        print(f"{scope}: {n} works written ({http.requests_made} network requests)")
    if args.stats:
        args.stats.write_text(json.dumps(all_stats, indent=1), encoding="utf-8")
    return 0


# Scale of loading a whole manifest, extrapolated from a stratified slice (fixed seed, per type and per kind of work:
# documents per work, provision versions per document, raw bytes per document, database bytes per provision version)
# run on 2026-10-16; heavy-tailed, so read it as plus or minus 50 percent. Requests include the versions that answer 404
# once and the listing pages; hours are at the 5 s crawl delay, one request at a time.
SCALE = {
    "primary": (
        "about 24,700 documents, 28,200 requests, 39 hours; about 2.75 million provision versions; about 10.7 GB of "
        "database and 9.2 GB of raw responses"
    ),
    "secondary": (
        "about 42,700 documents, 48,700 requests, 68 hours; about 1.18 million provision versions; about 4.6 GB of "
        "database and 2.4 GB of raw responses"
    ),
    "secondary_made": (
        "65,505 documents, 65,505 requests, 91 hours; about 2.31 million provision versions; about 5.8 GB of database "
        "and 1.8 GB of raw responses (not loaded by default)"
    ),
}


def write_manifest(
    scope: str,
    source_id: str,
    out: Path,
    types: list[str],
    kept: dict[str, list[dict[str, object]]],
    stats: dict[str, dict[str, int]],
    requests_made: int,
) -> None:
    today = datetime.now(UTC).date().isoformat()
    works = [e for t in types for e in kept[t]]
    n_hist = sum(
        1 for e in works if classify(e) != "single"
    )  # revised texts, and the few types with another version name
    n_single = len(works) - n_hist
    table = "\n".join(
        f"#   {t:6} {TYPES[t][0]:62} items {stats[t].get('items', 0):>7}, listed here {len(kept[t]):>7} "
        f"(of which with XML: revised {stats[t].get('history', 0)}, one version {stats[t].get('single', 0)}), "
        f"PDF only {stats[t].get('pdf_only', 0)}"
        for t in types
    )
    lines = []
    for t in types:
        for e in kept[t]:
            path, single = str(e["path"]), classify(e) == "single"
            if scope == "primary":
                title = json.dumps(e["title"], ensure_ascii=False)
                extra = f", version: {e['version']}" if single else ""
                lines.append(f"  - {{path: {path}, history: {'false' if single else 'true'}{extra}, title: {title}}}")
            else:
                # Compact: a plain path is a work with a version history (history_default), `path@made` has one version.
                lines.append(f'  - "{path}@{e["version"]}"' if single else f'  - "{path}"')
    kind = {
        "primary": "Acts, Measures and Orders in Council",
        "secondary": "statutory instruments and rules that have versions",
        "secondary_made": "statutory instruments and rules that exist as made only",
    }[scope]
    header = f"""# Works fetched from legislation.gov.uk ({kind}): every such item the site holds as Akoma Ntoso XML.
# Licence: Open Government Licence v3.0 (pre-1987 secondary legislation contributed by Westlaw UK also carries the
# attribution "Westlaw UK derived from Crown Copyright material and contributed to legislation.gov.uk").
# Fair use: identifying User-Agent, robots.txt Crawl-delay 5 s (1 request per 5 s; the limit is 1,500 per 5 minutes).
# Generated {today} by scripts/build_legislation_gov_uk_manifest.py from the site's listing feeds
# (https://www.legislation.gov.uk/<type>/data.feed?results-count=1000): {len(works)} works, of which {n_hist} have a
# version history (`history: true`: every point-in-time version the lifecycle lists is fetched) and {n_single} exist
# in one version only (`version: enacted|made`: one request).
# Repealed and revoked items are included (their in-force versions are history); items the site holds only as PDF
# (no machine-readable text; robots.txt disallows */data.pdf) are not listed.
# Scale of loading all of it (estimated from a slice, plus or minus 50 percent): {SCALE[scope]}.
{table}
# `path` is the canonical path on the site; Acts before 1963 use the regnal-year form (ukpga/Eliz2/10-11/58).
"""
    head = f"""schema_version: 1
source_id: {source_id}
base_url: https://www.legislation.gov.uk
user_agent: "{USER_AGENT}"
crawl_delay_seconds: 5
history_default: true
works:
"""
    out.write_text(header + head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"(header requests so far: {requests_made})")


if __name__ == "__main__":
    raise SystemExit(main())
