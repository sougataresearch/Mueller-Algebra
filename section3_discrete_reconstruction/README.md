# Section III — discrete Mueller-matrix reconstruction

**Question this answers:** given a *discrete* grid of static intensity
images of an unknown sample, what is its Mueller matrix?

| File | What it is |
|---|---|
| `discrete_measurement.py` | **Capture main** — thin wrapper around `../common/measure.py`, presetting `--acquisition discrete` (any of 3x3/3x4/4x3/4x4). |
| `discrete_reconstruction.py` | **Reconstruction main** — mode-agnostic reconstruction math (Hauge Eq. 8/11/17/18), reads a capture run's folder. |
| `test_discrete_reconstruction.py` | Synthetic round-trip tests (see Testing, below). |

## Discrete, not continuous

Hauge's paper titles this section "**III. DIRECT (16-INTENSITY) METHODS**"
— it is specifically a grid of static positions (at least 16 for a full
4×4, fewer for 3×3/3×4/4×3; more than the minimum is fine and reduces
noise), one image per position, no rotation during capture. That's why
this module reads `measure.py`'s **discrete**-mode output specifically.
(The continuous-rotation methods are the separate Section IV/V modules —
not layered on top of this one, but alternative ways to get the same kind
of information.)

## The math, briefly

Each image is one equation: `R = (g·Ip/2) · d^T · M · s`, where `s` is the
generator side's known Stokes-generating vector at that state (from the
commanded P/C angles and, if that arm has a QWP, its *real* measured
s,f,r from Section II — not an assumed ideal 90° retarder) and `d` is the
analyzer side's dual vector. Stack every state into `R = (g·Ip/2)·D·M·G`
(Hauge Eq. 17):

- **Exactly 4 states/side**: direct inverse, `M = (2/g·Ip)·D⁻¹·R·G⁻¹`
  (Eq. 18).
- **More than 4 states/side** (typical, and lower-noise): joint
  least-squares over the vectorized system — more numerically robust than
  inverting each side separately.

For 3×3/3×4/4×3 (no QWP on one or both arms), the corresponding side's
vector only spans 3 dimensions (a plain polarizer never touches circular
polarization) — the unrecoverable row/column comes back as `NaN`, matching
`../common/README.md`'s mode table exactly. That's expected, not a solver
failure.

Every pixel gets its own tiny linear solve — done as one batched NumPy
`linalg.solve`/`linalg.inv` call across the whole frame, not a per-pixel
Python loop.

## N-angle grids (more than the minimum, for lower noise)

Unlike Section II's original 3-angle-only draft, this module was designed
from the start to reconstruct from *any* number of states at or above the
mode's minimum — `reconstruct_mueller_matrix` already dispatches to the
exact inverse at the minimum and least-squares above it automatically, and
`measure.py`'s own default grid (`FIRST_ANGLES_DEG = SECOND_ANGLES_DEG =
[0,30,60,90,120,150]`, 36 states for 4×4) is already well above the
16-state minimum. Averaging over more states lowers the noise on the
recovered M the same way it does in every other section (verified in
testing).

The one thing worth help picking is the angle *list itself* — some grids
alias (measure.py's own pre-flight `check_angle_grid_rank` exists
specifically to catch this against your real, final choice).
`suggest_angle_grid(mode, num_angles)` generates a grid verified full-rank
via a duplicated ideal-optics proxy check, for use as both
`FIRST_ANGLES_DEG` and `SECOND_ANGLES_DEG`:

```python
import discrete_reconstruction as dr
grid = dr.suggest_angle_grid("4x4", 12, {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0})
# -> [0.0, 15.0, 30.0, ..., 165.0] -- paste into measure.py's FIRST_ANGLES_DEG/SECOND_ANGLES_DEG
```

One aliasing trap found while building this: `0°/45°/90°/135°` (evenly
45°-spaced, the exact minimum for an arity-4/QWP-bearing mode) aliases to
rank 9 of the 12 needed for 3×4 — not because of *which* phase you start
at, but because 45° steps are themselves a resonance of the
`cos(2θ)`/`cos(4θ)` structure, so every evenly-45°-spaced candidate fails
regardless of offset. `suggest_angle_grid` falls back to a deterministic
(seeded, reproducible) search when that happens — only visible at the
exact 4-angle minimum for 3x4/4x3/4x4; N≥5 always finds a nice
evenly-spaced grid.

## Output

`Results/mueller_matrix.npy` (per-pixel, `height x width x 4 x 4`,
M00-normalized) and `Results/mueller_matrix_summary.json` (an
ROI-summarized single matrix). Both come with a
`trace(M^T@M) <= 4*M00^2` physical-realizability diagnostic.

## Running it

`discrete_measurement.py` and `..\common\measure.py --acquisition discrete`
are the same acquisition, callable either way — the former lives in this
folder specifically so this section visibly has both of its own mains:

```powershell
python discrete_measurement.py --mode 4x4 --dry-run --no-prompt --run-label sample1
# equivalent: python ..\common\measure.py --mode 4x4 --acquisition discrete --dry-run --no-prompt --run-label sample1

python discrete_reconstruction.py "..\Data\2026-08-18_sample1_01" `
    --psg-calibration-dir "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "..\Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --roi 100 100 200 200
```

(`--psg-calibration-dir`/`--psa-calibration-dir` only needed for whichever
arm actually has a QWP in your chosen mode — see `../common/README.md`'s
mode table. `--scalar-calibration` uses the ROI-summary s/f/r instead of
the per-pixel maps.)

## Testing

```powershell
python -m unittest test_discrete_reconstruction -v
```

Synthetic round-trip: generates "measured" intensities from a KNOWN
Mueller matrix and KNOWN QWP defects using the same forward vector
formulas this module implements, feeds them through reconstruction, and
confirms the known M comes back to near machine precision (noiseless,
both the exact-4-state and overdetermined/least-squares paths, both
scalar and per-pixel calibration) and degrades gracefully under added
noise. Also checks each mode recovers exactly the sub-block the mode
table promises (and NaNs the rest), that more states measurably lower
noise, and `suggest_angle_grid`'s full-rank/reproducibility guarantees
(including the `0/45/90/135` aliasing-trap regression case). No hardware
required.
