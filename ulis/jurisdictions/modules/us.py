"""United States (federal) module: citation formats and extension schema.

Citation forms follow common US practice (Bluebook-style) for the document types ingested:

* public law (as enacted): ``Short Title, Pub. L. No. 115-6, § 2(1), 131 Stat. 11 (2017)``; a private law:
  ``Priv. L. No. 85-1, 71 Stat. A1 (1957)``-style (``Priv. L. No.``)
* act of the Statutes at Large without a public law number (before the 57th Congress, 1901): the Bluebook form
  ``Act of Jan. 13, 1902, ch. 2, § 1, 32 Stat. 1``; a named act keeps its name in front
* regulation (a final rule in the Federal Register): ``91 Fed. Reg. 65,077 (Oct. 14, 2026)``, with the part of the rule
  after a comma for a provision (``, summary``, ``, dates``, ``, regulatory text``)
* regulation (CFR annual edition): ``27 C.F.R. § 478.11(b) (2018)``; the year is the year of the edition's revision
  date, which is the first day of a quarter (January 1 for titles 1-16, April 1 for 17-27, July 1 for 28-41, October 1
  for 42-50). A version of the same part dated any other day is a file of the eCFR, which is not an edition, and is
  cited without a year, whichever source created the instrument
* regulation (the eCFR, the Federal Register Office's continuously updated compilation): ``12 CFR 220.1(b)(3)(ii)``,
  ``12 CFR part 220, appendix A`` - the Office of the Federal Register's own form; no year, because the eCFR is not a
  printed edition (the date of the file is carried in the version data)
* bill or resolution (one stage): ``H.R. 1000, 116th Cong. § 2(a) (2019)``
* United States Code title (a release point of the Office of the Law Revision Counsel):
  ``18 U.S.C. § 1030(a)(2)``; the year is omitted because the online release points are not
  printed editions, and the release point is carried in the version data

* judgment of the Supreme Court (United States Reports, the page is the pinpoint):
  ``Roe v. Wade, 410 U.S. 113, 153 (1973)``
* judgment of a court of appeals (slip opinion, docket number as the source gives it):
  ``Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)``; no pinpoint, because the PDF page is not the slip opinion's
  own page number

* judgment from CourtListener's bulk files (any court): the reporter citation the source gives, the court and the
  year in the parenthetical, a pinpoint only when the units are the printed pages of that very citation:
  ``State v. Reams, 945 P.2d 52, 53 (Mont. 1997)``; a neutral citation stands alone (``State v. Doe, 2019 ND 15``); a
  Westlaw or Lexis number takes the full date (``Doe v. Roe, 2019 WL 5 (9th Cir. Sept. 22, 2019)``); with no citation at
  all the docket number and the date (``Doe v. Roe, No. 99-1234 (9th Cir. Sept. 22, 1997)``)

They are formatting conventions for research output, reviewed by no one yet; a reviewer
sign-off is required before the US profile can be marked "reviewed".
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
    subdivision,
)

BILL_LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.R.J. Res.",
    "sjres": "S.J. Res.",
    "hconres": "H.R. Con. Res.",
    "sconres": "S. Con. Res.",
    "hres": "H.R. Res.",
    "sres": "S. Res.",
}


class UsExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    congress: int | None = None
    number: str | None = None
    statutes_at_large: list[str] | None = None
    official_title: str | None = None
    public_private: str | None = None
    compiled_through: str | None = None
    cfr_title: int | None = None
    cfr_part: str | None = None
    agency: str | None = None
    bill_type: str | None = None
    version_date_basis: str | None = None
    usc_title: int | None = None
    positive_law: bool | None = None  # False: a title that is only prima facie evidence of the law (1 U.S.C. 204)
    chapter: str | None = None  # chapter of an act in the Statutes at Large (acts before 1901: no public law number)
    session: int | None = None
    fr_volume: int | None = None  # Federal Register: volume, first page and document number of a final rule
    fr_page: int | None = None
    fr_number: str | None = None
    fr_action: str | None = None  # "Final rule", "Interim final rule", "Direct final rule" ... as the rule states it
    sub_agency: str | None = None
    rin: str | None = None  # Regulation Identifier Number
    cfr_affected: list[str] | None = None  # the CFR parts the rule's heading names, as printed
    release_point: str | None = None
    cfr_compilation: str | None = None  # "ecfr": the eCFR (current text), as against an annual edition
    cfr_chapter: str | None = None
    cfr_authority: str | None = None  # the part's statutory authority statement, as printed
    volume_amended_on: str | None = None  # amendment date of the CFR volume (eCFR file), ISO date


class UsParty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    role: str | None = None


class UsJudgmentExt(BaseModel):
    """What the case-law sources record beyond the common judgment fields (all taken from the source's metadata)."""

    model_config = ConfigDict(extra="forbid")
    # United States Reports (GovInfo USREPORTS)
    reporter: str | None = None
    volume: int | None = None
    first_page: int | None = None
    court_term: int | None = None
    volume_cases_listed: int | None = None
    volume_cases_located: int | None = None
    decision_date_caveat: str | None = None
    long_part_labels: dict[str, str] | None = None
    # GovInfo packages (USREPORTS and USCOURTS)
    govinfo_package: str | None = None
    govinfo_granule: str | None = None
    # US Courts opinions (GovInfo USCOURTS)
    court_code: str | None = None
    case_number: str | None = None
    nature_of_suit: str | None = None
    nature_of_suit_code: str | None = None
    parties: list[UsParty] | None = None
    docket_text: str | None = None
    pdf_pages: int | None = None
    # CourtListener bulk data (case law of all courts the source covers); see parsers/courtlistener_bulk.py
    courtlistener_cluster: int | None = None
    courtlistener_docket: int | None = None
    courtlistener_court: str | None = None
    court_name: str | None = None
    court_citation: str | None = None  # the court's form in a citation parenthetical, as CourtListener gives it
    citations: list[str] | None = None  # every reporter citation of the case, in the publisher's order
    citation_kind: str | None = None  # "reporter", "neutral" or "docket": what judgments.neutral_citation holds
    unit_scheme: str | None = None  # "page": units are printed pages of page_citation; "block": numbered text blocks
    page_citation: str | None = None
    text_columns: list[str] | None = None
    source_codes: str | None = None
    precedential_status: str | None = None
    date_filed_is_approximate: bool | None = None
    judges: str | None = None
    authors: list[str] | None = (
        None  # "Opinion concurring in part: <author>" where the part label had no room for the name
    )
    disposition: str | None = None
    scdb_id: str | None = None
    harvard_path: str | None = None
    opinion_ids: list[int] | None = None
    ocr: bool | None = None
    continuation: bool | None = None
    download_urls: list[str] | None = None


COURT_ABBREVIATIONS = {
    "US-CA1": "1st Cir.",
    "US-CA2": "2d Cir.",
    "US-CA3": "3d Cir.",
    "US-CA4": "4th Cir.",
    "US-CA5": "5th Cir.",
    "US-CA6": "6th Cir.",
    "US-CA7": "7th Cir.",
    "US-CA8": "8th Cir.",
    "US-CA9": "9th Cir.",
    "US-CA10": "10th Cir.",
    "US-CA11": "11th Cir.",
    "US-CADC": "D.C. Cir.",
    "US-CAFC": "Fed. Cir.",
}
MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


BLUEBOOK_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
CFR_REVISION_DAYS = {(1, 1), (4, 1), (7, 1), (10, 1)}
# The parts of a final rule in the Federal Register that its provisions are named after (the first word of the eId).
FR_PARTS = {
    "summary": "summary",
    "dates": "dates",
    "addresses": "addresses",
    "contact": "contact",
    "supplementary": "supplementary information",
    "regtext": "regulatory text",
    "signature": "signature",
    "footnotes": "footnotes",
}


def bluebook_date(d: date) -> str:
    return f"{BLUEBOOK_MONTHS[d.month - 1]} {d.day}, {d.year}"


def is_cfr_edition_date(d: date | None) -> bool:
    """True for the revision dates of the annual CFR (first day of a quarter); False for the date of an eCFR file."""
    return d is not None and (d.month, d.day) in CFR_REVISION_DAYS


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class UsModule(JurisdictionModule):
    id: ClassVar[str] = "us"
    version: ClassVar[str] = "1.4.0"
    ext_model: ClassVar[type[BaseModel]] = UsExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A judgment's ext is recognised by its GovInfo package; legislation's by its own keys.
        if "govinfo_package" in ext or "courtlistener_cluster" in ext:
            return UsJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        return f"§ {parts[0]}" + "".join(subdivision(p) for p in parts[1:])

    def ecfr_citation(self, c: ProvisionCitationInput) -> str:
        """``12 CFR 220.1(b)(1)``; an appendix: ``12 CFR part 220, appendix A``; no provision: ``12 CFR part 220``."""
        title, part = c.ext["cfr_title"], c.ext.get("cfr_part")
        parts = [p for p in c.num_path if p]
        if not parts:
            return f"{title} CFR part {part}"
        tail = "".join(subdivision(p) for p in parts[1:])
        if c.top_kind == "appendix":
            what = parts[0][0].lower() + parts[0][1:] if parts[0].startswith("Supplement") else f"appendix {parts[0]}"
            return f"{title} CFR part {part}, {what}{tail}"
        return f"{title} CFR {parts[0]}{tail}"

    def fr_citation(self, c: ProvisionCitationInput) -> str:
        """``91 Fed. Reg. 65,077 (Oct. 14, 2026)``; a provision adds the part of the rule it is (``, summary``)."""
        published = c.work_date or c.version_date
        cite = f"{c.ext['fr_volume']} Fed. Reg. {c.ext['fr_page']:,}"
        if published:
            cite += f" ({bluebook_date(published)})"
        label = FR_PARTS.get(c.eid.split("_")[0]) if c.eid else None
        return f"{cite}, {label}" if label else cite

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        ext = c.ext
        pin = self.pinpoint(c)
        dated = c.version_date or c.work_date
        when = f" ({dated.year})" if dated else ""
        if c.instrument_type == "act" and ext.get("usc_title"):
            return f"{ext['usc_title']} U.S.C." + (f" {pin}" if pin else "")
        if c.instrument_type == "regulation" and ext.get("cfr_title") and ext.get("cfr_compilation") == "ecfr":
            return self.ecfr_citation(c)
        if c.instrument_type == "regulation" and ext.get("fr_volume") and ext.get("fr_page"):
            return self.fr_citation(c)
        if c.instrument_type == "regulation" and ext.get("cfr_title"):
            if not is_cfr_edition_date(c.version_date):  # a file of the eCFR in an instrument that began as an edition
                return self.ecfr_citation(c)
            return f"{ext['cfr_title']} C.F.R. {pin or 'pt. ' + str(ext.get('cfr_part'))}{when}"
        if c.instrument_type in ("bill", "resolution") and ext.get("bill_type"):
            label = BILL_LABELS.get(ext["bill_type"], ext["bill_type"].upper())
            congress = f", {ordinal(int(ext['congress']))} Cong." if ext.get("congress") else ""
            return f"{label} {ext.get('number')}{congress}{' ' + pin if pin else ''}{when}"
        if c.instrument_type == "act" and ext.get("congress"):
            stat = (ext.get("statutes_at_large") or [None])[0]
            enacted_year = f" ({c.work_date.year})" if c.work_date else ""
            named = not c.instrument_title.startswith(("Public Law", "Private Law", "Act of "))
            if not ext.get("number") and ext.get("chapter") and c.work_date:
                # "Act of Jan. 13, 1902, ch. 2, § 1, 32 Stat. 1": the chapter stands in for the public law number
                parts = [
                    c.instrument_title if named else None,
                    f"Act of {bluebook_date(c.work_date)}",
                    f"ch. {ext['chapter']}",
                    pin or None,
                    stat,
                ]
                return ", ".join(p for p in parts if p)
            if ext.get("number"):
                label = "Priv. L. No." if ext.get("public_private") == "private" else "Pub. L. No."
                parts = [
                    c.instrument_title if named else None,
                    f"{label} {ext['congress']}-{ext['number']}",
                    pin or None,
                    stat,
                ]
                return ", ".join(p for p in parts if p) + enacted_year
        return f"{c.instrument_title}{', ' + pin if pin else ''}"

    def courtlistener_citation(self, c: JudgmentCitationInput, ext: dict[str, Any]) -> str:
        """``Roe v. Wade, 410 U.S. 113, 153 (1973)``; a state case ``State v. Reams, 945 P.2d 52, 53 (Mont. 1997)``;
        a neutral citation stands alone (``State v. X, 2019 ND 15``); with no citation, the docket number and date."""
        year = c.decision_date.year
        court = ext.get("court_citation") or COURT_ABBREVIATIONS.get(c.court_id) or c.court_name
        kind = ext.get("citation_kind")
        # A pinpoint is given only when the unit is a printed page, and then of the reporter whose pages they are: when
        # that is not the citation the case is filed under, it follows as a parallel citation
        # ("523 U.S. 751, 118 S. Ct. 1700, 1703").
        cite = c.neutral_citation
        page_cite = ext.get("page_citation")
        if cite and c.paragraph and ext.get("unit_scheme") == "page" and page_cite:
            cite = f"{cite}, {c.paragraph}" if page_cite == cite else f"{cite}, {page_cite}, {c.paragraph}"
        if kind == "neutral" and cite:
            return f"{c.case_name}, {cite}"
        if kind == "reporter" and cite:
            if c.court_id == "US-SC" and (c.neutral_citation or "").split()[1:2] == ["U.S."]:
                return f"{c.case_name}, {cite} ({year})"
            return f"{c.case_name}, {cite} ({court} {year})"
        d = c.decision_date
        dated = f"{court} {MONTHS[d.month - 1]} {d.day}, {year}"
        if kind == "database" and c.neutral_citation:  # a Westlaw or Lexis number: the full date, as the Bluebook asks
            return f"{c.case_name}, {c.neutral_citation} ({dated})"
        docket = f", No. {c.docket}" if c.docket else ""
        return f"{c.case_name}{docket} ({dated})"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        if c.ext and "courtlistener_cluster" in c.ext:
            return self.courtlistener_citation(c, c.ext)
        year = c.decision_date.year
        if c.court_id == "US-SC":
            if not c.neutral_citation:
                return f"{c.case_name}, {c.decision_date.isoformat()}"
            pin = f", {c.paragraph}" if c.paragraph else ""
            return f"{c.case_name}, {c.neutral_citation}{pin} ({year})"
        court = COURT_ABBREVIATIONS.get(c.court_id, c.court_name)
        if c.neutral_citation:
            return f"{c.case_name}, {c.neutral_citation} ({court} {year})"
        d = c.decision_date
        docket = f", No. {c.docket}" if c.docket else ""
        return f"{c.case_name}{docket} ({court} {MONTHS[d.month - 1]} {d.day}, {year})"

    def judgment_unit_label(self, num: str | None, part: str, ext: dict[str, Any] | None) -> str:
        if ext and "courtlistener_cluster" in ext:
            if ext.get("unit_scheme") == "page":
                return f"p. {num}" if num and not num.startswith("syllabus") else part
            return f"{part}, text block {num}"  # a running number of the unit, not a page or paragraph of the source
        if ext and ext.get("reporter"):
            return f"p. {num}"  # the units of the United States Reports are its printed pages
        if ext and ext.get("case_number"):
            return f"PDF p. {num}"  # the units of a slip opinion are the pages of its PDF
        return super().judgment_unit_label(num, part, ext)
