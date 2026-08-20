# MMIE Conventions

## Scope and status

This file records the notation and file/folder conventions this project
uses throughout Sections II–V — the physics itself is Hauge (1978)'s, so
these conventions exist to keep every module's variable names and file
formats traceable back to that paper and to each other, not to invent a
house style independent of it.

## Physics notation (Hauge 1978)

- **QWP defect parameters**: `s, f, r, p, delta_deg, T` — where
  `p = 1 - 2f` and `p^2 + r^2 + s^2 = 1`. Ideal QWP: `s=0, f=0.5, r=1,
  delta_deg=90`. Never renamed to alternative symbols even in code
  comments — a reader cross-checking against the paper should be able to
  match variable names directly.
- **Azimuths**: `P, C` for the PSG (generator) side's polarizer/QWP
  angles; `C', A` (primed) for the PSA (analyzer) side's QWP/polarizer
  angles. Primed always means analyzer-side, matching Hauge's own
  convention — never used for anything else.
- **Stokes vector**: `(S0, S1, S2, S3)`. **Mueller matrix**: `M` (or
  `M00..M33`), always reported **M00-normalized** — the `epsilon=0,
  delta=90` reduction always gives `2*identity` unnormalized, not
  identity, so normalization is not optional cleanup, it's required for
  the number to mean what it's supposed to mean.
- **Realizability diagnostic**: `trace(M^T@M) <= 4*M00^2`, reported
  alongside every reconstruction as a diagnostic, never used to "correct"
  the result.

## Mode naming

`3x3`/`3x4`/`4x3`/`4x4` — first digit is the PSG (generator) side's arity,
second is the PSA (analyzer) side's, exactly matching
`common/README.md`'s mode table. Never reordered, never abbreviated
differently elsewhere (e.g. never `4x3` written as `"3-4"` or similar).

## File/folder naming

- Discrete-mode images: `<first>_<second>.tiff` (e.g.
  `PSG_QWP_PSA_Analyzer...tiff`) — `first`/`second` are literally the axis
  names being varied, per mode.
- Continuous-mode frames: `frame_NNNN_....tiff`, logged per-frame angles
  in `Logs/experiment_log.csv`.
- Run folders: `Data/<date>_<label>_NN/` (samples),
  `Data/QWP_Calibration/<date>_<target>_NN/` (Section II calibration) —
  always under the project root, regardless of current directory
  (`troubleshooting.md`).
- Reconstruction output, identical schema across Sections III/IV/V:
  `Results/mueller_matrix.npy` (per-pixel `height x width x 4 x 4`) +
  `Results/mueller_matrix_summary.json` (ROI summary + realizability
  diagnostic).

## Code conventions

- **Per-pixel math is always vectorized** — never a per-pixel Python
  loop. A batched `numpy.linalg.solve`/`.lstsq`/`.inv` call across the
  full frame, every time.
- **Every function implementing a paper formula cites the Hauge equation
  number(s)** in its docstring (`rules.md` Documentation Standards).
- **Structurally-unrecoverable rows/columns are `NaN`**, never a
  fabricated number — 3×3/3×4/4×3 never pretend to see what they
  structurally cannot.
- **`common/measure.py` is read-only from every reconstruction module's
  perspective** — acquisition and reconstruction never share code beyond
  the on-disk file format they agree on.

## Known limitation

This file records conventions as they're actually followed across the
existing Sections II–V code — it isn't a forward-looking style guide for
features that don't exist yet (contrast the sibling `ocd_library`
project's `CONVENTIONS.md`, which is explicitly aspirational since almost
nothing there is implemented).
