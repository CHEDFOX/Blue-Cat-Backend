"""United States (federal) module: citation formats and extension schema.

Citation forms follow common US practice (Bluebook-style) for the document types ingested:

* public law (as enacted): ``Short Title, Pub. L. No. 115-6, § 2(1), 131 Stat. 11 (2017)``
* regulation (CFR annual edition): ``27 C.F.R. § 478.11(b) (2018)``
* bill or resolution (one stage): ``H.R. 1000, 116th Cong. § 2(a) (2019)``
* United States Code title (a release point of the Office of the Law Revision Counsel):
  ``18 U.S.C. § 1030(a)(2)``; the year is omitted because the online release points are not
  printed editions, and the release point is carried in the version data

* judgment of the Supreme Court (United States Reports, the page is the pinpoint):
  ``Roe v. Wade, 410 U.S. 113, 153 (1973)``
* judgment of a court of appeals (slip opinion, docket number as the source gives it):
  ``Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)``; no pinpoint, because the PDF page is not the slip opinion's
  own page number

They are formatting conventions for research output, reviewed by no one yet; a reviewer
sign-off is required before the US profile can be marked "reviewed".
"""

from __future__ import annotations

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
    positive_law: bool | None = None
    release_point: str | None = None


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


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class UsModule(JurisdictionModule):
    id: ClassVar[str] = "us"
    version: ClassVar[str] = "1.2.0"
    ext_model: ClassVar[type[BaseModel]] = UsExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        # A judgment's ext is recognised by its GovInfo package; legislation's by its own keys.
        if "govinfo_package" in ext:
            return UsJudgmentExt.model_validate(ext).model_dump(exclude_none=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        return f"§ {parts[0]}" + "".join(subdivision(p) for p in parts[1:])

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        ext = c.ext
        pin = self.pinpoint(c)
        dated = c.version_date or c.work_date
        when = f" ({dated.year})" if dated else ""
        if c.instrument_type == "act" and ext.get("usc_title"):
            return f"{ext['usc_title']} U.S.C." + (f" {pin}" if pin else "")
        if c.instrument_type == "regulation" and ext.get("cfr_title"):
            return f"{ext['cfr_title']} C.F.R. {pin or 'pt. ' + str(ext.get('cfr_part'))}{when}"
        if c.instrument_type in ("bill", "resolution") and ext.get("bill_type"):
            label = BILL_LABELS.get(ext["bill_type"], ext["bill_type"].upper())
            congress = f", {ordinal(int(ext['congress']))} Cong." if ext.get("congress") else ""
            return f"{label} {ext.get('number')}{congress}{' ' + pin if pin else ''}{when}"
        if c.instrument_type == "act" and ext.get("congress"):
            stat = (ext.get("statutes_at_large") or [None])[0]
            enacted_year = f" ({c.work_date.year})" if c.work_date else ""
            parts = [
                c.instrument_title if not c.instrument_title.startswith("Public Law") else None,
                f"Pub. L. No. {ext['congress']}-{ext.get('number')}",
                pin or None,
                stat,
            ]
            return ", ".join(p for p in parts if p) + enacted_year
        return f"{c.instrument_title}{', ' + pin if pin else ''}"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
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
        if ext and ext.get("reporter"):
            return f"p. {num}"  # the units of the United States Reports are its printed pages
        if ext and ext.get("case_number"):
            return f"PDF p. {num}"  # the units of a slip opinion are the pages of its PDF
        return super().judgment_unit_label(num, part, ext)
