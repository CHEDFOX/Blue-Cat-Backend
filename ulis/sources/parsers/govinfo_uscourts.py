"""Parser for US courts of appeals opinions from GovInfo (collection USCOURTS).

Input (one raw document per opinion, made by ``connectors/govinfo_uscourts.py``): a JSON bundle holding the package MODS
(``https://www.govinfo.gov/metadata/pkg/USCOURTS-<court>-<yy>-<number>/mods.xml``) and the opinion's PDF
(``.../content/pkg/<package>/pdf/<package>-<n>.pdf``), both exactly as received (the PDF base64-encoded).

* Metadata comes from the MODS: case name (``title``), court (``courtCode``), docket (``caseNumber`` and the short form
  in the "preferred citation" identifier), date of the opinion (``dateIssued`` of the granule), parties, nature of
  suit, and the clerk's docket text ("OPINION filed. ... Circuit Judges. per curiam UNPUBLISHED ..."), which is stored
  as it is and never interpreted. The MODS has no reporter citation, so none is recorded.
* The text is the PDF's text layer, extracted by ``pdftotext`` (poppler-utils, which must be installed): one unit per
  PDF page (``num`` is the PDF page index, which is not necessarily the slip opinion's own page number, so the
  citation carries no pinpoint). The court's e-filing banner ("Case: 23-1808 Document: 21 Page: 2 Filed: ...") stays in
  the text. A PDF without a text layer (a scan) is skipped: ULIS does no OCR.

Judicial treatment is not in the source and is never inferred.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import date
from typing import Any

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import (
    ParsedDocument,
    ParsedJudgmentMeta,
    ParsedParagraph,
    ParseSkipped,
)
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-us-govinfo-uscourts/1"
MODS = "{http://www.loc.gov/mods/v3}"

# GovInfo court codes of the courts of appeals -> court ids of the US profile.
COURTS = {
    **{f"ca{n}": f"US-CA{n}" for n in range(1, 12)},
    "ca13": "US-CAFC",
    "caDC": "US-CADC",
}
BANNER = re.compile(r"^(?:Case|Document|Page|Date Filed|Entry ID|Filed|USCA\w*|Appellate Case|Appeal)\b.*$")
MIN_TEXT = 400  # characters of text beyond the e-filing banners: less means the PDF is a scan without text layer

__all__ = [
    "BUNDLE_SCHEMA",
    "COURTS",
    "PARSER_VERSION",
    "UsCourtsParseError",
    "bundle_bytes",
    "parse_govinfo_uscourts",
    "work_uri_for",
]


class UsCourtsParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def work_uri_for(granule: str) -> str:
    return f"/us/case/govinfo/{granule}"


def _clean(page: str) -> str:
    lines = [ln.rstrip() for ln in page.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _has_text(pages: list[str]) -> bool:
    body = [ln for page in pages for ln in page.splitlines() if ln.strip() and not BANNER.match(ln.strip())]
    return sum(len(ln) for ln in body) >= MIN_TEXT


# --------------------------------------------------------------------------- MODS


def _text(el: Any, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_govinfo_uscourts(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsCourtsParseError(f"not a ULIS bundle: {exc}") from exc
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise UsCourtsParseError(f"unexpected bundle schema {bundle.get('schema')!r}")
    package, granule = bundle.get("package"), bundle.get("granule")
    try:
        root = fromstring(bundle["mods"].encode("utf-8"), forbid_dtd=True)
    except Exception as exc:  # defusedxml raises several exception types
        raise UsCourtsParseError(f"the package MODS cannot be read safely: {exc}") from exc
    if root.tag != f"{MODS}mods":
        raise UsCourtsParseError(f"unexpected root element {root.tag!r}")
    package_ext = next((e for e in root.findall(f"{MODS}extension") if _text(e, f"{MODS}docClass") == "USCOURTS"), None)
    if package_ext is None or _text(package_ext, f"{MODS}accessId") != package:
        raise UsCourtsParseError(f"the MODS is not that of package {package!r}")
    item = next(
        (
            i
            for i in root.findall(f"{MODS}relatedItem[@type='constituent']")
            if i.findtext(f"{MODS}extension/{MODS}accessId") == granule
        ),
        None,
    )
    if item is None:
        raise UsCourtsParseError(f"the package lists no granule {granule!r}")
    code = _text(package_ext, f"{MODS}courtCode") or ""
    court_id = COURTS.get(code)
    if court_id is None:
        raise ParseSkipped(f"{granule}: court {code!r} is not a court of appeals modelled in the US profile")
    case_name = _text(item, f"{MODS}titleInfo/{MODS}title") or _text(root, f"{MODS}titleInfo/{MODS}title")
    decided = _date(_text(item, f"{MODS}originInfo/{MODS}dateIssued"))
    if not case_name or decided is None:
        raise ParseSkipped(f"{granule}: the MODS gives no case name or no date of the opinion")
    number = _text(package_ext, f"{MODS}caseNumber")
    preferred = next(
        (i.text.strip() for i in root.findall(f"{MODS}identifier") if i.get("type") == "preferred citation" and i.text),
        None,
    )
    docket = preferred.split(";")[-1].strip() if preferred else number
    version = (
        _date(_text(root, f"{MODS}recordInfo/{MODS}recordChangeDate"))
        or _date(_text(root, f"{MODS}recordInfo/{MODS}recordCreationDate"))
        or _date(_text(root, f"{MODS}extension/{MODS}dateIngested"))
    )
    if version is None:
        raise ParseSkipped(f"{granule}: the MODS gives no record date")

    try:
        pdf = base64.b64decode(bundle["pdf_base64"], validate=True)
    except (KeyError, binascii.Error, ValueError) as exc:
        raise UsCourtsParseError(f"the bundle holds no valid PDF: {exc}") from exc
    try:
        pages = pdf_pages(pdf)
    except PdfTextError as exc:
        raise UsCourtsParseError(str(exc)) from exc
    if not _has_text(pages):
        raise ParseSkipped(f"{granule}: the PDF has no text layer (a scan); ULIS does no OCR")

    parties = [
        {"name": p.get("fullName"), **({"role": p.get("role")} if p.get("role") else {})}
        for p in package_ext.findall(f"{MODS}party")
        if p.get("fullName")
    ]
    ext: dict[str, Any] = {
        "govinfo_package": package,
        "govinfo_granule": granule,
        "court_code": code,
        "pdf_pages": len(pages),
    }
    for key, value in (
        ("case_number", number),
        ("nature_of_suit", _text(package_ext, f"{MODS}natureSuit")),
        ("nature_of_suit_code", _text(package_ext, f"{MODS}natureSuitCode")),
        ("docket_text", _text(item, f"{MODS}extension/{MODS}docketText")),
    ):
        if value:
            ext[key] = value
    if parties:
        ext["parties"] = parties
    paragraphs = [
        ParsedParagraph(eid=f"p{n}", num=str(n), part="opinion", text=text, sort_order=n, refs=[])
        for n, text in ((n, _clean(page)) for n, page in enumerate(pages, start=1))
        if text
    ]
    work_uri = work_uri_for(granule)
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="US",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{version.isoformat()}",
            work_title=case_name,
            title=case_name,
            short_title=None,
            number=docket,
            work_date=decided,
            language="en",
            authoritative=False,  # text extracted from the PDF that GovInfo publishes
            text_origin="official_conversion",
            consolidation_kind="unknown",
            version_date=version,
            currency_date=None,
            extent=["US"],
            ext_module="us",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=case_name,
                neutral_citation=None,
                decision_date=decided,
                docket=docket,
            ),
            paragraphs=paragraphs,
        )
    ]
