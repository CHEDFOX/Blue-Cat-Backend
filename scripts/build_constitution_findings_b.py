"""Write the planned/rejected constitution sources and docs/constitutions-americas-europe.md from the findings table.

python scripts/build_constitution_findings_b.py

The table is ``scripts/constitution_findings_b_data.py`` (one entry per publisher examined, read 2026-10-15). The three sources
that were ingested (cr-tse-constitution, mx-diputados-constitution, by-president-constitution) have hand-written descriptors;
their rows in the document are defined below.
"""

# ruff: noqa: E501 - the generated document quotes long lines
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constitution_findings_b_data import PLANNED  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
READ_ON = "2026-10-15"

ACTIVE_ROWS = [
    dict(
        id="cr-tse-constitution",
        country="Costa Rica",
        region="Americas",
        url="https://www.tse.go.cr/pdf/normativa/constitucion.pdf",
        langs="es",
        finding='Site footer: "Basada en una obra en https://www.tse.go.cr" with the licence link http://creativecommons.org/licenses/by-sa/4.0/ (rel="license"); the Términos y Condiciones de Uso restrict only the Tribunal\'s marks and graphics.',
        outcome="ingested - CC BY-SA 4.0 (AMBER: footer licence)",
    ),
    dict(
        id="mx-diputados-constitution",
        country="Mexico",
        region="Americas",
        url="https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf",
        langs="es",
        finding='No robots.txt, no terms. Ley Federal del Derecho de Autor art. 14: "No son objeto de la protección como derecho de autor ... VIII. Los textos legislativos, reglamentarios, administrativos o judiciales, así como sus traducciones oficiales" (read on the same host).',
        outcome="ingested - statutory basis (AMBER)",
    ),
    dict(
        id="by-president-constitution",
        country="Belarus",
        region="Europe",
        url="https://president.gov.by/ru/gosudarstvo/constitution",
        langs="ru, be",
        finding='"Материалы Портала могут использоваться ... без каких-либо ограничений по объему и срокам публикации. Единственным условием является ссылка на первоисточник. Никакого предварительного согласия ... не требуется."',
        outcome="ingested - portal notice (AMBER)",
    ),
]

NO_SOURCE_ROWS = [
    dict(
        country="Austria",
        region="Europe",
        url="https://www.vfgh.gv.at/ ; https://www.parlament.gv.at/verfassung/",
        langs="de",
        finding='https://www.vfgh.gv.at/robots.txt: "User-agent: * Disallow:" (nothing); the Court\'s pages read hold no text of the B-VG. https://www.parlament.gv.at/robots.txt is readable; the guessed address /verfassung/ answered HTTP 404. The B-VG is on the RIS, whose source at-ris-bundesrecht stays planned (data host disallows all robots).',
        outcome="not found elsewhere - no new descriptor; see at-ris-bundesrecht",
    ),
    dict(
        country="Slovakia",
        region="Europe",
        url="https://www.nrsr.sk/web/",
        langs="sk",
        finding="https://www.nrsr.sk/robots.txt: name or service not known (the host did not resolve). Slovakia has the suspended source sk-slovlex-static.",
        outcome="unreachable - no new descriptor",
    ),
]

REGION = {
    "Americas": [
        "AG",
        "BS",
        "BB",
        "BZ",
        "BO",
        "BR",
        "CL",
        "CO",
        "CR",
        "CU",
        "DM",
        "DO",
        "EC",
        "SV",
        "GD",
        "GT",
        "GY",
        "HT",
        "HN",
        "JM",
        "MX",
        "NI",
        "PA",
        "PY",
        "PE",
        "KN",
        "LC",
        "VC",
        "SR",
        "TT",
        "VE",
    ]
}


def region_of(cc: str) -> str:
    return (
        "Americas"
        if cc in REGION["Americas"]
        else ("Caucasus and Central Asia" if cc in ("AM", "AZ", "GE", "KZ", "KG", "TJ", "TM", "UZ") else "Europe")
    )


def cell(text: str, limit: int = 420) -> str:
    flat = re.sub(r"\s+", " ", text).replace("|", "/").strip()
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + " ..."


def yaml_for(e: dict) -> str:
    rejected = e["status"] == "rejected"
    permission = "not_allowed" if rejected else "unknown"
    prepared = (
        ROOT / "config" / "manifests" / f"{e['id']}.yaml"
    )  # a manifest ready for activation, if the entry has one
    endpoint = f"config/manifests/{e['id']}.yaml" if prepared.exists() else e["url"]
    reviewed_by = (
        f"ULIS project, {READ_ON}. READ - robots.txt: {e['robots']} Terms or notice: {e['terms']} Document: {e['document']} "
        "Nothing beyond these pages was requested."
    )
    doc = {
        "schema_version": 1,
        "id": e["id"],
        "name": f"{e['doc_title']} ({e['publisher'].split(' (')[0].split(';')[0]})",
        "publisher": e["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [e["cc"]],
        "document_kinds": ["constitution"],
        "languages": e["langs"],
        "coverage": (
            f"The constitution of {e['country']} as this publisher offers it ({e['doc_title']}). "
            f"Nothing is ingested: the source is recorded as {e['status']}."
        ),
        "access": {"method": "permitted_web_retrieval", "endpoint": endpoint, "authentication": None},
        "update_frequency": "Not stated.",
        "rate_limits": e["robots"],
        "licence": {
            "id": "REJECTED-TERMS" if rejected else "UNVERIFIED",
            "name": e["licence"],
            "url": e["url"],
            "review_status": e["review"],
            "reviewed_by": reviewed_by,
            "reviewed_on": READ_ON,
            "redistribution": permission,
            "bulk_download": permission,
            "automated_processing": "not_allowed" if rejected else "unknown",
            "commercial_use": permission,
            "attribution_required": None,
            "attribution_text": None,
            "conditions": [
                "Not ingested. The source can be activated only when the publisher's own pages state terms that allow reuse, or a statute"
                " excluding legislative texts from copyright is read and quoted (the project's earlier AMBER approvals).",
            ],
            "notes": e.get("note")
            or "Recorded by the constitutions sweep; the finding above is what the pages said when read.",
        },
        "provenance": f"No document was stored. The pages read on {READ_ON} are listed in licence.reviewed_by.",
        "known_limitations": ["Nothing is ingested."] + ([e["note"]] if e.get("note") else []),
        "status": e["status"],
    }
    header = (
        f"# {e['country']} - {e['doc_title']}: {e['status'].upper()}, nothing ingested. Generated by scripts/build_constitution_findings_b.py\n"
        f"# from scripts/constitution_findings_b_data.py (read {READ_ON}); edit the table, not this file.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def main() -> int:
    sources_dir = ROOT / "config" / "sources"
    for e in PLANNED:
        (sources_dir / f"{e['id']}.yaml").write_text(yaml_for(e), encoding="utf-8")

    rows: list[tuple[str, str, str]] = []  # (region, country, line)
    for r in ACTIVE_ROWS:
        rows.append(
            (
                r["region"],
                r["country"],
                f"| {r['country']} | `{r['id']}` | {r['url']} | {r['langs']} | {cell(r['finding'])} | {r['outcome']} |",
            )
        )
    for e in PLANNED:
        finding = f"{e['licence']}. {e['terms']}" if e["terms"] != "Not read." else e["licence"]
        first = cell(finding)
        rows.append(
            (
                region_of(e["cc"]),
                e["country"],
                f"| {e['country']} | `{e['id']}` | {e['url']} | {', '.join(e['langs'])} | {first} | {e['status']} |",
            )
        )
    for r in NO_SOURCE_ROWS:
        rows.append(
            (
                r["region"],
                r["country"],
                f"| {r['country']} | - | {r['url']} | {r['langs']} | {cell(r['finding'])} | {r['outcome']} |",
            )
        )

    order = ["Americas", "Europe", "Caucasus and Central Asia"]
    lines = [
        "# Constitutions sweep: the Americas, Europe, the Caucasus and Central Asia",
        "",
        f"Read {READ_ON} by `scripts/build_constitution_findings_b.py` from `scripts/constitution_findings_b_data.py`; the sources are",
        "`config/sources/<id>.yaml` (the full robots.txt, terms and document findings of each publisher). Research aid, not legal advice.",
        "",
        "## Method",
        "",
        "- Scope: every country of the Americas, of Europe (including the microstates and Kosovo) and of the Caucasus and Central Asia that had no active",
        "  legislation source in `docs/corpus.md`; the constitution only. The 3 ingested documents' source is one descriptor per publisher.",
        "- Per host, the robots.txt was read first and every path checked against it (a group naming `ulis-research-bot`, else `User-agent: *`;",
        "  longest match). A host that answered 401/403/405 on robots.txt, a path it disallows, a TLS certificate chain that could not be verified, a bot",
        "  challenge or a CAPTCHA ended the attempt for that host: no other agent, proxy, mirror or browser was used.",
        "- At most about six requests per country: robots.txt, the page or PDF holding the constitution, and the terms or footer pages. Web search was",
        "  used only to find addresses (it does not touch the publishers' hosts).",
        "- A source is ingested only when the publisher's own words allow reuse (CC BY-SA, a portal notice) or a statute that excludes legislative texts",
        '  from copyright was read and quoted. A bare "© ... all rights reserved" footer, no terms at all, a bot challenge or a refusal leaves it `planned`; terms that',
        "  forbid the use make it `rejected`.",
        "- Connector and parser: `constitution_pages_b` (HTML, text-layer PDF or text; articles split by the document's own headings; blocks of",
        "  paragraphs where the structure is not recognisable); citation `<Country> Constitution art. N` (module `constitution_b`).",
        "",
        "## Result",
        "",
        f"- Ingested (status `active`): {len(ACTIVE_ROWS)} publishers, 4 documents: Costa Rica (es), Mexico (es), Belarus (ru, be).",
        f"- Recorded as `planned`: {sum(1 for e in PLANNED if e['status'] == 'planned')} publishers; as `rejected`: {sum(1 for e in PLANNED if e['status'] == 'rejected')} (Dominica, Portugal's Tribunal Constitucional).",
        "- Not reachable at all from the sandbox (refusal, unreachable host, unverifiable TLS chain): see the rows whose finding says so.",
        "",
        "## Outcomes by country",
        "",
    ]
    for region in order:
        lines += [
            f"### {region}",
            "",
            "| Country | Source | Source URL | Language | Finding | Outcome |",
            "|---|---|---|---|---|---|",
        ]
        for _, _, line in sorted((x for x in rows if x[0] == region), key=lambda x: (x[1], x[2])):
            lines.append(line)
        lines.append("")
    lines += [
        "## Countries not in the sweep",
        "",
        "Argentina, Australia, Canada, Croatia, the Czech Republic, Denmark, Estonia, Finland, France, Germany, Ireland, Italy, Latvia, Liechtenstein,",
        "Luxembourg, the Netherlands, Poland, Slovenia, Spain, Sweden, Switzerland, the United Kingdom, the United States and Uruguay already have an",
        "active legislation source that includes their constitution (see `docs/corpus.md`).",
        "",
    ]
    (ROOT / "docs" / "constitutions-americas-europe.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(PLANNED)} source descriptors and the document")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
