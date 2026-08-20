# Testing Strategy — MMIE

## Current State

All five suites exist and pass, hardware-independent, verified
2026-08-20:

| Suite | Location | Tests | Result |
|---|---|---|---|
| `test_measure` | `common/` | 35 | OK |
| `test_qwp_calibration` | `section2_qwp_calibration/` | 20 | OK |
| `test_discrete_reconstruction` | `section3_discrete_reconstruction/` | 13 | OK |
| `test_continuous_single_arm` | `section4_single_arm_continuous/` | 14 | OK |
| `test_continuous_dual_arm` | `section5_dual_arm_continuous/` | 15 | OK |

**97 tests total, all passing**, no motors/camera/Kinesis/IDS Peak SDK
required for any of them. `section2_qwp_calibration`'s suite is the
slowest (~70s) — it exercises real golden-section search convergence
against a synthetic optical-bench model, not a mocked instant result.

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

### `section2_qwp_calibration/test_qwp_calibration.py` (20 tests)

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
the N=3-aliasing regression case — `design.md` §4).

### `section3_discrete_reconstruction/test_discrete_reconstruction.py` (13 tests)

Synthetic round-trip: known M + known QWP defects → forward vector
formulas → reconstruction → known M recovered to near machine precision
(noiseless, both exact-4-state and overdetermined/least-squares paths,
both scalar and per-pixel calibration), graceful degradation under noise,
each mode recovers exactly the sub-block the mode table promises (and
`NaN`s the rest), more states measurably lower noise, and
`suggest_angle_grid`'s full-rank/reproducibility guarantees (including the
`0/45/90/135` aliasing-trap regression case).

### `section4_single_arm_continuous/test_continuous_single_arm.py` (14 tests)

Synthetic round-trip for both 3×4 and 4×3: known M + known QWP defects →
synthetic per-frame intensities at real, non-uniform logged angles →
reconstruction → known M recovered to near machine precision, graceful
noise degradation. Also validates the calibration module's C1′ and s,f,r
recovery against injected ground truth, the cross-check-against-Section-II
report, **a direct regression test at `measure.py`'s literal
`OUTER_ANGLES_DEG` default** (`[0, 45, 90]` — the exact underdetermined-fit
bug from `design.md` §2), that more outer angles further reduce noise, and
`fit_outer_fourier`'s underdetermined-system guard. This is the one test
file that reaches across section folders (reuses Section III's own
generator/analyzer vector formulas for its synthetic forward model, since
Section IV's physics is explicitly built on those same equations).

### `section5_dual_arm_continuous/test_continuous_dual_arm.py` (15 tests)

Synthetic round-trip: known M + known QWP defects → synthetic per-frame
intensities at a real (phase-offset, 5:1-locked) angle trajectory →
reconstruction → known M recovered to near machine precision, graceful
noise degradation, all 9 Eq. 66 consistency-check relationships hold
exactly at the noiseless recovery. Also validates the calibration module's
C1/C1′ and s,f,s′,f′ recovery against injected ground truth, the
cross-check-against-Section-II report, **the below-25-frames
underdetermined-system guard** (`design.md` §3, and that exactly 25 is
accepted), and that more frames beyond the minimum further reduce noise.
Fully self-contained — no cross-section imports.

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
