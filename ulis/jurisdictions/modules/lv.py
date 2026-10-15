"""Latvia module: Latvian citations of acts from Likumi.lv.

One documented style, the act's name in the genitive first, then the pinpoint from the largest unit
to the smallest, every container in the genitive and the cited unit in the nominative (the form used
in Latvian legal writing):

* ``Krimināllikuma 117. pants``; ``Satversmes 91. pants``; ``Civillikuma 1415. pants``
* parts are written with the ordinal as a word: ``Krimināllikuma 1. panta pirmā daļa``; points and
  sub-points follow: ``Darba likuma 101. panta pirmās daļas 1. punkts``,
  ``Darba likuma 101. panta pirmās daļas 1. punkta a) apakšpunkts``
* an article inserted between two others is written with a superscript: ``Krimināllikuma 38.¹ pants``
* transitional provisions: ``Krimināllikuma pārejas noteikumu 11. punkts``
* the Satversme's introduction: ``Satversmes ievads``

A Cabinet regulation (noteikumi) is cited by its date and number and divided into numbered points (punkti), whose
sub-points carry their full number: ``Ministru kabineta 2011. gada 17. maija noteikumu Nr. 377 2. punkts``,
``... Nr. 377 2.1. apakšpunkts``, ``... Nr. 377 12.1. apakšpunkta a) apakšpunkts``; its enabling provision
("Izdoti saskaņā ar ...") is ``... Nr. 377 izdošanas pamats``.

The genitive of the act's name (``Krimināllikuma``) comes from the parser (``ext.genitive``): the table of the pilot
acts, the regular genitive of a title ending ``likums`` or ``kodekss``, or the date and number of a Cabinet regulation;
for an act without one the citation is ``117. pants (Title)``. Parts numbered
beyond twenty, or with a superscript, are written with digits (``21. daļa``). Formatting conventions
only; to be confirmed by a Latvian reviewer before the LV profile is "reviewed".
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
# Ordinals for parts (daļa): nominative and genitive feminine.
ORDINALS = [
    ("pirmā", "pirmās"),
    ("otrā", "otrās"),
    ("trešā", "trešās"),
    ("ceturtā", "ceturtās"),
    ("piektā", "piektās"),
    ("sestā", "sestās"),
    ("septītā", "septītās"),
    ("astotā", "astotās"),
    ("devītā", "devītās"),
    ("desmitā", "desmitās"),
    ("vienpadsmitā", "vienpadsmitās"),
    ("divpadsmitā", "divpadsmitās"),
    ("trīspadsmitā", "trīspadsmitās"),
    ("četrpadsmitā", "četrpadsmitās"),
    ("piecpadsmitā", "piecpadsmitās"),
    ("sešpadsmitā", "sešpadsmitās"),
    ("septiņpadsmitā", "septiņpadsmitās"),
    ("astoņpadsmitā", "astoņpadsmitās"),
    ("deviņpadsmitā", "deviņpadsmitās"),
    ("divdesmitā", "divdesmitās"),
]


class LvExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    likumi_id: int  # the document's number on Likumi.lv (https://likumi.lv/ta/id/<id>)
    short_name: str  # "Krimināllikums"
    genitive: str | None = None  # "Krimināllikuma", for citations
    part: str | None = None  # Civillikums part, "PIRMĀ DAĻA. Ģimenes tiesības"
    issuer: str | None = None  # "Saeima", "Ministru kabinets", "Satversmes Sapulce"
    kind: str | None = None  # "regulation" for a Cabinet regulation (noteikumi); absent for a law
    omitted: list[str] | None = None  # parts of the page not ingested: "pielikumi" (annexes), "tabulas" (tables)
    published_in: str | None = None  # the official publication, as the portal states it


def number(label: str) -> str:
    """``38_1`` -> ``38.¹``; ``117`` -> ``117``."""
    base, _, sub = label.partition("_")
    return f"{base}.{sub.translate(SUPERSCRIPT)}" if sub else base


def numeral(label: str) -> str:
    """``117`` -> ``117.``; ``38_1`` -> ``38.¹`` (the superscript follows the point)."""
    n = number(label)
    return n if "." in n else f"{n}."


def dotted(label: str) -> str:
    """``12-1-3`` -> ``12.1.3.``: the number of a sub-point of a Cabinet regulation."""
    return ".".join(number(part) for part in label.split("-")) + "."


def part_word(label: str, genitive: bool) -> str:
    if label.isdigit() and 1 <= int(label) <= len(ORDINALS):
        return ORDINALS[int(label) - 1][1 if genitive else 0]
    return numeral(label)


class LvModule(JurisdictionModule):
    id: ClassVar[str] = "lv"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = LvExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        units: list[tuple[str, str]] = []
        for part in c.num_path:
            kind, _, label = part.partition(":")
            units.append((kind, label))
        regulation = c.ext.get("kind") == "regulation"
        out: list[str] = []
        for i, (kind, label) in enumerate(units):
            gen = i < len(units) - 1
            follows_transitional = i > 0 and units[0][0] == "pn"
            if kind == "art" and regulation:
                # a Cabinet regulation is divided into numbered points (punkti): "12. punkts"
                out.append(f"{numeral(label)} {'punkta' if gen else 'punkts'}")
            elif kind == "p" and "-" in label:
                # "12.1." / "12.1.3.": the number already holds the point it belongs to, so it is cited alone
                out.clear()
                out.append(f"{dotted(label)} {'apakšpunkta' if gen else 'apakšpunkts'}")
            elif kind == "art":
                out.append(f"{numeral(label)} {'panta' if gen else 'pants'}")
            elif kind == "d":
                out.append(f"{part_word(label, gen)} {'daļas' if gen else 'daļa'}")
            elif kind == "p":
                if follows_transitional:
                    out.append(f"{number(label)}) {'apakšpunkta' if gen else 'apakšpunkts'}")
                else:
                    out.append(f"{numeral(label)} {'punkta' if gen else 'punkts'}")
            elif kind == "ap":
                out.append(f"{label}) {'apakšpunkta' if gen else 'apakšpunkts'}")
            elif kind == "pn":
                out.append(f"pārejas noteikumu {numeral(label)} {'punkta' if gen else 'punkts'}")
            elif kind == "preamble":
                out.append("ievads")
            elif kind == "legal-basis":
                out.append("izdošanas pamats")
            elif kind == "eu-ref":
                out.append("informatīvā atsauce uz Eiropas Savienības direktīvām")
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        genitive = c.ext.get("genitive")
        if genitive:
            return f"{genitive} {pin}".strip()
        return f"{pin} ({c.instrument_title})" if pin else c.instrument_title
