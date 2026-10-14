"""Registry of jurisdiction modules.

To add a module: subclass :class:`JurisdictionModule`, give it a unique ``id`` and register
it in ``MODULES``.  A profile selects it with ``citation.module: <id>``.
"""

from __future__ import annotations

from ulis.jurisdictions.modules.au import AuModule
from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
)
from ulis.jurisdictions.modules.ca import CaModule
from ulis.jurisdictions.modules.ch import ChModule
from ulis.jurisdictions.modules.de import DeModule
from ulis.jurisdictions.modules.es import EsModule
from ulis.jurisdictions.modules.eu import EuModule
from ulis.jurisdictions.modules.fi import FiModule
from ulis.jurisdictions.modules.fictional import AureliaModule, BorealisModule
from ulis.jurisdictions.modules.gb import GbModule
from ulis.jurisdictions.modules.jp import JpModule
from ulis.jurisdictions.modules.nl import NlModule
from ulis.jurisdictions.modules.tw import TwModule
from ulis.jurisdictions.modules.us import UsModule

MODULES: dict[str, JurisdictionModule] = {
    m.id: m
    for m in (
        JurisdictionModule(),
        TwModule(),
        EuModule(),
        JpModule(),
        AureliaModule(),
        BorealisModule(),
        CaModule(),
        ChModule(),
        DeModule(),
        EsModule(),
        FiModule(),
        GbModule(),
        NlModule(),
        UsModule(),
        AuModule(),
    )
}


def get_module(module_id: str) -> JurisdictionModule:
    try:
        return MODULES[module_id]
    except KeyError as exc:
        raise KeyError(f"unknown jurisdiction module {module_id!r}") from exc


__all__ = [
    "MODULES",
    "JudgmentCitationInput",
    "JurisdictionModule",
    "ProvisionCitationInput",
    "get_module",
]
