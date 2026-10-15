"""Brazil module: citation of decisions of the Superior Tribunal de Justiça (STJ) as its open data publishes them.

The STJ's open data identifies a decision by its procedural class and number (``processo``, e.g. ``REsp 2093032``),
its registration number (``numeroRegistro``) and the document id (``SeqDocumento``); it has no neutral citation and no
judgment date, only the date of publication in the Diário da Justiça eletrônico (DJe). Decisions are therefore cited
the way Brazilian practice cites them, with the identifiers and dates the data has:

* acórdão: ``STJ, REsp 2093032, Rel. Min. REGINA HELENA COSTA, DJe de 9/11/2023``
* decisão monocrática: ``STJ, HC 637258, decisão monocrática, Min. HUMBERTO MARTINS, DJe de 4/1/2021``

The minister is printed as the data gives it (upper case). The state (``/SP``) that Brazilian practice adds to the
class and number, and the judgment date (``j.``), are not in the data and are not invented. Formatting conventions
only; to be confirmed by a Brazilian reviewer before the BR profile is "reviewed". No citation of statutes is defined
because no Brazilian legislation is loaded.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JudgmentCitationInput, JurisdictionModule

COURT_ABBREVIATIONS = {"BR-STJ": "STJ", "BR-STF": "STF"}
# Section of a decision (``part`` of its paragraphs) -> how a search hit names it.
PART_LABELS = {
    "acordao": "acórdão",
    "ementa": "ementa",
    "relatorio": "relatório",
    "voto": "voto",
    "voto_vista": "voto-vista",
    "voto_vogal": "voto-vogal",
    "decisao": "decisão",
    "text": "texto",
}


class BrJudgmentExt(BaseModel):
    """Facts from the STJ open data that have no column of their own (``stj_integras`` parser), as published."""

    model_config = ConfigDict(extra="forbid")
    seq_documento: str  # SeqDocumento: the id of the decision/acórdão, also the name of its text file
    numero_registro: str | None = None  # numeroRegistro, e.g. 202302081120 (2023/0208112-0)
    processo: str  # class and number, e.g. "REsp 2093032"
    tipo_documento: str  # tipoDocumento as published: DECISÃO / DECISAO / ACÓRDÃO / ACORDAO
    document_type: Literal["acordao", "decisao"]
    recurso: list[str] = Field(default_factory=list)  # internal appeal the decision was given on (none: the main case)
    ministros: list[str] = Field(default_factory=list)  # reporting minister (acórdão) or deciding minister
    teor: list[str] = Field(default_factory=list)  # outcome, e.g. "Negando", "Não Conhecendo"
    descricao_monocratica: list[str] = Field(default_factory=list)  # the Court's template, party name masked
    assuntos: list[str] = Field(default_factory=list)  # CNJ subject codes, verbatim
    data_publicacao: str  # date of publication in the DJe (ISO)
    data_recebimento: str | None = None  # the case was received at the STJ
    data_distribuicao: str | None = None  # the case was distributed
    date_kind: Literal["dje_publication"]  # what the stored decision date is


class BrModule(JurisdictionModule):
    id: ClassVar[str] = "br"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = BrJudgmentExt

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        if not ext:  # legislation will carry its own ext once a Brazilian source is loaded
            return {}
        return super().validate_ext(ext)

    def judgment_unit_label(self, num: str | None, part: str, ext: dict[str, Any] | None) -> str:
        label = PART_LABELS.get(part, part)
        return f"{label}, n. {num}" if num else label

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        court = COURT_ABBREVIATIONS.get(c.court_id, c.court_name)
        ext = c.ext or {}
        parts = [court, c.docket or c.case_name]
        minister = (ext.get("ministros") or [None])[0]
        if ext.get("document_type") == "acordao":
            parts.append(f"Rel. Min. {minister}" if minister else "acórdão")
        elif ext.get("document_type") == "decisao":
            parts.append(f"decisão monocrática, Min. {minister}" if minister else "decisão monocrática")
        d = c.decision_date
        if ext.get("date_kind") == "dje_publication":
            parts.append(f"DJe de {d.day}/{d.month}/{d.year}")
        else:
            parts.append(f"{d.day}/{d.month}/{d.year}")
        text = ", ".join(parts)
        return f"{text}, n. {c.paragraph}" if c.paragraph else text
