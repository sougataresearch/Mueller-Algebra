# common/ — shared hardware layer + acquisition script

Not a "section" of the Hauge paper by itself — this is the acquisition
script and thin hardware-communication layer every section's math reads
the output of.

| File | What it is |
|---|---|
| `motor_communication.py` | One Thorlabs K10CR2/M rotation-stage wrapper (connect, home, move-to-angle, continuous spin). Dry-run simulates every operation. |
| `camera_communication.py` | One IDS Peak camera wrapper (open/configure, triggered capture, TIFF save+verify, ROI helpers). Dry-run simulates a synthetic frame. |
| `measure.py` | The lab's acquisition script. Covers 3x3/3x4/4x3/4x4 **discrete** and 3x4/4x3/4x4 **continuous** acquisition. |
| `test_measure.py` | Hardware-independent tests (see Testing, below). |

`measure.py` is intentionally **not** organized under one of the four
section folders (`../section2_qwp_calibration/` etc.) even though it's
what feeds all of them: it is one script that serves Sections III, IV,
*and* V simultaneously — which mode/acquisition-type you pick decides
which section's math reads its output — so it doesn't belong to any one
of them.

## Physics background, from zero

**Polarization** is the shape a light wave's oscillation traces: a
straight line at some angle (linear), a circle (circular), or something in
between (elliptical). A **polarizer** only lets one linear angle through;
a **quarter-wave plate (QWP)** delays one axis of oscillation relative to
the other, which is what turns linear polarization into circular and back.
A polarization state is a **Stokes vector** `(S0,S1,S2,S3)`; what a sample
does to polarization is its **Mueller matrix** `M`, where
`Stokes_out = M @ Stokes_in`.

The camera only ever reads plain brightness (`S0`). To solve for `M`, this
rig **generates** a series of known input states before the sample
(rotating `PSG_Polarizer` and/or `PSG_QWP`), **analyzes** the light after
the sample (rotating `PSA_Analyzer` and/or `PSA_QWP`), and records one
brightness value per (generator, analyzer) combination — one image, one
equation. Enough combinations and there are enough equations to solve for
every entry of `M`; that solving happens offline, in
`../section3_discrete_reconstruction/`, `../section4_single_arm_continuous/`,
or `../section5_dual_arm_continuous/` depending on which mode you ran.

A plain rotating polarizer can only generate/detect *linear* polarization
— it never touches `S3` — so a two-polarizer rig (3×3 mode) can only
recover the `S0,S1,S2` sub-block. Reaching every state needs a QWP; a
fixed polarizer + rotating QWP sweeps the full 4-D Stokes space (4×4
mode's shape on each arm).

**Mode table** (filename is always `<first>_<second>.tiff`):

| Mode | first (filename prefix) | second (filename suffix) | fixed | recovers |
|---|---|---|---|---|
| 3×3 | `PSG_Polarizer` | `PSA_Analyzer` | none | `S0,S1,S2` sub-block only |
| 3×4 | `PSG_QWP` | `PSA_Analyzer` | `PSG_Polarizer` | everything except the bottom row (output circular) |
| 4×3 | `PSG_Polarizer` | `PSA_QWP` | `PSA_Analyzer` | everything except the rightmost column (input circular) |
| 4×4 | `PSG_QWP` | `PSA_QWP` | both polarizers | the full 16-entry matrix |

3×4/4×3 are the "one extra QWP" modes — a QWP added to only one arm,
matching a real experimental step. Whichever side has **no** QWP must
itself rotate through several angles (a single fixed angle would only
recover one row/column, not all of them); whichever side **has** the QWP
can stay fixed, since the QWP supplies the polarization diversity instead.

**Discrete vs continuous**: discrete steps the two varying motors to a
grid of angles and captures one image per stop (Section III). Continuous
spins the QWP(s) and captures on an angle trigger instead. 4×4 continuous
spins *both* QWPs simultaneously at a fixed revolution ratio (Section V,
classic dual-rotating-retarder polarimetry). 3×4/4×3 continuous only have
a QWP on one arm (Section IV) — a single spinning QWP against a genuinely
*fixed* linear analyzer/generator can only span a rank-4 subspace of the
unknowns, not enough to recover every row/column — so the linear-only side
is instead stepped through a short `OUTER_ANGLES_DEG` list, with one full
QWP revolution captured at **each** outer angle.

## Running it

Edit the **USER SETTINGS** block at the top of `measure.py` (mode,
acquisition type, angle lists or continuous shape, motor serials, zero
offsets, camera settings), then:

```powershell
python measure.py
```

Or override from the command line for a scripted/one-off run:

```powershell
python measure.py --mode 3x4 --acquisition discrete --run-label sample1
python measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt
python measure.py --resume "Data\2026-08-13_sample1_01"
```

Run from anywhere — `Data/` always lands at the **project root**
(`../Data/`, one level up from this folder), not inside `common/`,
regardless of your current directory when you invoke it.

`--dry-run` simulates every motor/camera call — including realistic
continuous-spin timing — with no Kinesis/pythonnet/IDS Peak installed.
Always test a new setup in dry-run first. `--no-prompt` skips every
confirmation, runs exactly one sample, and exits (used for scripted
dry-run tests). `--resume` only applies to discrete acquisition —
continuous has never supported resume; an interrupted revolution or outer
step just restarts from scratch.

## Safety features

- **Automatic bright/dark reference verification**, once per sample,
  before real acquisition starts: captures a bright and a 90°-crossed dark
  frame, auto-selects a flat/bright ROI, and warns (with a confirm-to-
  continue) if bright isn't actually brighter than dark or if the bright
  frame is saturated.
- **Retried, tolerance-verified motor moves** and **retried, verified
  image captures** (min/max/mean/saturated-pixel stats, with dark/bright
  warnings against configurable thresholds).
- **Crash-safe checkpointing** with `--resume` (discrete only).
- **Full session transcript** — every `print()` and every operator answer
  is duplicated to `Logs/terminal_transcript.txt`.
- **Multi-sample sessions** — hardware bring-up (discover → connect →
  initialize → home → optical zero) happens once; you're then looped
  through as many samples as needed without restarting the script.
- **Only the axes that actually changed value are re-commanded** each
  state — fixed axes are parked once, before the loop, not re-sent every
  single state.
- **Pre-flight angle-grid rank check** (`CHECK_ANGLE_GRID_RANK`, discrete
  only): before spending time capturing a grid, a duplicated minimal
  ideal-optics model checks the planned system matrix's rank and refuses
  to proceed if it's short — catching a degenerate grid (e.g. QWP angles
  spaced 90° apart, which alias in `cos(2*theta)`/`sin(2*theta)`) before
  any image is taken instead of after a wasted capture.

## Output folder structure

```
Data/YYYY-MM-DD_<label>_NN/
├── Images/                        <- <first>_<second>.tiff (discrete) or frame_NNNN_....tiff (continuous)
├── Config/
│   ├── experiment_config.json     <- mode, fixed_angles, camera/motor settings -- read by every section's reconstruction module
│   └── roi.json                   <- the auto-selected bright/dark reference ROI
├── Logs/
│   ├── terminal_transcript.txt
│   ├── measurement_log.csv        <- discrete: human audit trail
│   └── experiment_log.csv         <- continuous: per-frame angles, read by Section IV/V's reconstruction modules
├── Checkpoints/checkpoint.json    <- discrete only
├── Reports/ExperimentReport.txt
└── Results/BrightReference.tiff, DarkReference.tiff
```

Capturing images is only half the job — see `../README.md` for how the
four Hauge-section folders turn this output into an actual QWP retardance
or sample Mueller matrix.

## Testing

```powershell
python -m unittest test_measure -v
```

Hardware-independent: angle formatting/conversion, mode-definition
consistency, checkpoint logic, ROI selection, `--resume`, and the
pre-flight rank check (including a regression test for the exact
90°-spacing aliasing case), plus one full `--dry-run --no-prompt` session
per mode/acquisition-type combination. No motors, camera, or Kinesis/IDS
Peak SDK required.

## Setup checklist for a new machine

1. Install Thorlabs Kinesis (64-bit) and the IDS Peak SDK.
2. `pip install numpy Pillow pythonnet` (IDS Peak's own Python packages
   come with its SDK installer).
3. Edit `MOTOR_SERIALS`, `ZERO_OFFSETS_DEG`, and
   `motor_communication.KINESIS_DIR` (if Kinesis isn't installed at the
   default path) for this bench.
4. `python -m unittest test_measure -v`, then a `--dry-run --no-prompt`
   pass for each mode you'll use, before ever running for real.
