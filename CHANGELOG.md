# Changelog

All notable changes to `slicer-profile-bridge` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project follows [semver](https://semver.org/). Everything on the
`0.x` line should be treated as schema-unstable — we lock the shape at
`1.0.0` once the third real consumer pushes back on it.

## [Unreleased]

## [0.3.1] — 2026-04-21

Derived-field release. The `enclosure_required` field on
`CanonicalFilament` was declared in v0.2.0 but had no extractor, so
every downstream consumer saw `False` even for ABS with bed 110°C.

### Added
- `slicer_profile_bridge.heuristics.infer_filament_enclosure_required()`
  — shared derivation used by every translator. Three gates in OR:
  normalised category membership (abs/asa/pc/pa*/pet_cf/peek/pei),
  bed_temp_normal_c ≥ 95°C, and name substring hints (ABS/ASA/Nylon/PA-/
  PEEK/PEI/ULTEM).

### Changed
- `translators/orca.translate_filament()` and
  `translators/prusa.translate_filament()` now set
  `CanonicalFilament.enclosure_required` via the shared helper.
  BambuStudio inherits the fix transparently — its translator delegates
  to Orca.

### Tests
- `tests/test_heuristics.py` — 21 parametrised cases covering all three
  gates individually, combined-gate behaviour, missing-data fallbacks,
  and substring false-positive canaries. Helper coverage 100%.

### Consumer impact
Admeshio engine rule `WARP_RISK_NO_ENCLOSURE` (v1-dev) was built with a
dual gate (bed_temp_max > 100°C runtime proxy OR this flag) precisely
because this field was unreliable. With 0.3.1 the profile leg becomes
the primary signal; the runtime proxy stays as defense-in-depth.

## [0.3.0] — 2026-04-20

Audit trust release. Adds the motion-tuning + custom-gcode + filament-
diameter + nozzle-type fields that audit consumers need to verify a real
user gcode against a simulated slice. Every addition is optional and
additive — existing v0.2.x consumers keep working, they just see `None`
on the new fields until they upgrade.

### Added

**`CanonicalPrinter`**:

- `pressure_advance_k: float | None` — PA / LA k-factor (seconds). Source
  of `M900` / `M572` / `SET_PRESSURE_ADVANCE` at gcode emission time.
- `max_jerk_mm_s: float | None` — classic Marlin junction-speed step.
  Populated by both Orca and Prusa from `machine_max_jerk_x/y` (max of
  the two axes for the outer envelope).
- `junction_deviation_mm: float | None` — modern continuous-motion
  alternative to jerk. Newer firmware carries one or the other.
- `start_gcode: str | None`, `end_gcode: str | None` — verbatim G-code
  injection points. A gcode-verify consumer comparing a user's gcode
  against a simulated slice needs the header / footer to match.
- `nozzle_type: NozzleType` — geometry class (`standard`, `volcano`,
  `chc`, `cht`, `high_flow`, `unknown`). Controls max volumetric
  throughput derivation beyond `nozzle_diameter_mm` alone. Inferred
  from Orca's `nozzle_volume_type` / `extruder_variant_list`;
  defaults to `unknown` when no marker is present.

**`CanonicalFilament`**:

- `filament_diameter_mm: float | None` — 1.75mm is the hobbyist default;
  2.85mm still ships on older LulzBot / Ultimaker / some industrial rigs.
  Consumers treat `None` as "assume 1.75" but must not mix that with a
  2.85 machine silently.

**`CanonicalProcess`**:

- `pressure_advance_k: float | None` — process-level PA override. Wins
  over `CanonicalPrinter.pressure_advance_k` when both are set, matching
  slicer precedence (Orca's per-process `pressure_advance`, Prusa's
  `linear_advance_speed`).

**New enum**: `NozzleType` exported from the package root.

### Changed

- Prusa translator gains `_to_str_verbatim()` for multi-line gcode
  fields. The CSV-first helper used elsewhere would truncate at the
  first comma, and PrusaResearch.ini gcode blocks routinely embed
  `{if layer_z < max, y}` style template commas.

### Translator coverage

- Orca: populates all six new printer fields + filament diameter +
  process PA. Nozzle-type classifier matches `volcano`, `cht`, `chc`,
  `high flow`, `bigtraffic`.
- Prusa: populates jerk, junction_deviation, start/end gcode, filament
  diameter, and process-level PA from `linear_advance_speed`. Printer-
  level PA left as `None` — Prusa firmware defaults live per-filament.
- Bambu: thin wrapper over Orca; all new fields flow through unchanged
  except `source.slicer`.

### Tests

87 tests (up from 68). New coverage:

- Schema bounds on every new field (non-negative / positive / enum
  domain, round-trip).
- Orca + Prusa end-to-end assertions against real fixtures: start/end
  gcode text, `max_jerk_mm_s > 0`, `filament_diameter_mm == 1.75`.
- Cross-slicer consistency: Bambu-translated vs Orca-translated X1C
  agree on `max_jerk` + `nozzle_type` + `filament_diameter` even though
  start/end gcode legitimately drifts between the two upstream repos.

### Rationale

First external consumer (Admeshio) is moving from "Beta" to "V1" and
needs GRI-I (gcode-inspection) to become authoritative. Without PA +
jerk + custom gcode + filament diameter the simulated-vs-actual gcode
diff produces too many false positives to trust in production. These
fields are universally present in FDM vendor profiles — no speculative
schema, just promoting data that was already sitting in `raw_data`.

## [0.2.1] — 2026-04-20

### Fixed

- `scripts/sync_upstream.py` used `datetime.UTC`, which was added in
  Python 3.11. The project declares `requires-python = ">=3.10"`, so the
  nightly sync workflow would have crashed on a 3.10 runner. Replaced
  with `datetime.timezone.utc` (available since 3.2). `canonical-profiles.json`
  output is byte-identical.

### Changed

- CI now runs `ruff check` and `mypy --strict` against `scripts/` as
  well as `src/` — the `dt.UTC` regression slipped through because
  the sync helper wasn't in the type-checked set.

## [0.2.0] — 2026-04-20

### Added

- `raw_data: dict[str, Any]` on `CanonicalPrinter`, `CanonicalFilament`,
  and `CanonicalProcess`. Carries every vendor field the canonical
  schema doesn't promote to first-class, minus inheritance-metadata
  keys (`type`, `name`, `inherits`, `setting_id`, etc.). Consumers can
  read obscure vendor fields without a bridge PR for every new audit
  check.
- `ProcessSpeeds.small_perimeter`, `.gap_infill`, `.bridge` — three
  speeds commonly audited for surface quality / cooling risk that were
  previously only reachable through `raw_data`.
- `CanonicalProcess.default_acceleration_mm_s2`, `retraction_overrides`
  — process-level motion knobs Prusa and Orca both carry.
- `.github/workflows/sync-upstream.yml` + `scripts/sync_upstream.py` —
  scheduled (daily) translation of OrcaSlicer / PrusaSlicer / BambuStudio
  HEAD into a single `canonical-profiles.json` artifact plus a
  `profiles-data` branch that downstream consumers can pull from.
- `src/slicer_profile_bridge/translators/README.md` — plugin-style
  contract for adding a new slicer translator (Cura, Lychee, ChiTuBox).

### Changed

- Translator output now includes `raw_data` by default — downstream
  consumers that did `CanonicalPrinter.model_dump()` get a strictly
  larger payload. Anything parsing with `extra="forbid"` on the
  canonical models must add the new field to its schema.

### Rationale

Feedback on 0.1.0: bridge should be a faithful translator, not an
opinionated extractor. Consumers with diverse needs (Admeshio auditing,
farm scheduling, cost estimation) each want a slightly different cut of
the vendor data. `raw_data` gives all of them read-access without
forcing a canonical-schema expansion for every new field request.

## [0.1.0] — 2026-04-20

### Added

- Canonical schema in pydantic v2: `CanonicalPrinter`,
  `CanonicalFilament`, `CanonicalProcess`, `CanonicalRecipe`,
  `ProfileBundle`. SI units throughout, `extra="forbid"` on every
  model.
- `SourceMetadata` on every top-level object — carries slicer, vendor,
  original profile id, and resolved `inherits_chain` for traceability.
- OrcaSlicer translator (Phase 1): walks `resources/profiles/` tree,
  resolves single-parent inheritance, maps the Orca JSON field names
  into canonical. Handles Orca's `"nil"` sentinel, percentage strings,
  list-wrapping for multi-extruder profiles, bed polygon parsing.
- PrusaSlicer translator (Phase 2): multi-section INI parser, multi-
  parent inheritance (`inherits = a; b`), abstract-base filter for
  `*wrapped*` names.
- BambuStudio translator (Phase 3): thin re-label over the Orca
  translator — Bambu's JSON is upstream of Orca so only the
  `source.slicer` label changes.
- 65 tests across schema, all three translators, and cross-slicer
  consistency. CI on Python 3.10 / 3.11 / 3.12 with ruff + mypy-strict
  + pytest + coverage.
- Fixtures: Bambu X1 Carbon family, Prusa MK4 / Prusament PLA /
  0.20mm QUALITY, Bambu X1C family. Attribution notes alongside.
