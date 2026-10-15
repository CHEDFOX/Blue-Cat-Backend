"""Add the acts of Normattiva's "Costituzione e Codici" page to config/manifests/it-normattiva.yaml.

    python -m scripts.build_normattiva_manifest [--date YYYY-MM-DD]

Why only these acts. Normattiva lists its acts (by year, type, ...) under ``/ricerca/`` and ``/mfr/ricerca/``, which
www.normattiva.it/robots.txt disallows for every robot, and on the open-data portal dati.normattiva.it, whose robots.txt
request is answered with HTTP 409 by the publisher's protection system (read 2026-10-14 and again 2026-10-15). The
sitemap lists static pages only, and the page of a partial URN (``urn:nir:stato:decreto.legislativo:2003``) is an error
page. The one listing of acts that a polite client may read is the static page ``/staticPage/codici``: the Constitution,
the codes and the regulations that implement them. That is what this script adds, so laws, decreti legislativi and
decreti-legge outside that page are not in the manifest. They are not guessed from number ranges either (that would be
tens of thousands of requests for pages that mostly do not exist).

For each act of the page the script requests the act's own page by URN (the documented way to link to an act; it also
starts the session the export needs), reads the Gazzetta Ufficiale date and the redactional code from the
"esporta in Akoma ntoso" link on it, and appends the work to the manifest with the date it was read. Acts already in
the manifest are left untouched; acts whose URN type the parser does not read (ministerial decrees) are reported and
skipped. Requests: 1 + 1 per new act, 6 s apart; every response is cached under ``var/sources/www.normattiva.it/``.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date

import yaml

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.normattiva import HOST, page_url
from ulis.sources.connectors.polite_http import PoliteHttp
from ulis.sources.parsers.normattiva import ACT_TYPES

OUT = REPO_ROOT / "config" / "manifests" / "it-normattiva.yaml"
CACHE = REPO_ROOT / "var" / "sources"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
MONTHS = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
LINK = re.compile(r'<a\s[^>]*href="([^"]*N2Ls\?(urn:nir:[^"]+))"[^>]*>(.*?)</a>', re.S)
AKN = re.compile(
    r"caricaAKN\?dataGU=(\d{8})&(?:amp;)?codiceRedaz=((?:\d{3}[A-Z]\d{4}|\d{2}[A-Z]\d{5}))&(?:amp;)?dataVigenza=\d{8}"
)
URN = re.compile(r"^urn:nir:stato:([a-z.]+):(\d{4})-(\d{2})-(\d{2});(\d+)$")
ABBREVIATION = {"regio.decreto": "R.D.", "relazione.e.regio.decreto": "R.D.", "decreto.legislativo": "d.lgs."}


def listing(page: str) -> list[tuple[str, str]]:
    """(urn, label) of every act linked from the page, in page order, once each."""
    seen: dict[str, str] = {}
    for _, urn, inner in LINK.findall(page):
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", "", inner)).split())
        seen.setdefault(urn, label)
    return list(seen.items())


def describe(urn: str, label: str) -> str:
    m = URN.match(urn)
    if not m:
        return label
    kind, year, month, day, number = m.groups()
    abbr = ABBREVIATION.get(kind) or {"decreto.del.presidente.della.repubblica": "d.P.R."}.get(kind, "")
    return f"{label} ({abbr} {int(day)} {MONTHS[int(month) - 1]} {year}, n. {int(number)})"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=date.today().isoformat(), help="vigenza date for the new acts")
    args = parser.parse_args(argv)
    when = date.fromisoformat(args.date)

    http = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=6.0, cookies=True)
    http.get(f"https://{HOST}/robots.txt")
    got = http.get(f"https://{HOST}/staticPage/codici")
    if got is None:
        raise SystemExit("the page /staticPage/codici was not found")
    acts = listing(got[0].decode("utf-8", "replace"))
    known = {w["urn"] for w in yaml.safe_load(OUT.read_text(encoding="utf-8"))["works"]}
    added: list[str] = []
    skipped: list[str] = []
    for urn, label in acts:
        m = URN.match(urn)
        if urn == "urn:nir:stato:costituzione" or urn in known:
            continue
        if not m or m.group(1) not in ACT_TYPES and m.group(1) != "relazione.e.regio.decreto":
            skipped.append(f"{urn} ({label}): URN type not read by the parser")
            continue
        page = http.get(page_url(urn))
        if page is None:
            skipped.append(f"{urn} ({label}): page not found")
            continue
        link = AKN.search(page[0].decode("utf-8", "replace"))
        if link is None:
            skipped.append(f"{urn} ({label}): no 'esporta in Akoma ntoso' link on the page")
            continue
        gu, code = link.groups()
        kind, year, month, day, number = m.groups()
        added.append(
            "\n".join(
                [
                    f"- code: {code}",
                    f"  label: {describe(urn, label)}",
                    f"  gazzetta_date: '{gu[:4]}-{gu[4:6]}-{gu[6:]}'",
                    f"  urn: {urn}",
                    f"  vigenza: ['{when.isoformat()}']",
                ]
            )
        )
    text = OUT.read_text(encoding="utf-8")
    seen_codes = set(re.findall(r"^- code: (\S+)", text, re.M))
    added = [a for a in added if re.match(r"- code: (\S+)", a).group(1) not in seen_codes]  # type: ignore[union-attr]
    if added:
        OUT.write_text(text.rstrip("\n") + "\n" + "\n".join(added) + "\n", encoding="utf-8")
    print(f"{len(added)} acts added, {len(skipped)} skipped; page lists {len(acts)} acts")
    for line in skipped:
        print("  skipped:", line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
