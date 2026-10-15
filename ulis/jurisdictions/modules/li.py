"""Liechtenstein module: citation conventions for the national law published on Lilex (gesetze.li).

* ``Art. 5 Abs. 1 Bst. a URG``, ``Art. 44 Abs. 2 Ziff. 1 URG`` - the article, then paragraph (Absatz), letter
  (Buchstabe) and number (Ziffer), then the statute's abbreviation, as Lilex writes the places of amendments in
  its footnotes ("Art. 1 Abs. 1 Bst. d aufgehoben durch LGBl. 2018 Nr. 112");
* a statute without an abbreviation in its title is cited with its short title, the title up to the date:
  ``Art. 1 Verfassung des Fürstentums Liechtenstein``;
* units outside the articles: ``Abs. 1 ...`` (numbered paragraphs of a law without articles),
  ``Übergangsbestimmungen Abs. 1 ...`` (loose numbered paragraphs after the heading "Übergangsbestimmungen"),
  ``Einleitung`` (the enacting formula), ``Anhang`` (annex and closing text).

The abbreviation is read from the title ("Gesetz vom ... (Urheberrechtsgesetz, URG)", "Strafgesetzbuch (StGB) vom
..."); the source has no separate field for it. Formatting conventions only; to be confirmed by a Liechtenstein reviewer before the
LI profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

_WORDS = {"abs": "Abs.", "bst": "Bst.", "ziff": "Ziff."}


class LiExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lgbl: str  # LGBl-Nr, "<year>.<number>" (the key of the law on Lilex)
    lgbl_year: int
    lgbl_number: int
    lr_number: str | None = None  # Liechtensteinische Rechtssammlung number, e.g. "101"
    abbreviation: str | None = None  # from the title's last parenthesis
    published_in: str | None = None  # "LGBl. 1999 Nr. 160" - the original publication
    issued_on: str | None = None  # date of issue of that Landesgesetzblatt
    version_number: int | None = None  # Lilex's number of this version
    version_label: str | None = None  # the dates Lilex prints for it


def _key(value: str) -> str:
    """``12a`` stays, ``1_a`` (a key for ``1a``) is written back without the separator."""
    return value.replace("_", "")


class LiModule(JurisdictionModule):
    id: ClassVar[str] = "li"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = LiExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        out: list[str] = []
        for i, part in enumerate(parts):
            tag, _, key = part.partition(":")
            if i == 0:
                if tag == "art":
                    out.append(f"Art. {_key(key)}")
                elif tag == "abs":
                    out.append(f"Abs. {_key(key)}")
                elif tag == "ueb":
                    out.append(f"Übergangsbestimmungen Abs. {_key(key)}")
                elif tag == "anh":
                    out.append("Anhang" + (f" {key}" if key != "1" else ""))
                elif part == "preamble":
                    out.append("Einleitung")
                elif tag == "txt":
                    out.append(f"Text {key}")
                else:
                    out.append(part)
            else:
                out.append(f"{_WORDS.get(tag, tag)} {_key(key)}")
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        name = c.ext.get("abbreviation") or re.split(r"\s+vom\s", c.instrument_title, maxsplit=1)[0]
        pin = self.pinpoint(c)
        return f"{pin} {name}" if pin else name
