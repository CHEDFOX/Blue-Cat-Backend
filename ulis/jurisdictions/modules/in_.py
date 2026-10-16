"""India module: citations for Acts, the Constitution and Supreme Court judgments (English and Hindi labels).

* Acts and the Constitution: ``The Indian Penal Code, 1860, s. 302``, ``The Constitution of India, art. 21(1)``;
  Hindi labels ``धारा`` (section) and ``अनुच्छेद`` (article).
* Supreme Court judgments: the case name as printed, the reporter citations the Court prints with the judgment
  (``1961 AIR 331``, ``1961 SCR (2) 40``, ``1979 SCC (1) 161``) or the neutral citation (``2023 INSC 1043``) when there
  is one, otherwise the case number, then the court and the date and the paragraph:
  ``... 1961 AIR 331; 1961 SCR (2) 40 (Supreme Court of India, 18 October 1960), para 7``. The reporter citations are
  reproduced as printed, not rewritten into the ``AIR 1961 SC 331`` form.

Formatting conventions only; to be confirmed by an Indian reviewer before the IN profile is "reviewed". No Act or
Constitution text is ingested yet, so the legislation formats are exercised by unit tests only.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
    subdivision,
)

PREFIX = {
    "en": {
        "section": "s.",
        "article": "art.",
        "rule": "r.",
        "regulation": "reg.",
        "clause": "cl.",
        "paragraph": "para",
    },
    "hi": {
        "section": "धारा",
        "article": "अनुच्छेद",
        "rule": "नियम",
        "regulation": "विनियम",
        "clause": "खंड",
        "paragraph": "पैरा",
    },
}
MONTHS = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
}
UNIT_LABELS = {
    "headnote": "headnote",
    "catchwords": "catchwords",
    "record": "record of proceedings",
    "text": "passage",
    "heading": "heading",
}
COURT_NAME = {"en": "Supreme Court of India", "hi": "भारत का सर्वोच्च न्यायालय"}


class InExt(BaseModel):
    """Extension data of an Indian Act or rule (none is ingested yet)."""

    model_config = ConfigDict(extra="forbid")
    act_number: str | None = None  # e.g. "45 of 1860"
    year: int | None = None


class InJudgmentExt(BaseModel):
    """Extension data of a Supreme Court judgment read from a JUDIS PDF."""

    model_config = ConfigDict(extra="forbid")
    layout: Literal["judis_header", "signed_judgment"]
    petitioner: str | None = None
    respondent: str | None = None
    case_numbers: list[str] = Field(default_factory=list)
    bench: list[str] = Field(default_factory=list)  # as printed (names, initials and capitals as in the PDF)
    author: str | None = None
    reporter_citations: list[str] = Field(default_factory=list)  # "1961 AIR 331", "1961 SCR (2) 40" ...
    reportable: bool | None = None


class InModule(JurisdictionModule):
    id: ClassVar[str] = "in"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = InExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        if "layout" in ext:
            return InJudgmentExt.model_validate(ext).model_dump(exclude_defaults=True)
        return super().validate_ext(ext)

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        labels = PREFIX["hi" if c.language == "hi" else "en"]
        return f"{labels.get(c.top_kind, c.top_kind)} {parts[0]}" + "".join(subdivision(p) for p in parts[1:])

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{c.instrument_title}, {pin}" if pin else c.instrument_title

    def judgment_unit_label(self, num: str | None, part: str, ext: dict[str, Any] | None) -> str:
        return f"para {num}" if num else UNIT_LABELS.get(part, part)

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        language = "hi" if c.language == "hi" else "en"
        months = MONTHS[language]
        date_text = f"{c.decision_date.day} {months[c.decision_date.month - 1]} {c.decision_date.year}"
        reporters = [r for r in (c.ext or {}).get("reporter_citations", []) if r]
        reference = c.neutral_citation or "; ".join(reporters) or c.docket
        court = f"({COURT_NAME[language] if c.court_id == 'IN-SC' else c.court_name}, {date_text})"
        pin = f", {PREFIX[language]['paragraph']} {c.paragraph}" if c.paragraph else ""
        return f"{c.case_name}, {reference} {court}{pin}" if reference else f"{c.case_name} {court}{pin}"
