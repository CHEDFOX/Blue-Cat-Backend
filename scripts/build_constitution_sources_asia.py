"""Write the source descriptors and the minimal profiles of the deep constitutions sweep for Asia and Oceania.

python scripts/build_constitution_sources_asia.py

The table is ``scripts/constitution_findings_asia_data.py``. Active publishers get a descriptor for the shared
``constitution_docs`` connector (their manifest is ``config/manifests/<id>.yaml``); planned and rejected publishers get a
descriptor with their findings and nothing registered for them. Profiles are written only for countries that have none.
"""

# ruff: noqa: E501 - the descriptors quote the publishers' pages
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constitution_findings_asia_data import ACTIVE, AMBER, PLANNED, PROFILES, READ_ON  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "ULIS project (not counsel)"


def active_yaml(e: dict[str, Any]) -> str:
    manifest = yaml.safe_load((ROOT / "config" / "manifests" / f"{e['id']}.yaml").read_text(encoding="utf-8"))
    documents = manifest["documents"]
    languages = list(dict.fromkeys(d["language"] for d in documents))
    requests = 1 + len(documents)
    reviewed_by = (
        f"{PROJECT}, {READ_ON}, from the publisher's own pages quoted in the header of this file: robots.txt ({e['robots']}) "
        f"and the terms ({e['terms']}). {e['document']}. {e['basis']} {AMBER}"
    )
    header = (
        f"# Constitution of {e['country']} from {e['publisher']}. Read on {READ_ON}, before any document was fetched:\n"
        f"#   robots.txt - {e['robots']}\n"
        f"#   Terms - {e['terms']}\n"
        f"#   The document - {e['document']}\n"
    )
    doc = {
        "schema_version": 1,
        "id": e["id"],
        "name": f"Constitution of {e['country']} - {e['short']} ({e['host']})",
        "publisher": e["publisher"],
        "kind": "official_legislation_portal",
        "authority": "official_non_authentic",
        "jurisdictions": [e["cc"]],
        "document_kinds": ["constitution"],
        "languages": languages,
        "coverage": e["coverage"],
        "access": {
            "method": "permitted_web_retrieval",
            "endpoint": f"config/manifests/{e['id']}.yaml",
            "authentication": "none",
        },
        "update_frequency": "Not stated; the retrieval date is the edition date unless the document prints one.",
        "rate_limits": (
            f"As recorded under robots.txt in the header; ULIS waits at least 5 s between requests, identifies itself, caches "
            f"every download and makes {requests} request(s) (robots.txt and {len(documents)} document(s))."
        ),
        "licence": {
            "id": f"{e['cc']}-official-text-no-terms",
            "name": e["licence_name"],
            "url": e["url"],
            "review_status": "approved",
            "reviewed_by": reviewed_by,
            "reviewed_on": READ_ON,
            "redistribution": "allowed_with_conditions",
            "bulk_download": "allowed_with_conditions",
            "automated_processing": "allowed_with_conditions",
            "commercial_use": "unknown",
            "attribution_required": True,
            "attribution_text": (
                f"Constitution of {e['country']}, {e['short']} ({e['host']}); retrieved {{retrieved_date}}. "
                "Structured and indexed by ULIS; not the authentic text and not endorsed by the publisher."
            ),
            "conditions": [
                "The text is the publisher's copy, not the authentic publication; amendments after the edition date are not applied.",
                "Polite retrieval (identifying User-Agent, 5 s between requests, cached downloads); stop on any refusal.",
            ],
            "notes": (
                "The basis is the official status of the text and the absence of any stated restriction on it; there is no "
                "licence. Counsel review is recommended before production use."
            ),
        },
        "provenance": (
            f"The document(s) listed in config/manifests/{e['id']}.yaml, stored byte for byte inside JSON bundles with the URL and "
            "retrieval time; the text is re-derived from the page or from the PDF's text layer (pdftotext)."
        ),
        "known_limitations": [
            "A copy on the publisher's site, not the authentic publication of the constitution; the publisher's own edition date "
            "(if any) is the edition date, else the retrieval date.",
            "Amendments adopted after the edition date are not applied; no history is held.",
            *e["limitations"],
        ],
        "status": "active",
        "connector": "constitution_docs",
        "parser": "constitution_text",
    }
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def planned_yaml(e: dict[str, Any]) -> str:
    rejected = e["status"] == "rejected"
    permission = "not_allowed" if rejected else "unknown"
    reviewed_by = (
        f"{PROJECT}, {READ_ON}. READ - robots.txt: {e['robots']} Terms or notice: {e['terms']} Document: {e['document']} "
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
        "access": {"method": "permitted_web_retrieval", "endpoint": e["url"], "authentication": None},
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
                "Not ingested. The source can be activated only when the publisher's own pages state terms that allow reuse, or a "
                "statute excluding legislative texts from copyright is read and quoted (the project's earlier AMBER approvals).",
            ],
            "notes": e.get("note")
            or "Recorded by the constitutions sweep; the finding above is what the pages said when read.",
        },
        "provenance": f"No document was stored. The pages read on {READ_ON} are listed in licence.reviewed_by.",
        "known_limitations": ["Nothing is ingested."] + ([e["note"]] if e.get("note") else []),
        "status": e["status"],
    }
    header = (
        f"# {e['country']} - {e['doc_title']}: {e['status'].upper()}, nothing ingested. Generated by "
        "scripts/build_constitution_sources_asia.py\n"
        f"# from scripts/constitution_findings_asia_data.py (read {READ_ON}); edit the table, not this file.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def profile_yaml(e: dict[str, Any]) -> str:
    p = PROFILES[e["cc"]]
    doc = {
        "schema_version": 1,
        "id": e["cc"],
        "profile_version": "0.1.0",
        "name": p["name"],
        "names": p["names"],
        "kind": "sovereign_state",
        "parent": "INT",
        "fictional": False,
        "verification": {"status": "draft_unverified", "notes": "Research notes only. Not reviewed by counsel."},
        "official_languages": p["languages"],
        "legal_system": {
            "families": [],
            "structure": p["structure"],
            "treaty_reception": "unknown",
            "notes": p["notes"],
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
            "notes": (
                f'The constitution is cited as "{p["name"]} Constitution" followed by the number the document itself gives the '
                "provision (a label, not the country's own convention). To verify - the country's own citation convention."
            ),
            "examples": p["examples"],
        },
        "publication": {"official_sources": [e["id"]]},
        "coverage": {
            "corpus_status": "pilot",
            "known_gaps": [
                f"Only the constitution is loaded ({p['loaded']}). No legislation, regulations, treaties or case law of "
                f"{p['name']} are ingested.",
                "The constitution is stored as the text of the publisher's page or PDF at the retrieval date; amendments adopted since "
                "are not applied, and no history is held (docs/constitutions-asia-oceania-deep.md).",
                "Courts, legislative institutions, legal families and treaty reception are not profiled: this is a minimal profile, "
                "not reviewed by counsel.",
            ],
        },
    }
    header = (
        f"# REAL JURISDICTION - DRAFT, UNVERIFIED. {p['name']}: a minimal profile created for the deep constitutions sweep of Asia and\n"
        "# Oceania. Only the constitution is loaded; nothing else about the legal system is profiled. Generated by\n"
        "# scripts/build_constitution_sources_asia.py.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def main() -> int:
    sources = ROOT / "config" / "sources"
    profiles = ROOT / "config" / "jurisdictions"
    written = 0
    for e in ACTIVE:
        (sources / f"{e['id']}.yaml").write_text(active_yaml(e), encoding="utf-8")
        target = profiles / f"{e['cc'].lower()}.yaml"
        if not target.exists():
            target.write_text(profile_yaml(e), encoding="utf-8")
        written += 1
    for e in PLANNED:
        (sources / f"{e['id']}.yaml").write_text(planned_yaml(e), encoding="utf-8")
        written += 1
    print(f"wrote {written} source descriptors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
