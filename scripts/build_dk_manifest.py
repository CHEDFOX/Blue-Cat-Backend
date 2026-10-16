"""Write config/manifests/dk-retsinformation.yaml: the lovbekendtgørelser (LBK H) in force on Retsinformation.

    python scripts/build_dk_manifest.py --sitemap-only          # read the ELI sitemap, count the candidates
    python scripts/build_dk_manifest.py --from-year 2024        # classify the documents of 2024 onwards (newest first)
    python scripts/build_dk_manifest.py --max-requests 300      # stop after 300 metadata requests (resumable)

Retsinformation's only public index is the ELI sitemap (https://www.retsinformation.dk/sitemap.xml, listed in its
robots.txt): the ELI of every document with a last-modified date, but not its type. The type and status are in each
document's ELI metadata (``https://www.retsinformation.dk/eli/lta/<year>/<number>.json``, documented in the FAQ):
``type_document`` is ``LBKH`` for a lovbekendtgørelse (an official consolidation published in Lovtidende A) and
``in_force`` says whether it is the current one. So the script reads the sitemap, then asks for the metadata of the
Lovtidende A documents (``/eli/lta/``) one by one, one request per 10 s like the connector, and keeps the LBK H that
are in force. Every answer is kept in a small TSV (``config/manifests/dk-retsinformation.classified.tsv``, committed),
so the run can be stopped and resumed and no document is asked for twice. The metadata bodies (about 50 kB each) are
not kept.

The six pilot Acts (with the earlier consolidations of Forvaltningsloven and Funktionærloven) stay as they are;
every other law gets its current consolidation only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "manifests" / "dk-retsinformation.yaml"
CACHE = ROOT / "var" / "sources"
TSV = ROOT / "config" / "manifests" / "dk-retsinformation.classified.tsv"  # committed: a run goes on from it
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "www.retsinformation.dk"
ELI = "http://data.europa.eu/eli/ontology#"
_LTA = re.compile(r"<loc>https://(?:www\.)?retsinformation\.dk/eli/lta/(\d{4})/(\d+)</loc>")
_PAGE = re.compile(r"sitemap\.xml\?page=(\d+)")
HEADER = """# Danish lovbekendtgørelser (official consolidations published in Lovtidende A) fetched as Lex Dania
# XML from Retsinformation by ELI (https://www.retsinformation.dk/eli/lta/<year>/<number>/xml).
# Terms: free, non-exclusive, unrestricted right of use (https://www.retsinformation.dk/api).
# The first six works are the pilot Acts: each lists its consolidations OLDEST FIRST (whole-law snapshots); the last
# one is the consolidation Retsinformation marked as current ("gældende") on 2026-10-14, found by following the ELI
# metadata (consolidates / consolidated_by). Every other work is ONE lovbekendtgørelse that Retsinformation's ELI
# metadata marks as in force (type_document LBKH, in_force inForce), found by scripts/build_dk_manifest.py.
# Scale: {candidates} Lovtidende A documents are listed in the ELI sitemap ({sitemap_total} documents in all, read on
# {built}); the sitemap does not give the type, so each candidate costs one metadata request (10 s). This manifest
# is the result of classifying {classified} of them ({years}); {found} lovbekendtgørelser in force were found. To
# widen: python scripts/build_dk_manifest.py --from-year <oldest year wanted> [--max-requests N]; it goes on where
# the last run stopped (documents already classified are listed in
# config/manifests/dk-retsinformation.classified.tsv) and takes about {hours_per_year:.1f} hours for each year of
# candidates.
"""


PILOT = """\
  - name: Forvaltningsloven        # LBK 1365/2007, 988/2012, 433/2014 (current; later changes lov 503/2018, 655/2024)
    consolidations: [lta/2007/1365, lta/2012/988, lta/2014/433]
  - name: Funktionærloven          # LBK 81/2009, 1002/2017 (current; later changes lov 1711/2018, 715/2025)
    consolidations: [lta/2009/81, lta/2017/1002]
  - name: Straffeloven             # LBK 1294/2025 (current)
    consolidations: [lta/2025/1294]
  - name: Aftaleloven              # LBK 193/2016 (current; later change lov 2158/2021)
    consolidations: [lta/2016/193]
  - name: Arveloven                # LBK 1347/2021 (current; later change lov 1464/2024)
    consolidations: [lta/2021/1347]
  - name: Databeskyttelsesloven    # LBK 289/2024 (current; consolidates lov 502/2018, 1783/2023, 1784/2023)
    consolidations: [lta/2024/289]
"""


def sitemap_elis(http: PoliteHttp) -> tuple[list[tuple[int, int]], int]:
    """All Lovtidende A ELIs (year, number) in the ELI sitemap, and the number of documents of every kind."""
    index = http.get(f"https://{HOST}/sitemap.xml")
    if index is None:
        raise SystemExit("the ELI sitemap is not served")
    pages = sorted({int(p) for p in _PAGE.findall(index[0].decode("utf-8-sig"))})
    elis: set[tuple[int, int]] = set()
    total = 0
    for page in pages:
        got = http.get(f"https://{HOST}/sitemap.xml?page={page}")
        if got is None:
            raise SystemExit(f"sitemap page {page} is not served")
        text = got[0].decode("utf-8-sig")
        total += text.count("<loc>")
        elis.update((int(y), int(n)) for y, n in _LTA.findall(text))
    return sorted(elis), total


def classify(body: bytes, year: int, number: int) -> dict[str, str]:
    """Type, status, date and titles from the ELI metadata (JSON-LD) of a document."""
    nodes = json.loads(body)
    main = next(n for n in nodes if str(n.get("@id", "")).endswith(f"/eli/lta/{year}/{number}"))
    expression = next((n for n in nodes if str(n.get("@id", "")).endswith(f"/eli/lta/{year}/{number}/dan")), {})

    def ref(node: dict, key: str) -> str:
        values = node.get(ELI + key) or [{}]
        return str(values[0].get("@id") or values[0].get("@value") or "")

    issued = ref(main, "date_document")[:10]
    return {
        "type": ref(main, "type_document").rsplit("#", 1)[-1],
        "in_force": ref(main, "in_force").rsplit("-", 1)[-1],
        "date": datetime.strptime(issued, "%d-%m-%Y").date().isoformat() if issued else "",
        "alt": re.sub(r"\s+", " ", ref(expression, "title_alternative")).strip(),
        "title": re.sub(r"\s+", " ", ref(expression, "title")).strip(),
    }


def read_tsv() -> dict[tuple[int, int], dict[str, str]]:
    done: dict[tuple[int, int], dict[str, str]] = {}
    if TSV.exists():
        for line in TSV.read_text(encoding="utf-8").splitlines():
            y, n, typ, inforce, issued, alt, title = line.split("\t")
            done[(int(y), int(n))] = {"type": typ, "in_force": inforce, "date": issued, "alt": alt, "title": title}
    return done


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-year", type=int, default=2007)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--sitemap-only", action="store_true")
    args = parser.parse_args(argv)

    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=10.0)
    elis, sitemap_total = sitemap_elis(http)
    per_year: dict[int, int] = {}
    for y, _ in elis:
        per_year[y] = per_year.get(y, 0) + 1
    print(f"sitemap: {sitemap_total} documents, {len(elis)} in Lovtidende A; per year:", file=sys.stderr)
    print(" ".join(f"{y}:{c}" for y, c in sorted(per_year.items(), reverse=True)[:25]), file=sys.stderr)
    if args.sitemap_only:
        return 0

    done = read_tsv()
    todo = sorted((e for e in elis if e[0] >= args.from_year and e not in done), reverse=True)
    TSV.parent.mkdir(parents=True, exist_ok=True)
    asked = 0
    with TSV.open("a", encoding="utf-8") as out:
        for year, number in todo:
            if args.max_requests is not None and asked >= args.max_requests:
                break
            url = f"https://{HOST}/eli/lta/{year}/{number}.json"
            try:
                got = http.get(url, headers={"Accept": "application/ld+json"})
            except FetchError as exc:
                print(f"stopped: {exc}", file=sys.stderr)
                return 1
            asked += 1
            row = {"type": "-", "in_force": "-", "date": "", "alt": "", "title": ""}
            if got is not None:
                try:
                    row = classify(got[0], year, number)
                except (ValueError, StopIteration, KeyError, IndexError):
                    row["type"] = "?"  # not JSON-LD (the website's HTML shell): not a document with metadata
                http.cache_path(url).unlink(missing_ok=True)
                http.cache_path(url).with_name(http.cache_path(url).name + ".meta.json").unlink(missing_ok=True)
            done[(year, number)] = row
            out.write(
                "\t".join([str(year), str(number), row["type"], row["in_force"], row["date"], row["alt"], row["title"]])
                + "\n"
            )
            out.flush()
    return write_manifest(elis, sitemap_total, per_year, done, args.from_year)


def write_manifest(
    elis: list[tuple[int, int]],
    sitemap_total: int,
    per_year: dict[int, int],
    done: dict[tuple[int, int], dict[str, str]],
    from_year: int,
) -> int:
    pilot_elis = set(re.findall(r"lta/\d{4}/\d+", PILOT))
    found = sorted(
        ((y, n) for (y, n), r in done.items() if r["type"] == "LBKH" and r["in_force"] == "inForce"),
        key=lambda e: (e[0], e[1]),
    )
    works: list[dict[str, object]] = []
    for y, n in found:
        eli = f"lta/{y}/{n}"
        if eli in pilot_elis:
            continue
        row = done[(y, n)]
        name = row["alt"] or re.sub(r"^Bekendtgørelse af ", "", row["title"]) or eli
        works.append({"name": name, "consolidations": [eli]})
    newest, oldest = (max(done), min(done)) if done else ((0, 0), (0, 0))
    span = f"from lta/{newest[0]}/{newest[1]} down to lta/{oldest[0]}/{oldest[1]}"
    candidates = sum(c for y, c in per_year.items() if y >= 2007)
    head = HEADER.format(
        candidates=candidates,
        sitemap_total=sitemap_total,
        built=date.today().isoformat(),
        classified=len(done),
        years=f"newest first, {span}",
        found=len(found),
        hours_per_year=(candidates / max(1, len([y for y in per_year if y >= 2007]))) * 10 / 3600,
    )
    lines = [
        "schema_version: 1",
        "source_id: dk-retsinformation",
        "base_url: https://www.retsinformation.dk/eli",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 10",
        "works:",
    ]
    added = [
        f"  - {{name: {json.dumps(w['name'], ensure_ascii=False)}, consolidations: [{w['consolidations'][0]}]}}"
        for w in works
    ]  # type: ignore[index]
    OUT.write_text(head + "\n".join(lines) + "\n" + PILOT + "\n".join(added) + "\n", encoding="utf-8")
    print(f"{len(works) + 6} works ({len(works)} added) from {len(done)} classified documents", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
