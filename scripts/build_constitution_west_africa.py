"""Write the source descriptors, manifests, profiles and docs/constitutions-africa-west-deep.md of the North/West/Central Africa sweep.

python scripts/build_constitution_west_africa.py

The tables are in ``scripts/constitution_west_africa_data.py`` (every publisher examined, read 2026-10-15/16). Each active
publisher gets ``config/sources/<id>.yaml`` (connector ``constitution_docs``, parser ``constitution_text``),
``config/manifests/<id>.yaml`` and, per country, a minimal ``config/jurisdictions/<cc>.yaml`` (unless one exists);
each blocked publisher gets a ``planned`` or ``rejected`` descriptor that the licence gate refuses.
"""

# ruff: noqa: E501 - the generated files quote long lines
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constitution_west_africa_data import ACTIVE, BLOCKED, NOT_FOUND, READ_ON, UA  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GENERIC_STATUTE = "no statute was read on a publisher's page"


class Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, data: str) -> Any:
    style = ">-" if len(data) > 110 and "\n" not in data and '"' not in data[:1] else None
    if style:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


Dumper.add_representer(str, _str)


def dump(data: Any) -> str:
    return yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=120, default_flow_style=False)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def comment(lines: list[str]) -> str:
    out = []
    for line in lines:
        for k, part in enumerate(textwrap.wrap(line, 140, subsequent_indent="       ") or [""]):
            out.append("# " + part)
    return "\n".join(out) + "\n"


def active_source(p: dict[str, Any]) -> None:
    langs = p["langs"]
    ndocs = len(p["docs"])
    header = [
        f"Constitution of {p['country']} from {p['publisher']}. Read on 2026-10-15/16, before any document was fetched:",
        f"  robots.txt - {p['robots']}",
        f"  Terms - {p['terms']}",
        f"  The document - {p['document']}",
    ]
    attribution = (
        f"Constitution of {p['country']}, {p['publisher']}; retrieved {{retrieved_date}}. "
        "Structured and indexed by ULIS; not the authentic text and not endorsed by the publisher."
    )
    reviewed = (
        f"ULIS project (not counsel), {READ_ON}, from the publisher's own pages quoted in the header of this file: robots.txt "
        f"({p['robots']}) and the terms ({p['terms']}). {p['document']} The Constitution is an official legal text; no statute "
        "excluding official texts from copyright was read on the publisher's pages (to verify). Reading: AMBER - the publisher "
        "states no restriction on reusing the text beyond a generic notice; no permission for automated retrieval is stated either."
    )
    source = {
        "schema_version": 1,
        "id": p["id"],
        "name": f"Constitution of {p['country']} - {p['publisher']}",
        "publisher": p["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [p["cc"]],
        "document_kinds": ["constitution"],
        "languages": langs,
        "coverage": f"The Constitution of {p['country']} in {', '.join(langs)} as published by {p['publisher']}: {ndocs} document(s). {p['coverage']}",
        "access": {
            "method": "permitted_web_retrieval",
            "endpoint": f"config/manifests/{p['id']}.yaml",
            "authentication": "none",
        },
        "update_frequency": "Not stated; the retrieval date is the edition date unless the document prints one.",
        "rate_limits": (
            "As recorded under robots.txt in the header; ULIS waits at least 5 s between requests (the Crawl-delay when it is "
            f"longer), identifies itself, caches every download and makes {ndocs + len(p['hosts'])} request(s) (robots.txt and "
            f"{ndocs} document(s))."
        ),
        "licence": {
            "id": f"{p['cc']}-official-text-no-terms",
            "name": f"Official legal text published by {p['publisher']}; no terms of reuse found ({p['terms'][:160]})",
            "url": f"https://{p['hosts'][0]}/",
            "review_status": "approved",
            "reviewed_by": reviewed,
            "reviewed_on": READ_ON,
            "redistribution": "allowed_with_conditions",
            "bulk_download": "allowed_with_conditions",
            "automated_processing": "allowed_with_conditions",
            "commercial_use": "unknown",
            "attribution_required": True,
            "attribution_text": attribution,
            "conditions": [
                f"The text is the publisher's copy ({', '.join(langs)}), not the authentic publication; amendments after the edition date are not applied.",
                "Polite retrieval (identifying User-Agent, 5 s between requests or the Crawl-delay, cached downloads); stop on any refusal.",
            ],
            "notes": "The basis is the official status of the text and the absence of any stated restriction on it; there is no licence. Counsel review is recommended before production use.",
        },
        "provenance": (
            f"The document(s) listed in config/manifests/{p['id']}.yaml, stored byte for byte inside JSON bundles with the URL and "
            "retrieval time; the text is re-derived from the page or from the PDF's text layer (pdftotext)."
        ),
        "known_limitations": [
            "A copy on the publisher's site, not the authentic publication of the constitution; the publisher's own edition date (if any) is the edition date, else the retrieval date.",
            "Amendments adopted after the edition date are not applied; no history is held.",
            *p["limitations"],
        ],
        "status": "active",
        "connector": "constitution_docs",
        "parser": "constitution_text",
    }
    write(ROOT / f"config/sources/{p['id']}.yaml", comment(header) + dump(source))


def manifest(p: dict[str, Any]) -> None:
    docs = []
    for d in p["docs"]:
        doc: dict[str, Any] = {
            "slug": d["slug"],
            "jurisdiction": p["cc"],
            "url": d["url"],
            "format": d["format"],
            "language": d["language"],
            "title": d["title"],
            "citation_name": d["citation_name"],
        }
        if d.get("adopted_on"):
            doc["adopted_on"] = d["adopted_on"]
        doc["edition_label"] = d["edition_label"]
        if d.get("events"):
            doc["events"] = d["events"]
        doc["authoritative"] = False
        doc["text_origin"] = "official_consolidation"
        doc["structure"] = d["structure"]
        docs.append(doc)
    data = {
        "schema_version": 1,
        "source_id": p["id"],
        "user_agent": UA,
        "crawl_delay_seconds": 10 if "Crawl-delay: 10" in p["robots"] or "waits 10 s" in p["robots"] else 5,
        "hosts": p["hosts"],
        "documents": docs,
    }
    head = f"# The Constitution of {p['country']} ({', '.join(p['langs'])}): {len(docs)} document(s) on {', '.join(p['hosts'])}. See config/sources/{p['id']}.yaml.\n"
    write(ROOT / f"config/manifests/{p['id']}.yaml", head + dump(data))


def profile(p: dict[str, Any], first_doc_title: str) -> None:
    path = ROOT / f"config/jurisdictions/{p['cc'].lower()}.yaml"
    if path.exists():
        return
    label = p["docs"][0]["citation_name"]
    langs = p["langs"]
    prof = {
        "schema_version": 1,
        "id": p["cc"],
        "profile_version": "0.1.0",
        "name": p["country"],
        "names": p["names"],
        "kind": "sovereign_state",
        "parent": "INT",
        "fictional": False,
        "verification": {"status": "draft_unverified", "notes": "Research notes only. Not reviewed by counsel."},
        "official_languages": langs,
        "legal_system": {
            "families": [],
            "structure": "unitary",
            "treaty_reception": "unknown",
            "notes": "To verify - not profiled: this is a minimal profile created for the constitutions sweep of North, West and Central Africa. The official languages listed are those of the text loaded.",
        },
        "legislative_institutions": [],
        "instrument_types": ["constitution"],
        "norm_hierarchy": ["constitution"],
        "courts": [],
        "precedent": {
            "doctrine": "not_applicable",
            "vertical_binding": False,
            "notes": "Not profiled: only the constitution is loaded, no court decisions. To verify - how precedent works in this jurisdiction.",
        },
        "citation": {
            "module": "constitution",
            "notes": f'The constitution is cited as "{label} art. N" (a label, with the article number as the document itself prints it). To verify - the country\'s own citation convention.',
            "examples": [f"{label} art. 1", f"{label} art. 10"],
        },
        "publication": {"official_sources": [p["id"]]},
        "coverage": {
            "corpus_status": "pilot",
            "known_gaps": [
                f"Only the constitution is loaded ({p['coverage']}) No legislation, regulations, treaties or case law of {p['country']} are ingested.",
                "The constitution is stored as the text of the publisher's page or PDF at the retrieval date; amendments adopted since are not applied, and no history is held (docs/constitutions-africa-west-deep.md).",
                "Courts, legislative institutions, legal families and treaty reception are not profiled: this is a minimal profile, not reviewed by counsel.",
            ],
        },
    }
    head = (
        f"# REAL JURISDICTION - DRAFT, UNVERIFIED. {p['country']}: a minimal profile created for the constitutions sweep of North, West and\n"
        "# Central Africa. Only the constitution is loaded; nothing else about the legal system is profiled.\n"
    )
    write(path, head + dump(prof))


def blocked_source(b: dict[str, Any]) -> None:
    status = b["status"]
    word = "REJECTED" if status == "rejected" else "PLANNED"
    reviewed = (
        f"ULIS project (not counsel), {READ_ON}, from the publisher's own pages and robots.txt. Findings: {b['robots']}. {b['finding']}"
        + (" It grants no licence to copy, store or republish the text: REJECTED." if status == "rejected" else " Nothing further is requested; no workaround was tried.")
    )
    data = {
        "schema_version": 1,
        "id": b["id"],
        "name": f"Constitution of {b['country']} - {b['publisher']}",
        "publisher": b["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [b["cc"]],
        "document_kinds": ["constitution"],
        "languages": b["langs"],
        "coverage": f"The Constitution of {b['country']} ({b['url']}); not ingested.",
        "access": {"method": "permitted_web_retrieval", "endpoint": b["url"], "authentication": "none"},
        "update_frequency": "unknown",
        "licence": {
            "id": "NOT-GRANTED" if status == "rejected" else "UNKNOWN",
            "name": b["finding"][:220],
            "url": b["url"],
            "review_status": "rejected" if status == "rejected" else "pending_review",
            "reviewed_by": reviewed,
            "reviewed_on": READ_ON,
            "redistribution": "not_allowed" if status == "rejected" else "unknown",
            "bulk_download": "not_allowed" if status == "rejected" else "unknown",
            "automated_processing": "not_allowed" if status == "rejected" else "unknown",
            "commercial_use": "not_allowed" if status == "rejected" else "unknown",
            "attribution_required": None,
            "attribution_text": None,
        },
        "provenance": "Evaluated and NOT ingested by the constitutions sweep (no document was stored). See the licence block and docs/constitutions-africa-west-deep.md.",
        "known_limitations": [b["finding"][:300]],
        "status": status,
        "connector": None,
        "parser": None,
    }
    head = (
        f"# {word} - evaluated {READ_ON}, not ingested. Findings are in licence.reviewed_by (publisher's own pages, robots.txt first).\n"
        f"# Identifying User-Agent used for every request: {UA}\n"
    )
    write(ROOT / f"config/sources/{b['id']}.yaml", head + dump(data))


def doc() -> None:
    lines = [
        "# Constitutions: North, West and Central Africa (deep sweep)",
        "",
        f"Outcome of the deep sweep for 30 states (Algeria, Egypt, Libya, Morocco, Tunisia, Sudan, Mauritania, Senegal, Gambia, Guinea, Guinea-Bissau, Sierra Leone, Liberia, Cote d'Ivoire, Ghana, Togo, Benin, Nigeria, Niger, Mali, Burkina Faso, Cabo Verde, Cameroon, Chad, Central African Republic, Gabon, Congo, DR Congo, Equatorial Guinea, Sao Tome and Principe), read on 2026-10-15/16. Method: a web search (which touches no target site) only to discover candidate official URLs; every request to a target site went through `PoliteHttp` (identifying User-Agent `{UA}`, robots.txt of the host read first and the path checked against it, at least 5 s between requests, the Crawl-delay when longer); the publisher's terms were read before any text was stored. A refusal, a TLS verification failure, a connection reset or a prohibition ended the work on that publisher; nothing was worked around. Research record, not legal advice.",
        "",
        "Shared machinery: connector `constitution_docs`, parser `constitution_text` (the sweep of Africa, the Middle East, Asia and Oceania, docs/constitutions-africa-asia-oceania.md); one source descriptor and one manifest per publisher, one minimal `draft_unverified` profile per country, citation module `constitution` (`<Country> Constitution art. N`). Two backward-compatible additions to the parser for this sweep: headings written `Article 1er` / `1ère` are read as article 1 (French), and the structure hint `columns: N` reads a PDF printed in N columns (an official gazette) strip by strip so that the columns do not interleave.",
        "",
        "Licence reading: AMBER (active; `commercial_use: unknown`, so a commercial deployment is refused by the gate) where the publisher states no restriction beyond a generic footer and the text is clearly the official one; RED (rejected or planned, refused by the gate, `tests/test_constitution_west_africa.py`) where a notice forbids reproduction or access was refused.",
        "",
        "## Result",
        "",
        f"Loaded: {len(ACTIVE)} publishers, {len(ACTIVE)} countries, {sum(len(p['docs']) for p in ACTIVE)} documents (counts of provisions are in the next section). Not loaded: {len(BLOCKED)} publishers examined and refused, unreadable or unreachable, and {len(NOT_FOUND) - 1} countries with no official host found.",
        "",
        "### Loaded",
        "",
        "| Country | Source | Publisher | Languages | Documents | Text |",
        "|---|---|---|---|---|---|",
    ]
    for p in ACTIVE:
        lines.append(f"| {p['country']} | `{p['id']}` | {p['publisher']} | {', '.join(p['langs'])} | {len(p['docs'])} | {p['coverage']} |")
    lines += ["", "### Not loaded", "", "| Country | Source | Outcome | Finding |", "|---|---|---|---|"]
    for b in BLOCKED:
        lines.append(f"| {b['country']} | `{b['id']}` ({b['publisher']}) | {b['status']} | {b['robots']}. {b['finding']} |")
    for n in NOT_FOUND:
        lines.append(f"| {n['country']} | - | not found | {n['finding']} |")
    lines += ["", "## Findings per loaded publisher", "", "The text below is the header of each source descriptor: the robots.txt, the terms and the document as read.", ""]
    for p in ACTIVE:
        lines += [f"### {p['country']} - `{p['id']}`", "", "```text", f"robots.txt - {p['robots']}", f"Terms - {p['terms']}", f"The document - {p['document']}", "```", ""]
    write(ROOT / "docs/constitutions-africa-west-deep.md", "\n".join(lines) + "\n")


def main() -> None:
    for p in ACTIVE:
        active_source(p)
        manifest(p)
        profile(p, p["docs"][0]["title"])
    for b in BLOCKED:
        blocked_source(b)
    doc()
    print(f"{len(ACTIVE)} active, {len(BLOCKED)} blocked")


if __name__ == "__main__":
    main()
