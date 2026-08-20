# Testing Strategy — MMIE

## Current State

All five suites exist and pass, hardware-independent, verified
2026-08-20:

| Suite | Location | Tests | Result |
|---|---|---|---|
| `test_measure` | `common/` | 35 | OK |
| `test_qwp_calibration` | `section2_qwp_calibration/` | 27 | OK |
| `test_discrete_reconstruction` | `section3_discrete_reconstruction/` | 14 | OK |
| `test_continuous_single_arm` | `section4_single_arm_continuous/` | 17 | OK |
| `test_continuous_dual_arm` | `section5_dual_arm_continuous/` | 18 | OK |

**111 tests total, all passing**, no motors/camera/Kinesis/IDS Peak SDK
required for any of them (two of the new tests below run an actual
`measure.py --dry-run` session, but with `DATA_ROOT` monkeypatched to a
temp directory — still no real hardware). `section2_qwp_calibration`'s
suite is the slowest per-test-average (~70s) — it exercises real
golden-section search convergence against a synthetic optical-bench
model, not a mocked instant result. `test_continuous_single_arm`'s full
suite now takes ~135s wall-clock, dominated by its one integration test's
real 1080-frame dry-run acquisition (below) — everything else in that
suite still runs in under a second.

## The primary correctness gate: synthetic round-trip

For every reconstruction/calibration module (Sections II–V): generate
"measured" intensities from a **known** Mueller matrix and **known** QWP
defect parameters, using the *same* forward formulas the module itself
implements, feed them through reconstruction, and confirm the known
answer comes back to near machine precision in the noiseless case, and
degrades gracefully (not catastrophically) under added synthetic noise.
This is the bar from `MMIE_FULL_PROJECT_SPEC.md` Part 5 — a module isn't
"done" until this passes, independent of whether the script merely runs
without raising.

## Testing Strategy By Suite

### `common/test_measure.py` (35 tests)

Hardware-independent: angle formatting/conversion, mode-definition
consistency, checkpoint logic, ROI selection, `--resume`, and the
pre-flight rank check (including the exact 90°-spacing aliasing
regression case), plus one full `--dry-run --no-prompt` session per
mode/acquisition-type combination.

### `section2_qwp_calibration/test_qwp_calibration.py` (27 tests)

The closed-form Eq. 19-21/Eq. 4 math; the golden-section search against a
known synthetic minimum; a full dry-run session
(`DryRunOpticalBench`'s hidden ground-truth model — a deliberately
imperfect, not-quite-90° QWP and a deliberately wrong assumed
zero-offset, so the search has something real and non-trivial to
converge on) exercising both QWPs' null search back-to-back; the
least-squares mode's own synthetic round-trip (known-defect recovery at
N=3 through many angles, exact agreement with the 3-point closed form at
N=3, a direct noise-reduction check that more angles lower the recovered-s
standard deviation, the B2/B4 alignment-diagnostic availability rule, and
the N=3-aliasing regression case — `design.md` §4). Five tests added
2026-08-20 (`decisions.md` ADR-012) exercise
`null_intensity_mismatch_warning()` directly — both branches of its
two-part (ratio AND absolute margin) threshold, since the full dry-run
session's two nulls always land close together and never trip it. Two
more tests added the same day, once `qwp_calibration.py` was split into
`run_acquisition()`/`run_reconstruction()` (`decisions.md` ADR-013,
`MMIE_ATOMIC_TARGETS.md` target 2.8): confirms `run_acquisition` writes
real `Images/*.tiff` + `Config/experiment_config.json` even in
`--dry-run` (previously dry-run frames only existed in memory), and that
running acquisition and reconstruction as two separate calls produces the
same numbers as the combined `run_calibration()` (`DryRunOpticalBench` has
no randomness, so this checks for close agreement, not just "runs without
error").

### `section3_discrete_reconstruction/test_discrete_reconstruction.py` (14 tests)

Synthetic round-trip: known M + known QWP defects → forward vector
formulas → reconstruction → known M recovered to near machine precision
(noiseless, both exact-4-state and overdetermined/least-squares paths,
both scalar and per-pixel calibration), graceful degradation under noise,
each mode recovers exactly the sub-block the mode table promises (and
`NaN`s the rest), more states measurably lower noise, and
`suggest_angle_grid`'s full-rank/reproducibility guarantees (including the
`0/45/90/135` aliasing-trap regression case). One test added 2026-08-20
(`decisions.md` ADR-013) for `discrete_measurement.py` (Section III's own
capture main, a thin wrapper around `common/measure.py`): a real
`--dry-run --no-prompt` invocation confirms `--acquisition discrete` is
preset and `--mode` still accepts any of 3x3/3x4/4x3/4x4 (`DATA_ROOT`
monkeypatched to a temp directory).

### `section4_single_arm_continuous/test_continuous_single_arm.py` (17 tests)

Synthetic round-trip for both 3×4 and 4×3: known M + known QWP defects →
synthetic per-frame intensities at real, non-uniform logged angles →
reconstruction → known M recovered to near machine precision, graceful
noise degradation. Also validates the calibration module's C1′ and s,f,r
recovery against injected ground truth, the cross-check-against-Section-II
report, **a direct regression test at `measure.py`'s literal
`OUTER_ANGLES_DEG` default** (`[0, 45, 90]` — the exact underdetermined-fit
bug from `design.md` §2), that more outer angles further reduce noise, and
`fit_outer_fourier`'s underdetermined-system guard. Also reaches across
section folders (reuses Section III's own generator/analyzer vector
formulas for its synthetic forward model, since Section IV's physics is
explicitly built on those same equations) and, since 2026-08-20, across
into `common/measure.py` too (one integration test only, monkeypatching
`DATA_ROOT`).

Two tests added 2026-08-20 while building the calibration CLI
(`MMIE_ATOMIC_TARGETS.md` Category 6): `test_accepts_genuine_per_pixel_intensities`
(a genuine `(H, W)`-shaped synthetic round-trip — the crash class three
functions had, found and fixed via `decisions.md` ADR-011) and
`test_load_and_run_against_real_dry_run_session` (an actual
`measure.py --dry-run --no-prompt` session, `DATA_ROOT` monkeypatched to a
temp directory, fed through the new loader — this is what actually
surfaced those bugs, not the pre-existing synthetic-array tests). One
more test added 2026-08-20 (`decisions.md` ADR-013) for
`continuous_single_arm_measurement.py` (Section IV's own capture main):
confirms `--acquisition continuous` is preset, `--mode` really is
restricted to 3x4/4x3 (rejects `4x4` with `SystemExit`), and a
`--mode 4x3` dry-run produces the expected config.

### `section5_dual_arm_continuous/test_continuous_dual_arm.py` (18 tests)

Synthetic round-trip: known M + known QWP defects → synthetic per-frame
intensities at a real (phase-offset, 5:1-locked) angle trajectory →
reconstruction → known M recovered to near machine precision, graceful
noise degradation, all 9 Eq. 66 consistency-check relationships hold
exactly at the noiseless recovery. Also validates the calibration module's
C1/C1′ and s,f,s′,f′ recovery against injected ground truth, the
cross-check-against-Section-II report, **the below-25-frames
underdetermined-system guard** (`design.md` §3, and that exactly 25 is
accepted), and that more frames beyond the minimum further reduce noise.
No cross-section imports except (since 2026-08-20) one integration test's
reach into `common/measure.py`, matching Section IV's own test file.

Two tests added the same day, mirroring Section IV's additions above:
`test_accepts_genuine_per_pixel_intensities` and
`test_load_and_run_against_real_dry_run_session` (`DATA_ROOT`
monkeypatched to a temp directory). One more test added 2026-08-20
(`decisions.md` ADR-013) for `continuous_dual_arm_measurement.py`
(Section V's own capture main): confirms `--mode 4x4 --acquisition continuous`
is preset fully (no `--mode` flag on this wrapper at all).

## Running Tests

```powershell
cd common                            ; python -m unittest test_measure -v
cd section2_qwp_calibration          ; python -m unittest test_qwp_calibration -v
cd section3_discrete_reconstruction  ; python -m unittest test_discrete_reconstruction -v
cd section4_single_arm_continuous    ; python -m unittest test_continuous_single_arm -v
cd section5_dual_arm_continuous      ; python -m unittest test_continuous_dual_arm -v
```

Or, per folder: `python -m unittest discover -p "test_*.py" -v`.

## What Is Explicitly Not Required (at current scope)

- **Real-hardware-in-the-loop testing** — not possible in this development
  environment (no Kinesis/IDS Peak SDK, no bench). Every suite above
  validates the math against its own stated forward model; it cannot
  catch a real-world effect the forward model doesn't include. See
  `troubleshooting.md`'s Anticipated Gotchas and `PRD.md`'s Risks.
- Performance/load testing — the per-pixel vectorization requirement is
  already met (`rules.md`), and there's no batch/combinatorial workload
  here to load-test (contrast `ocd_library`'s much larger-scale needs).
- Security testing — no untrusted input, no network surface, no
  deserialization risk beyond plain `.npy`/JSON reads.
