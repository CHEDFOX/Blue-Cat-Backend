"""Polite fetcher for judgments of the Court of Justice and the General Court from Cellar.

Same service and rules as :mod:`ulis.sources.connectors.eu_cellar` (SPARQL endpoint and RESTful
item URLs of the Publications Office's Cellar; identifying User-Agent, at least 5 s between
requests, cache; neither curia.europa.eu nor eur-lex.europa.eu is used - both answer automated
clients with a bot challenge, which ULIS does not try to pass).

For every judgment in the manifest (a CELEX number) and every language the connector runs two
SPARQL queries, then downloads one XHTML file per language:

* the work's metadata: date of the decision, ECLI, document type, deciding court and formation;
* for each language version: the case designation ("Case C-362/14"), the parties, the title and
  the XHTML files.

Cellar sometimes holds two works with the judgment's CELEX number (for example a later
re-publication of the same text) and so lists two XHTML files for one language. All language
versions are taken from the one work that has files in the most requested languages; if that is
still ambiguous the connector stops (see :func:`choose_items`). The result is one *bundle*
per (judgment, language): a JSON envelope with every response used, exactly as received, with
URL, retrieval time and SHA-256 (the same envelope as the legislation connector), plus the
manifest's citation label (``short_name``) for the case.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.eu_cellar import (
    BUNDLE_FORMAT,
    BUNDLE_MEDIA_TYPE,
    CELEX_RE,
    ITEM_RE,
    LANG3,
    LANGUAGE_AUTHORITY,
    PREFIXES,
    CellarConnector,
    _work,
    bundle_part,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

CASE_CELEX_RE = re.compile(r"^6[0-9]{4}(CJ|TJ|CO|TO|CC|CN)[0-9]{4}$")
CASE_PROPERTIES = (
    "cdm:work_date_document",
    "cdm:case-law_ecli",
    "cdm:work_has_resource-type",
    "cdm:resource_legal_id_celex",
    "cdm:work_created_by_agent",
    "cdm:case-law_delivered_by_court-formation",
    "cdm:case-law_uses_procedure_language",
)
CELLAR_ID = re.compile(r"/resource/cellar/([0-9a-f-]{36})\.")


def query_case_metadata(celex: str) -> str:
    """Work-level facts: date, ECLI, resource type, deciding court (corporate body), formation."""
    return (
        PREFIXES
        + "SELECT DISTINCT ?work ?p ?o WHERE {\n"
        + f"  {_work(celex)}\n"
        + "  ?work ?p ?o .\n"
        + f"  FILTER(?p IN ({', '.join(CASE_PROPERTIES)}))\n"
        + "}\nORDER BY ?work ?p ?o\n"
    )


def query_case_items(celex: str, languages: list[str]) -> str:
    """Per language: case designation, parties, title and the XHTML files."""
    langs = ", ".join(f"<{LANGUAGE_AUTHORITY}{LANG3[lang]}>" for lang in languages)
    return (
        PREFIXES
        + "SELECT DISTINCT ?work ?lang ?title ?case ?parties ?m ?item WHERE {\n"
        + f"  {_work(celex)}\n"
        + "  ?e cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language ?lang .\n"
        + f"  FILTER(?lang IN ({langs}))\n"
        + "  OPTIONAL { ?e cdm:expression_title ?title }\n"
        + "  OPTIONAL { ?e cdm:expression_case-law_identifier_case ?case }\n"
        + "  OPTIONAL { ?e cdm:expression_case-law_parties ?parties }\n"
        + "  OPTIONAL {\n"
        + "    ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?mtype .\n"
        + '    FILTER(STR(?mtype) = "xhtml")\n'
        + "    ?item cdm:item_belongs_to_manifestation ?m .\n"
        + "  }\n"
        + "}\nORDER BY ?work ?lang ?m ?item\n"
    )


def choose_items(bindings: list[dict[str, Any]], languages: list[str]) -> dict[str, str]:
    """The XHTML file of each language version, from one Cellar work where possible.

    Cellar may list more than one work with the judgment's CELEX number (for example a later
    re-publication of the same text) and so more than one XHTML file per language. The files of
    the work that has a file in the most requested languages are used (ties: the lowest Cellar
    identifier), so that all language versions come from the same work; for a language that work
    lacks, the only file Cellar lists for it. More than one candidate is an error: nothing is
    guessed.
    """
    by_work: dict[str, dict[str, set[str]]] = {}
    for b in bindings:
        item = b.get("item", {}).get("value")
        m = CELLAR_ID.search(item or "")
        if item and m:
            by_work.setdefault(m.group(1), {}).setdefault(b["lang"]["value"], set()).add(item)
    uris = {lang: LANGUAGE_AUTHORITY + LANG3[lang] for lang in languages}
    ranked = sorted(by_work, key=lambda w: (-sum(1 for u in uris.values() if u in by_work[w]), w))
    out: dict[str, str] = {}
    for lang, uri in uris.items():
        own = by_work[ranked[0]].get(uri, set()) if ranked else set()
        every = {i for files in by_work.values() for i in files.get(uri, set())}
        pick = own or every
        if len(pick) != 1:
            raise FetchError(f"expected one XHTML file for language {lang}, Cellar lists {sorted(pick)}")
        out[lang] = next(iter(pick))
    return out


class CellarCaseLawConnector(CellarConnector):
    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        languages = manifest.get("languages") or []
        if not languages or any(lang not in LANG3 for lang in languages):
            raise ManifestError(f"languages must be EU official languages, got {languages!r}")
        for work in manifest.get("works") or []:
            celex = str(work.get("celex", ""))
            if not CELEX_RE.match(celex) or not CASE_CELEX_RE.match(celex):
                raise ManifestError(f"invalid CELEX number of a judgment {celex!r}")
            if work.get("short_name") is not None and not str(work["short_name"]).strip():
                raise ManifestError(f"{celex}: empty short_name")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        http = self._client(manifest)
        languages: list[str] = manifest["languages"]
        for work in manifest.get("works") or []:
            celex = work["celex"]
            meta_raw = self._sparql(http, celex, "case-metadata", query_case_metadata(celex))
            items_raw = self._sparql(http, celex, "case-items", query_case_items(celex, languages))
            meta_part = bundle_part("sparql:metadata", *meta_raw)
            items_part = bundle_part("sparql:items:xhtml", *items_raw)
            bindings = json.loads(items_raw[0])["results"]["bindings"]
            try:
                chosen = choose_items(bindings, languages)
            except FetchError as exc:
                raise FetchError(f"{celex}: {exc}") from exc
            for lang in languages:
                url = chosen[lang]
                if not ITEM_RE.match(url):
                    raise FetchError(f"{celex}: unexpected item URL {url}")
                got = http.get(url.replace("http://", "https://", 1), headers={"Accept": "*/*"})
                if got is None:
                    raise FetchError(f"{celex} ({lang}): {url} not found")
                parts = [meta_part, items_part, bundle_part("item:xhtml", *got)]
                bundle = {
                    "format": BUNDLE_FORMAT,
                    "version": 1,
                    "kind": "case_law",
                    "celex": celex,
                    "language": lang,
                    "content_format": "xhtml",
                    "short_name": work.get("short_name"),
                    "parts": parts,
                }
                content = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
                retrieved = max(datetime.fromisoformat(p["retrieved_at"]) for p in parts)
                yield RawDocument(
                    external_id=f"cellar-case-law:{celex}:{lang}",
                    uri=url.replace("http://", "https://", 1),
                    media_type=BUNDLE_MEDIA_TYPE,
                    content=content,
                    retrieved_at=retrieved,
                    metadata={
                        "celex": celex,
                        "language": lang,
                        "format": "xhtml",
                        "parts": [{k: p[k] for k in ("role", "url", "sha256", "retrieved_at")} for p in parts],
                    },
                )
