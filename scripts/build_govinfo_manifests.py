"""Write the manifests of the two GovInfo bulk sources from the repository's own directory listings.

    python scripts/build_govinfo_manifests.py [--cache-dir var/sources] [--fr-years 5] [--cfr-from 1996]
                                              [--plaw-from 119] [--offline]

* config/manifests/us-govinfo-bulk-statutes.yaml: every volume of the Statutes at Large listed in
  https://www.govinfo.gov/bulkdata/STATUTE/, the public and private laws of the Congresses from ``--plaw-from`` on
  (the Congresses whose laws no Statutes at Large volume holds yet), and the statute compilations (COMPS.zip).
* config/manifests/us-govinfo-bulk-cfr-fr.yaml: the annual editions of the CFR (one zip per title and year, from
  ``--cfr-from``) and the Federal Register (one zip per month of the last ``--fr-years`` calendar years).

Only directory listings are fetched (one per year / Congress), each with the polite client: robots.txt first, 5 s
between requests, cached. The archives themselves are fetched by the connector, not here.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date
from pathlib import Path

from ulis.sources.connectors.govinfo import BASE_URL, ROBOTS_URL, GovInfoClient
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
MANIFESTS = ROOT / "config" / "manifests"


def listing(client: GovInfoClient, path: str) -> list[str]:
    """Names of the entries of a directory of the bulk repository (``/bulkdata/CFR/2025/`` -> ``title-1`` ...)."""
    url = f"{BASE_URL}/{path.strip('/')}/"
    if client.robots is not None and not robots_allows(client.robots, USER_AGENT, url):
        raise FetchError(f"{url} is disallowed by robots.txt")
    got = client.get(url, cache_key=f"{path.strip('/')}/_listing.html")
    if got is None:
        return []
    text = got[0].decode("utf-8", errors="replace")
    prefix = f"/{path.strip('/')}/"
    names = []
    for href in re.findall(r'<a href="([^"]+)"', text):
        href = html.unescape(href)
        if href.startswith(prefix) and "/" not in href[len(prefix) :] and href != prefix:
            names.append(href[len(prefix) :])
    return sorted(set(names))


def header(lines: list[str]) -> str:
    return "".join(f"# {line}\n" for line in lines)


def write(path: Path, head: list[str], source_id: str, files: list[dict[str, object]]) -> None:
    import yaml

    out = [
        header(head).rstrip("\n"),
        "schema_version: 1",
        f"source_id: {source_id}",
        f"base_url: {BASE_URL}",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        "files:",
    ]
    for f in files:
        flow = yaml.safe_dump(f, default_flow_style=True, sort_keys=False, width=10_000).strip()
        out.append(f"  - {flow}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "var" / "sources")
    ap.add_argument("--fr-years", type=int, default=5)
    ap.add_argument("--cfr-from", type=int, default=1996)
    ap.add_argument("--plaw-from", type=int, default=119)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    today = date.today().isoformat()
    client = GovInfoClient({"user_agent": USER_AGENT, "crawl_delay_seconds": 5}, args.cache_dir, offline=args.offline)
    if client.robots is None:
        raise FetchError(f"{ROBOTS_URL}: not found; refusing to list without reading it")

    # ---- statutes
    volumes = sorted(int(v) for v in listing(client, "bulkdata/STATUTE") if v.isdigit())
    files: list[dict[str, object]] = [
        {"path": f"bulkdata/STATUTE/{v}/STATUTE-{v}.xml", "kind": "xml", "volume": v} for v in volumes
    ]
    congresses = sorted(int(c) for c in listing(client, "bulkdata/PLAW") if c.isdigit() and int(c) >= args.plaw_from)
    for congress in congresses:
        names = set(listing(client, f"bulkdata/PLAW/{congress}"))
        for kind in ("public", "private"):
            if kind in names:
                inner = set(listing(client, f"bulkdata/PLAW/{congress}/{kind}"))
                if f"PLAW-{congress}-{kind}.zip" in inner:
                    files.append(
                        {
                            "path": f"bulkdata/PLAW/{congress}/{kind}/PLAW-{congress}-{kind}.zip",
                            "kind": "zip",
                            "congress": congress,
                        }
                    )
    files.append({"path": "bulkdata/COMPS/COMPS.zip", "kind": "zip"})
    write(
        MANIFESTS / "us-govinfo-bulk-statutes.yaml",
        [
            "GovInfo Bulk Data Repository - Statutes at Large, public and private laws,",
            "statute compilations (USLM XML, beta).",
            f"Written by scripts/build_govinfo_manifests.py on {today} from the directory listings of",
            "https://www.govinfo.gov/bulkdata/STATUTE/ and /PLAW/ (robots.txt allows /bulkdata/; no Crawl-delay).",
            f"STATUTE: volumes {volumes[0]}-{volumes[-1]} ({len(volumes)}), one XML file each (1789 to the latest).",
            f"PLAW: the public and private laws of the Congresses {args.plaw_from} and later, as zips: the laws",
            "of earlier Congresses are in the Statutes at Large volumes and are not listed twice.",
            "COMPS: COMPS.zip, the statute compilations (an Act as amended; editorial, not the enacted text).",
            "No sha256 pins: these collections are live (GPO adds laws and corrects volumes); the connector",
            "records the SHA-256 of every file it stores.",
        ],
        "us-govinfo-bulk-statutes",
        files,
    )
    print(f"statutes: {len(files)} files ({len(volumes)} volumes, PLAW congresses {congresses})")

    # ---- CFR annual editions and Federal Register
    files = []
    years = sorted(int(y) for y in listing(client, "bulkdata/CFR") if y.isdigit() and int(y) >= args.cfr_from)
    for year in years:
        titles = sorted(
            int(t.removeprefix("title-"))
            for t in listing(client, f"bulkdata/CFR/{year}")
            if re.fullmatch(r"title-\d+", t)
        )
        for n in titles:
            files.append(
                {
                    "path": f"bulkdata/CFR/{year}/title-{n}/CFR-{year}-title-{n}.zip",
                    "kind": "zip",
                    "year": year,
                    "title": n,
                }
            )
    cfr_count = len(files)
    fr_all = sorted(int(y) for y in listing(client, "bulkdata/FR") if y.isdigit())
    fr_years = fr_all[-args.fr_years :]
    for year in fr_years:
        for month in sorted(m for m in listing(client, f"bulkdata/FR/{year}") if re.fullmatch(r"\d{2}", m)):
            files.append(
                {
                    "path": f"bulkdata/FR/{year}/{month}/FR-{year}-{month}.zip",
                    "kind": "zip",
                    "year": year,
                    "month": int(month),
                }
            )
    write(
        MANIFESTS / "us-govinfo-bulk-cfr-fr.yaml",
        [
            "GovInfo Bulk Data Repository - Code of Federal Regulations annual editions and",
            "Federal Register issues (XML).",
            f"Written by scripts/build_govinfo_manifests.py on {today} from the directory listings of",
            "https://www.govinfo.gov/bulkdata/CFR/ and /FR/ (robots.txt allows /bulkdata/; no Crawl-delay).",
            f"CFR: one zip per title and year for the editions {years[0]}-{years[-1]} that the repository holds",
            f"({cfr_count} title-years; earlier years are incomplete: not every title is published for every",
            "year). Ordered oldest edition first: the pipeline records a changed wording from the date of the",
            "edition that shows it, so editions must be loaded oldest first (and before the current eCFR text",
            "of the us-ecfr source).",
            f"FR: one zip per month of {fr_years[0]}-{fr_years[-1]} (the last {len(fr_years)} calendar years: the",
            "daily issues of the archive since 2000 are about 25 GB of XML); the parser keeps the final rules.",
            "No sha256 pins: these collections are live; the connector records the SHA-256 of every file it",
            "stores.",
        ],
        "us-govinfo-bulk-cfr-fr",
        files,
    )
    print(f"cfr-fr: {len(files)} files (CFR {cfr_count} title-years {years[0]}-{years[-1]}, FR months of {fr_years})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
