# Adding a new slicer translator

Each slicer lives in its own module (`orca.py`, `prusa.py`, `bambu.py`).
Adding a fourth — Cura, Lychee, ChiTuBox, whatever — follows the same
shape. This note is the recipe.

## Contract

Every translator module exposes **three per-profile functions** and
**one high-level loader**:

```python
# Per-profile — unit-testable directly
def translate_printer(resolved: ResolvedProfile) -> CanonicalPrinter: ...
def translate_filament(resolved: ResolvedProfile) -> CanonicalFilament: ...
def translate_process(resolved: ResolvedProfile) -> CanonicalProcess: ...

# High-level — walks a filesystem, returns an indexed bundle
def load_<slicer>(profiles_root: str | Path) -> ProfileBundle: ...
```

`ResolvedProfile` is whatever your slicer's profiles look like after
`inherit.resolve` has flattened the inheritance chain — the translator
sees effective values, not raw override deltas.

## Minimum work

1. **Pick your input shape.** Most slicers use either:
   * JSON per profile (Orca, Bambu) — `loader.index_directory` already
     walks this layout. No new file-scanner needed.
   * Multi-section INI (Prusa, some Cura variants) — see
     `loader.load_ini_bundle` / `index_ini_file` for the pattern.
   * Proprietary bundle (ChiTuBox `.ctbprofile`) — write a loader that
     emits `RawProfile` objects the same shape those helpers do.

2. **Fill the canonical fields your slicer carries.** Leave any field
   your slicer doesn't declare as `None` — don't invent defaults.
   Canonical is a superset contract; sparse is fine.

3. **Stuff the rest into `raw_data`.** Call `_strip_metadata(resolved.data)`
   (define your own if your metadata keys differ) and pass the result
   as `raw_data` on the model. Consumers that need a vendor-specific
   field can read it from there without you needing to promote every
   field to first-class.

4. **Translate enums through the existing normalisers.** `FilamentCategory`,
   `InfillPattern`, `SupportPattern`, `SeamPosition` have vendor-flavoured
   maps in `orca.py` / `prusa.py` — extend if your slicer uses different
   strings (e.g. ChiTuBox resin categories).

5. **Register.** Add your `load_<slicer>` to `translators/__init__.py` and
   re-export from the package root `__init__.py`.

## Testing

Drop a representative sample of profiles under
`tests/fixtures/<slicer>/`. Include the inheritance chain if your
slicer has one (root templates + concrete user profile).

The test file should cover:

* **Primitive coercion** — vendor-specific quirks (percentage strings,
  list wrapping, inherited `nil` sentinels) exercised directly against
  your helpers.
* **End-to-end translation** — load the fixture directory, pick one
  representative profile from each of the three types, assert on the
  canonical values plus the `source.inherits_chain`.
* **Cross-slicer consistency** — if your slicer ships a printer that
  another supported slicer also ships (Ender 3 V2 is everywhere), a
  smoke test confirms the canonical output matches on shared fields.
  See `tests/test_bambu.py::TestCrossSlicerConsistency`.

## What NOT to do

* **Don't filter aggressively.** `raw_data` is cheap; losing a vendor
  field because you guessed no one would ever read it is expensive to
  roll back once a consumer depends on its absence.
* **Don't promote vendor-unique fields to first-class canonical.**
  `fan_direction` is Orca-only; keeping it in `raw_data` means the
  schema stays stable for non-Orca consumers. Promote only when two
  independent slicers converge on the same concept.
* **Don't touch the pydantic models from a translator.** If the
  canonical schema needs a new field, add it to `schema.py` with
  documentation; everyone wins.

## File layout

```
src/slicer_profile_bridge/translators/<your_slicer>.py
tests/test_<your_slicer>.py
tests/fixtures/<your_slicer>/...     # vendor profile samples
tests/fixtures/<SLICER>_ATTRIBUTION.md  # licence note for the fixtures
```

Keep the attribution file — the fixtures are almost always under the
slicer's own licence (AGPL for Orca / Prusa / Bambu, varies for others).
The rest of the project stays Apache-2.0; the fixture dir is the only
place non-permissive content lives.
