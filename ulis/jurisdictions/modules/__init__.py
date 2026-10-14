"""Registry of jurisdiction modules.

To add a module: subclass :class:`JurisdictionModule`, give it a unique ``id`` and register
it in ``MODULES``.  A profile selects it with ``citation.module: <id>``.
"""

from __future__ import annotations

from ulis.jurisdictions.modules.base import (
    JudgmentCitationInput,
    JurisdictionModule,
    ProvisionCitationInput,
)
from ulis.jurisdictions.modules.fictional import AureliaModule, BorealisModule
from ulis.jurisdictions.modules.gb import GbModule
from ulis.jurisdictions.modules.us import UsModule

MODULES: dict[str, JurisdictionModule] = {
    m.id: m for m in (JurisdictionModule(), AureliaModule(), BorealisModule(), UsModule(), GbModule())
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
