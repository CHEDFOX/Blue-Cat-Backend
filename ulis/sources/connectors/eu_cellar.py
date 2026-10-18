"""Polite fetcher for EU law from Cellar, the Publications Office repository behind EUR-Lex.

eur-lex.europa.eu answers automated clients with a bot challenge (HTTP 202 with
``x-amzn-waf-action: challenge``); ULIS does not try to pass it. The Publications Office offers
the same documents and their metadata to re-users through Cellar (dataset "Sparql and RESTful
API access to European Union law and publications (CELLAR)" on data.europa.eu, licensed under
the Commission reuse notice, Decision 2011/833/EU):

* the SPARQL endpoint ``https://publications.europa.eu/webapi/rdf/sparql`` for metadata (dates
  and their kind, document type, in-force flag, amendments and corrigenda, the files of each
  language version), and
* the RESTful item URLs ``https://publications.europa.eu/resource/cellar/<id>/DOC_<n>`` for the
  files themselves (Formex 4 XML, a Formex zip, OJ XHTML, or the legacy EUR-Lex HTML of the acts
  that have no Formex 4 file; manifest ``format``: ``fmx4``, ``xhtml``, ``html``).

For every work in the manifest the connector runs three SPARQL queries, then downloads the
files of the requested format in each requested language, all through :class:`PoliteHttp`
(identifying User-Agent, at least 5 s between requests, back-off, refusals respected, cache).
For each (work, language) it yields one *bundle*: a JSON envelope holding every response used
exactly as received (UTF-8 text, or base64 for binary), each with its URL, retrieval time and
SHA-256. The parser therefore sees the metadata and the text together, and every byte stays
traceable to the request that produced it.

A manifest of thousands of acts (``scripts/build_eu_manifest.py``) sets ``sparql_batch``: the three
SPARQL queries then cover that many works at once (``VALUES``), and each work's bundle holds only the
rows that concern it, as a part that records the SHA-256 of the full response it was cut from. A work
may name its own ``languages`` (the bulk of the manifest is English only; the core acts keep English,
French and German). A *consolidated version* (CELEX ``0YYYYTNNNN-YYYYMMDD``: the text with the
amendments applied up to a date, which Cellar distributes in Formex 4 like the Official Journal) is
listed with the CELEX of its ``base`` act; its bundle carries the base act's metadata too and no list
of changes (the amendments are already in the text).

Per-work problems (no file in a language, a file that is not there, a query the endpoint cannot answer
for one work) are reported in ``fetch_errors`` and the run continues; a refusal (403, 429) stops it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "publications.europa.eu"
SPARQL_URL = f"https://{HOST}/webapi/rdf/sparql"
BUNDLE_MEDIA_TYPE = "application/vnd.ulis.cellar-bundle+json"
BUNDLE_FORMAT = "ulis-cellar-bundle"
LANGUAGE_AUTHORITY = "http://publications.europa.eu/resource/authority/language/"
# BCP 47 -> Publications Office language authority code (the 24 official languages).
LANG3 = {
    "bg": "BUL",
    "cs": "CES",
    "da": "DAN",
    "de": "DEU",
    "el": "ELL",
    "en": "ENG",
    "es": "SPA",
    "et": "EST",
    "fi": "FIN",
    "fr": "FRA",
    "ga": "GLE",
    "hr": "HRV",
    "hu": "HUN",
    "it": "ITA",
    "lt": "LIT",
    "lv": "LAV",
    "mt": "MLT",
    "nl": "NLD",
    "pl": "POL",
    "pt": "POR",
    "ro": "RON",
    "sk": "SLK",
    "sl": "SLV",
    "sv": "SWE",
}
FORMATS = {"fmx4", "xhtml", "html"}
CELEX_RE = re.compile(r"^[0-9CE][0-9]{4}[A-Z]{1,2}[0-9A-Z/()\-]{1,20}$")
# A consolidated version of an act: sector 0, the act's year, type and number, and the date it applies from.
CONSOLIDATED_RE = re.compile(r"^0[0-9]{4}[A-Z]{1,2}[0-9]{4}-[0-9]{8}$")
MAX_BATCH = 50
ITEM_RE = re.compile(r"^https?://publications\.europa\.eu/resource/cellar/[0-9a-f-]{36}\.\d{4}\.\d{2}/DOC_\d{1,4}$")

PREFIXES = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX ann: <http://publications.europa.eu/ontology/annotation#>\n"
    "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)
DATE_PROPERTIES = (
    "cdm:work_date_document",
    "cdm:resource_legal_date_signature",
    "cdm:resource_legal_date_entry-into-force",
    "cdm:work_date_entry-into-force",
    "cdm:resource_legal_date_end-of-validity",
    "cdm:directive_date_transposition",
    "cdm:date_creation_legacy",
)
WORK_PROPERTIES = (
    *DATE_PROPERTIES,
    "cdm:resource_legal_in-force",
    "cdm:resource_legal_eli",
    "cdm:work_has_resource-type",
    "cdm:resource_legal_id_celex",
    "cdm:case-law_ecli",
)
CHANGE_RELATIONS = (
    "cdm:resource_legal_amends_resource_legal",
    "cdm:resource_legal_corrects_resource_legal",
    "cdm:resource_legal_repeals_resource_legal",
    "cdm:resource_legal_implicitly_repeals_resource_legal",
)
CHANGE_ANNOTATIONS = (
    "ann:reference_to_modified_location",
    "ann:start_of_validity",
    "ann:language_list",
    "ann:role2",
)


def _work(celex: str) -> str:
    return f'?work cdm:resource_legal_id_celex "{celex}"^^xsd:string .'


def query_metadata(celex: str) -> str:
    """Work-level facts and the kind of each date (entry into force, application, ...)."""
    return (
        PREFIXES
        + "SELECT DISTINCT ?work ?p ?o ?ap ?av WHERE {\n"
        + f"  {_work(celex)}\n"
        + "  ?work ?p ?o .\n"
        + f"  FILTER(?p IN ({', '.join(WORK_PROPERTIES)}))\n"
        + "  OPTIONAL {\n"
        + "    ?ax owl:annotatedSource ?work ; owl:annotatedProperty ?p ; owl:annotatedTarget ?o ; ?ap ?av .\n"
        + "    FILTER(?ap IN (ann:type_of_date, ann:comment_on_date))\n"
        + "  }\n"
        + "}\nORDER BY ?work ?p ?o ?ap ?av\n"
    )


def query_changes(celex: str, languages: list[str]) -> str:
    """Acts that amend, correct or repeal the work, with the source's annotations and titles."""
    langs = ", ".join(f"<{LANGUAGE_AUTHORITY}{LANG3[lang]}>" for lang in languages)
    return (
        PREFIXES
        + "SELECT DISTINCT ?x ?rel ?celex ?date ?ax ?ap ?av ?lang ?title WHERE {\n"
        + f"  {_work(celex)}\n"
        + "  ?x ?rel ?work .\n"
        + f"  FILTER(?rel IN ({', '.join(CHANGE_RELATIONS)}))\n"
        + "  OPTIONAL { ?x cdm:resource_legal_id_celex ?celex }\n"
        + "  OPTIONAL { ?x cdm:work_date_document ?date }\n"
        + "  OPTIONAL {\n"
        + "    ?ax owl:annotatedSource ?x ; owl:annotatedProperty ?rel ; owl:annotatedTarget ?work ; ?ap ?av .\n"
        + f"    FILTER(?ap IN ({', '.join(CHANGE_ANNOTATIONS)}))\n"
        + "  }\n"
        + "  OPTIONAL {\n"
        + "    ?e cdm:expression_belongs_to_work ?x ; cdm:expression_uses_language ?lang ;"
        + " cdm:expression_title ?title .\n"
        + f"    FILTER(?lang IN ({langs}))\n"
        + "  }\n"
        + "}\nORDER BY ?x ?rel ?ax ?ap ?av ?lang\n"
    )


def query_items(celex: str, languages: list[str], fmt: str) -> str:
    """Each language version's title and the files of the requested format."""
    langs = ", ".join(f"<{LANGUAGE_AUTHORITY}{LANG3[lang]}>" for lang in languages)
    return (
        PREFIXES
        + "SELECT DISTINCT ?work ?lang ?title ?m ?item WHERE {\n"
        + f"  {_work(celex)}\n"
        + "  ?e cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language ?lang .\n"
        + f"  FILTER(?lang IN ({langs}))\n"
        + "  OPTIONAL { ?e cdm:expression_title ?title }\n"
        + "  OPTIONAL {\n"
        + "    ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?mtype .\n"
        + f'    FILTER(STR(?mtype) = "{fmt}")\n'
        + "    ?item cdm:item_belongs_to_manifestation ?m .\n"
        + "  }\n"
        + "}\nORDER BY ?work ?lang ?m ?item\n"
    )


def _values(celexes: list[str], var: str = "celex") -> str:
    """``VALUES`` block for a batch of works; every CELEX was validated against :data:`CELEX_RE`."""
    if any(not CELEX_RE.match(c) for c in celexes):
        raise ManifestError("invalid CELEX number in a batch")
    listed = " ".join(f'"{c}"^^xsd:string' for c in celexes)
    return f"  VALUES ?{var} {{ {listed} }}\n  ?work cdm:resource_legal_id_celex ?{var} .\n"


def query_metadata_batch(celexes: list[str]) -> str:
    """:func:`query_metadata` for several works; each row names its work's CELEX."""
    return (
        PREFIXES
        + "SELECT DISTINCT ?celex ?work ?p ?o ?ap ?av WHERE {\n"
        + _values(celexes)
        + "  ?work ?p ?o .\n"
        + f"  FILTER(?p IN ({', '.join(WORK_PROPERTIES)}))\n"
        + "  OPTIONAL {\n"
        + "    ?ax owl:annotatedSource ?work ; owl:annotatedProperty ?p ; owl:annotatedTarget ?o ; ?ap ?av .\n"
        + "    FILTER(?ap IN (ann:type_of_date, ann:comment_on_date))\n"
        + "  }\n"
        + "}\nORDER BY ?celex ?work ?p ?o ?ap ?av\n"
    )


def query_changes_batch(celexes: list[str], languages: list[str]) -> str:
    """:func:`query_changes` for several works; each row names the changed work's CELEX as ``?base``."""
    langs = ", ".join(f"<{LANGUAGE_AUTHORITY}{LANG3[lang]}>" for lang in languages)
    return (
        PREFIXES
        + "SELECT DISTINCT ?base ?x ?rel ?celex ?date ?ax ?ap ?av ?lang ?title WHERE {\n"
        + _values(celexes, "base")
        + "  ?x ?rel ?work .\n"
        + f"  FILTER(?rel IN ({', '.join(CHANGE_RELATIONS)}))\n"
        + "  OPTIONAL { ?x cdm:resource_legal_id_celex ?celex }\n"
        + "  OPTIONAL { ?x cdm:work_date_document ?date }\n"
        + "  OPTIONAL {\n"
        + "    ?ax owl:annotatedSource ?x ; owl:annotatedProperty ?rel ; owl:annotatedTarget ?work ; ?ap ?av .\n"
        + f"    FILTER(?ap IN ({', '.join(CHANGE_ANNOTATIONS)}))\n"
        + "  }\n"
        + "  OPTIONAL {\n"
        + "    ?e cdm:expression_belongs_to_work ?x ; cdm:expression_uses_language ?lang ;"
        + " cdm:expression_title ?title .\n"
        + f"    FILTER(?lang IN ({langs}))\n"
        + "  }\n"
        + "}\nORDER BY ?base ?x ?rel ?ax ?ap ?av ?lang\n"
    )


def query_items_batch(celexes: list[str], languages: list[str], fmt: str) -> str:
    """:func:`query_items` for several works; each row names its work's CELEX."""
    langs = ", ".join(f"<{LANGUAGE_AUTHORITY}{LANG3[lang]}>" for lang in languages)
    return (
        PREFIXES
        + "SELECT DISTINCT ?celex ?work ?lang ?title ?m ?item WHERE {\n"
        + _values(celexes)
        + "  ?e cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language ?lang .\n"
        + f"  FILTER(?lang IN ({langs}))\n"
        + "  OPTIONAL { ?e cdm:expression_title ?title }\n"
        + "  OPTIONAL {\n"
        + "    ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?mtype .\n"
        + f'    FILTER(STR(?mtype) = "{fmt}")\n'
        + "    ?item cdm:item_belongs_to_manifestation ?m .\n"
        + "  }\n"
        + "}\nORDER BY ?celex ?lang ?m ?item\n"
    )


def split_rows(data: bytes, key: str) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """Group the bindings of a SPARQL JSON answer by the value of one variable."""
    answer = json.loads(data)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in answer["results"]["bindings"]:
        grouped.setdefault(row[key]["value"], []).append(row)
    return answer["head"].get("vars", []), grouped


def derived_response(variables: list[str], rows: list[dict[str, Any]]) -> bytes:
    """A SPARQL JSON answer holding only the given rows (the parser reads it like a whole answer)."""
    body = {"head": {"vars": variables}, "results": {"bindings": rows}}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _item_order(url: str) -> tuple[str, int]:
    base, _, num = url.rpartition("/DOC_")
    return base, int(num)


def bundle_part(role: str, data: bytes, meta: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """One response, exactly as received: UTF-8 text when it decodes losslessly, else base64.
    ``extra`` adds keys that say what the part is (a batched answer cut down to one work: the SHA-256 of the full
    answer and the selection that was made).

    Images (e.g. scanned forms in annexes) carry no text the parser could use: they are listed
    with their URL, type and SHA-256 but their bytes are omitted from the bundle.
    """
    if (meta.get("content_type") or "").startswith("image/"):
        return {
            "role": role.replace("item:", "omitted:", 1),
            "url": meta.get("url"),
            "retrieved_at": meta.get("retrieved_at"),
            "content_type": meta.get("content_type"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "encoding": "omitted",
            "content": "",
        }
    try:
        text = data.decode("utf-8")
        encoding, content = ("utf-8", text) if text.encode("utf-8") == data else ("base64", "")
    except UnicodeDecodeError:
        encoding, content = "base64", ""
    if encoding == "base64":
        content = base64.b64encode(data).decode("ascii")
    return {
        "role": role,
        "url": meta.get("url"),
        "retrieved_at": meta.get("retrieved_at"),
        "content_type": meta.get("content_type"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encoding": encoding,
        "content": content,
        **(extra or {}),
    }


class CellarConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.http: PoliteHttp | None = None
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        languages = manifest.get("languages") or []
        if not languages or any(lang not in LANG3 for lang in languages):
            raise ManifestError(f"languages must be EU official languages, got {languages!r}")
        batch = manifest.get("sparql_batch", 1)
        if not isinstance(batch, int) or not 1 <= batch <= MAX_BATCH:
            raise ManifestError(f"sparql_batch must be an integer from 1 to {MAX_BATCH}")
        if float(manifest.get("crawl_delay_seconds", 5)) < 5:
            raise ManifestError("crawl_delay_seconds must be at least 5")
        seen: set[str] = set()
        for work in manifest.get("works") or []:
            celex = str(work.get("celex", ""))
            if not CELEX_RE.match(celex) or celex in seen:
                raise ManifestError(f"invalid or repeated CELEX number {work.get('celex')!r}")
            seen.add(celex)
            if work.get("format") not in FORMATS:
                raise ManifestError(f"{work['celex']}: format must be one of {sorted(FORMATS)}")
            own = work.get("languages")
            if own is not None and (not own or any(lang not in LANG3 for lang in own)):
                raise ManifestError(f"{celex}: languages must be EU official languages, got {own!r}")
            base = work.get("base")
            if CONSOLIDATED_RE.match(celex):
                if not base or not CELEX_RE.match(str(base)) or CONSOLIDATED_RE.match(str(base)):
                    raise ManifestError(f"{celex}: a consolidated version needs the CELEX number of its base act")
                if work["format"] != "fmx4":
                    raise ManifestError(f"{celex}: consolidated versions are read from Formex 4")
            elif base is not None:
                raise ManifestError(f"{celex}: only a consolidated version has a base act")
        return manifest

    def _client(self, manifest: dict[str, Any]) -> PoliteHttp:
        if self.http is None:
            self.http = PoliteHttp(
                self.cache_dir,
                manifest["user_agent"],
                allowed_hosts={HOST},
                crawl_delay=max(5.0, float(manifest.get("crawl_delay_seconds", 5))),
                offline=self.offline,
                sleep=self._sleep,
            )
        return self.http

    def _sparql(self, http: PoliteHttp, celex: str, name: str, query: str) -> tuple[bytes, dict[str, Any]]:
        url = SPARQL_URL + "?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})
        # The query's hash is part of the cache key, so a changed query is never answered from cache.
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
        key = "webapi/rdf/sparql/" + re.sub(r"[^0-9A-Za-z]", "_", celex) + f"/{name}-{digest}.json"
        got = http.get(url, cache_key=key, headers={"Accept": "application/sparql-results+json"})
        if got is None:
            raise FetchError(f"{celex}: SPARQL endpoint not found")
        return got

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        if manifest.get("sparql_batch", 1) > 1:
            yield from self._fetch_batched(manifest)
            return
        http = self._client(manifest)
        languages: list[str] = manifest["languages"]
        for work in manifest.get("works") or []:
            celex, fmt = work["celex"], work["format"]
            meta_part = bundle_part("sparql:metadata", *self._sparql(http, celex, "metadata", query_metadata(celex)))
            changes_part = bundle_part(
                "sparql:changes", *self._sparql(http, celex, "changes", query_changes(celex, languages))
            )
            items_raw = self._sparql(http, celex, f"items-{fmt}", query_items(celex, languages, fmt))
            items_part = bundle_part(f"sparql:items:{fmt}", *items_raw)
            bindings = json.loads(items_raw[0])["results"]["bindings"]
            for lang in languages:
                lang_uri = LANGUAGE_AUTHORITY + LANG3[lang]
                urls = sorted(
                    {b["item"]["value"] for b in bindings if b["lang"]["value"] == lang_uri and "item" in b},
                    key=_item_order,
                )
                parts = [meta_part, changes_part, items_part]
                for url in urls:
                    if not ITEM_RE.match(url):
                        raise FetchError(f"{celex}: unexpected item URL {url}")
                    got = http.get(url.replace("http://", "https://", 1), headers={"Accept": "*/*"})
                    if got is None:
                        raise FetchError(f"{celex} ({lang}): {url} not found")
                    parts.append(bundle_part(f"item:{fmt}", *got))
                bundle = {
                    "format": BUNDLE_FORMAT,
                    "version": 1,
                    "celex": celex,
                    "language": lang,
                    "content_format": fmt,
                    "parts": parts,
                }
                content = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
                retrieved = max(datetime.fromisoformat(p["retrieved_at"]) for p in parts)
                yield RawDocument(
                    external_id=f"cellar:{celex}:{lang}",
                    uri=(urls[0].replace("http://", "https://", 1) if urls else SPARQL_URL),
                    media_type=BUNDLE_MEDIA_TYPE,
                    content=content,
                    retrieved_at=retrieved,
                    metadata={
                        "celex": celex,
                        "language": lang,
                        "format": fmt,
                        "parts": [{k: p[k] for k in ("role", "url", "sha256", "retrieved_at")} for p in parts],
                    },
                )

    # ------------------------------------------------------------------ batched manifests

    def _sparql_batch(self, http: PoliteHttp, name: str, query: str) -> tuple[bytes, dict[str, Any]] | None:
        """One batched answer, or None when the endpoint cannot answer it (a server error: the caller splits the batch).
        A refusal (403, 429 ...) is not an error of the batch and stops the run."""
        url = SPARQL_URL + "?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        try:
            got = http.get(
                url,
                cache_key=f"webapi/rdf/sparql/batch/{name}-{digest}.json",
                headers={"Accept": "application/sparql-results+json"},
            )
        except FetchError as exc:
            if re.search(r"HTTP 5\d\d\b", str(exc)):
                self.fetch_errors.append(f"SPARQL {name}: {str(exc)[-120:]}; the batch is split")
                return None
            raise
        if got is None:
            raise FetchError(f"SPARQL endpoint not found ({name})")
        return got

    def _fetch_batched(self, manifest: dict[str, Any]) -> Iterator[RawDocument]:
        http = self._client(manifest)
        works: list[dict[str, Any]] = manifest.get("works") or []
        size: int = manifest["sparql_batch"]
        for start in range(0, len(works), size):
            yield from self._fetch_chunk(http, manifest, works[start : start + size])

    def _fetch_chunk(
        self, http: PoliteHttp, manifest: dict[str, Any], chunk: list[dict[str, Any]]
    ) -> Iterator[RawDocument]:
        celexes = [w["celex"] for w in chunk]
        bases = [w["base"] for w in chunk if w.get("base")]
        wanted = [*celexes, *(b for b in bases if b not in celexes)]
        langs_of = {w["celex"]: list(w.get("languages") or manifest["languages"]) for w in chunk}
        union = sorted({lang for langs in langs_of.values() for lang in langs})
        plain = [c for c in celexes if not CONSOLIDATED_RE.match(c)]

        def split() -> Iterator[RawDocument]:
            """The endpoint could not answer for this batch: ask for each half (a single work is skipped)."""
            if len(chunk) == 1:
                self.fetch_errors.append(f"{celexes[0]}: the SPARQL endpoint could not answer for this work; skipped")
                return
            half = len(chunk) // 2
            yield from self._fetch_chunk(http, manifest, chunk[:half])
            yield from self._fetch_chunk(http, manifest, chunk[half:])

        metadata = self._sparql_batch(http, "metadata", query_metadata_batch(wanted))
        if metadata is None:
            yield from split()
            return
        changes: tuple[bytes, dict[str, Any]] = (b"", {})
        if plain:
            answer = self._sparql_batch(http, "changes", query_changes_batch(plain, union))
            if answer is None:
                yield from split()
                return
            changes = answer
        items: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for fmt in sorted({w["format"] for w in chunk}):
            of_format = [w["celex"] for w in chunk if w["format"] == fmt]
            got = self._sparql_batch(http, f"items-{fmt}", query_items_batch(of_format, union, fmt))
            if got is None:
                yield from split()
                return
            items[fmt] = got

        meta_vars, meta_rows = split_rows(metadata[0], "celex")
        change_vars, change_rows = split_rows(changes[0], "base") if plain else ([], {})
        item_split = {fmt: split_rows(got[0], "celex") for fmt, got in items.items()}

        def derived(
            role: str, full: tuple[bytes, dict[str, Any]], variables: list[str], rows: list[dict[str, Any]], key: str
        ):  # type: ignore[no-untyped-def]
            return bundle_part(
                role,
                derived_response(variables, rows),
                full[1],
                {"derived_from_sha256": hashlib.sha256(full[0]).hexdigest(), "selection": key},
            )

        for work in chunk:
            celex, fmt = work["celex"], work["format"]
            if celex not in meta_rows:
                self.fetch_errors.append(f"{celex}: Cellar has no metadata for this CELEX number; skipped")
                continue
            base = work.get("base")
            if base and base not in meta_rows:
                self.fetch_errors.append(f"{celex}: Cellar has no metadata for its base act {base}; skipped")
                continue
            head = [derived("sparql:metadata", metadata, meta_vars, meta_rows[celex], f"celex = {celex}")]
            if base:
                head.append(derived("sparql:metadata:base", metadata, meta_vars, meta_rows[base], f"celex = {base}"))
            else:
                head.append(
                    derived("sparql:changes", changes, change_vars, change_rows.get(celex, []), f"base = {celex}")
                )
            item_vars, item_rows = item_split[fmt]
            rows = item_rows.get(celex, [])
            head.append(derived(f"sparql:items:{fmt}", items[fmt], item_vars, rows, f"celex = {celex}"))
            for lang in langs_of[celex]:
                lang_uri = LANGUAGE_AUTHORITY + LANG3[lang]
                urls = sorted(
                    {r["item"]["value"] for r in rows if r["lang"]["value"] == lang_uri and "item" in r},
                    key=_item_order,
                )
                if not urls:
                    self.fetch_errors.append(f"{celex} ({lang}): Cellar lists no {fmt} file in this language; skipped")
                    continue
                parts = list(head)
                failed = False
                for url in urls:
                    if not ITEM_RE.match(url):
                        self.fetch_errors.append(f"{celex} ({lang}): unexpected item URL {url}; skipped")
                        failed = True
                        break
                    got = http.get(url.replace("http://", "https://", 1), headers={"Accept": "*/*"})
                    if got is None:
                        self.fetch_errors.append(f"{celex} ({lang}): {url} not found (404); skipped")
                        failed = True
                        break
                    parts.append(bundle_part(f"item:{fmt}", *got))
                if failed:
                    continue
                bundle: dict[str, Any] = {
                    "format": BUNDLE_FORMAT,
                    "version": 1,
                    "celex": celex,
                    "language": lang,
                    "content_format": fmt,
                    "parts": parts,
                }
                if base:
                    bundle["base_celex"] = base
                content = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
                yield RawDocument(
                    external_id=f"cellar:{celex}:{lang}",
                    uri=urls[0].replace("http://", "https://", 1),
                    media_type=BUNDLE_MEDIA_TYPE,
                    content=content,
                    retrieved_at=max(datetime.fromisoformat(p["retrieved_at"]) for p in parts),
                    metadata={
                        "celex": celex,
                        "language": lang,
                        "format": fmt,
                        "base": base,
                        "parts": [{k: p[k] for k in ("role", "url", "sha256", "retrieved_at")} for p in parts],
                    },
                )
