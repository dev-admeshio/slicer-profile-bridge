"""Inheritance chain resolution.

Slicer profiles share defaults through an `inherits` field: a concrete
profile only declares the values it overrides, with `"nil"` as a sentinel
meaning "don't override, keep the parent's value". This module flattens
that chain into one dict of effective values, along with the list of
parents walked (so consumers can emit a full source audit trail).

Cycles (rare but possible in hand-edited user profiles) abort the walk
and the partial result is returned — failing loudly by raising would
take down bulk loads for one bad user file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slicer_profile_bridge.loader import ProfileType, RawProfile, RawProfileIndex

# ── Sentinel handling ──────────────────────────────────────────────────
# Orca encodes "inherit this value from parent" as the string "nil" at
# any position in a value. Single scalars, lists, and mixed shapes all
# use the same convention. We strip those during merge so a child with
# `"retract_length": ["nil", "nil"]` cleanly falls through to whatever
# the parent declared.


_NIL_STRINGS = frozenset({"nil", "NIL", "null"})


def _is_nil_value(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v in _NIL_STRINGS
    if isinstance(v, list) and v:
        return all(isinstance(x, str) and x in _NIL_STRINGS for x in v)
    return False


@dataclass
class ResolvedProfile:
    """A profile with inheritance flattened into a single dict of
    effective values, plus the parent chain for traceability.
    """

    type: ProfileType
    name: str
    vendor: str
    data: dict[str, Any]              # merged values
    inherits_chain: list[str]         # root-most last


def resolve(profile: RawProfile, index: RawProfileIndex) -> ResolvedProfile:
    """Walk `profile.inherits` until we hit a root (no `inherits`), then
    fold each layer into the merged dict from root → child.

    Child values win over parent values, except when the child value is
    the `nil` sentinel (in which case the parent's value carries through).
    """
    chain: list[RawProfile] = []
    seen: set[str] = set()

    current: RawProfile | None = profile
    while current is not None:
        if current.name in seen:
            break  # cycle — abort, use what we have so far
        seen.add(current.name)
        chain.append(current)
        parent_name = current.inherits
        if parent_name is None:
            break
        current = index.get(profile.type, parent_name)

    # Merge root → child so child wins last. `chain` is child-first, so
    # iterate reversed.
    merged: dict[str, Any] = {}
    for layer in reversed(chain):
        for key, value in layer.data.items():
            if _is_nil_value(value):
                continue
            merged[key] = value

    # Parent chain for source metadata: everyone except the profile
    # itself, in root-last order (matches how git ancestors are usually
    # written — immediate parent first, root at the end).
    parents = [p.name for p in chain[1:]]

    return ResolvedProfile(
        type=profile.type,
        name=profile.name,
        vendor=profile.vendor,
        data=merged,
        inherits_chain=parents,
    )
