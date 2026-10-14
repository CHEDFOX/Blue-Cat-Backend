"""United States (federal) module: citation formats and extension schema.

Citation forms follow common US practice (Bluebook-style) for the document types ingested:

* public law (as enacted): ``Short Title, Pub. L. No. 115-6, § 2(1), 131 Stat. 11 (2017)``
* regulation (CFR annual edition): ``27 C.F.R. § 478.11(b) (2018)``
* bill or resolution (one stage): ``H.R. 1000, 116th Cong. § 2(a) (2019)``
* United States Code title (a release point of the Office of the Law Revision Counsel):
  ``18 U.S.C. § 1030(a)(2)``; the year is omitted because the online release points are not
  printed editions, and the release point is carried in the version data

They are formatting conventions for research output, reviewed by no one yet; a reviewer
sign-off is required before the US profile can be marked "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput, subdivision

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


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class UsModule(JurisdictionModule):
    id: ClassVar[str] = "us"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = UsExt

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
