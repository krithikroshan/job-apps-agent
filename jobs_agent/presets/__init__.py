"""Career presets: complete scoring profiles to start a new account from.

A preset is loaded into the Profile page's editor, reviewed, and saved like
any manual edit — it's a starting point, not a mode. Adding a field means one
module here and one entry in :data:`PRESETS`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..profile import Profile
from . import accounting_graduate, law_compliance


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    profile: Profile


PRESETS: tuple[Preset, ...] = tuple(
    Preset(id=m.PROFILE.preset, label=m.LABEL, description=m.DESCRIPTION, profile=m.PROFILE)
    for m in (accounting_graduate, law_compliance)
)

_BY_ID = {p.id: p for p in PRESETS}


def get_preset(preset_id: str) -> Preset | None:
    return _BY_ID.get(preset_id)


__all__ = ["PRESETS", "Preset", "get_preset"]
