# Dry-run walkthrough — what actually happens when you run this

Every acquisition script here (`common/measure.py`,
`section2_qwp_calibration/qwp_calibration.py`) has a `--dry-run` mode that
runs the *entire* real workflow — same prompts, same files written, same
checks — but with the motors and camera replaced by software simulations.
Nothing physical moves, no SDK needs to be installed, and it's safe to run
as many times as you like. This file walks through exactly what you'll
see, so you can tell "normal dry-run output" apart from "something's
actually wrong," and know exactly which lines in the code you'd touch to
change behavior once you move to a real bench.

The reconstruction scripts (`discrete_reconstruction.py` and friends) have
no hardware in them at all — they only ever read files dry-run/real
acquisition already wrote, so there's nothing to "dry-run" there; they
behave identically either way. This file is about the two acquisition
scripts only.

## What's simulated vs. what's real, even in `--dry-run`

| Piece | In `--dry-run` |
|---|---|
| Motors (`motor_communication.CageRotatorMotor`) | No Kinesis/pythonnet call at all. Each simulated motor still tracks a real internal angle and takes simulated time to "move" (based on `MOVE_VELOCITY_DEG_S`), so timing/logging code paths are exercised for real. |
| Camera (`camera_communication.IDSCamera`) | No IDS Peak SDK call. `measure.py` generates a generic synthetic 640x480 gradient frame. `qwp_calibration.py` instead uses `DryRunOpticalBench` — a synthetic Malus's-law optical model driven by the *actual* simulated motor angle, with a deliberately-imperfect QWP and a deliberately-wrong assumed zero-offset baked in — so the null-search algorithm has a genuine minimum to find, not a trivial always-succeeds stub. |
| Files on disk (`Data/...`, `Config/`, `Logs/`, `Results/`) | 100% real. Every dry-run produces the exact same folder/file layout a real run would, with real (if physically meaningless) numbers in them. |
| Operator prompts | Real, unless `--no-prompt` is also given. |
| Environment check (Kinesis dir, IDS Peak import, disk space) | Real checks, but only block a **real** run — a missing SDK is reported but doesn't stop `--dry-run`. |

## Walkthrough 1 — `qwp_calibration.py --dry-run --no-prompt --target both`

Run:

```powershell
python section2_qwp_calibration\qwp_calibration.py --dry-run --no-prompt --target both
```

What happens, in order:

1. **Environment verification** prints a table (pythonnet, IDS Peak,
   NumPy, Pillow, Kinesis directory, disk space) — `MISSING` lines are
   expected and harmless here since you're not touching real hardware.
2. **Motors "connect" and "home"** — printed as `[axis] Connecting motor
   ... / Homing ...`, instantly, no real Kinesis call underneath.
3. **Step 0a — null the analyzer against the polarizer.** You'll see the
   coarse-then-fine golden-section search print a shrinking sequence of
   `angle -> ROI intensity` readings as it converges on a minimum — this
   minimum is a real number computed from `DryRunOpticalBench`'s synthetic
   Malus's-law model, not a placeholder. Since `--no-prompt` is set, no
   operator confirmation is needed.
4. **Step 0b — insert first QWP (simulated), null it against the crossed
   P/A.** Same search pattern, now converging on the compensator's optical
   0° instead.
5. **Step 1-4 — three-angle (or N-angle, if `--calibration-angle-mode
   least_squares`) measurement and closed-form solve.** Prints the
   recovered `s, f, r, delta_deg, T` for this QWP, plus any sanity-check
   warnings (e.g. `abs(s)>0.15` would print a warning — in dry-run with
   default settings you should NOT see these, since the synthetic model's
   defect is deliberately mild).
6. **Swap prompt** (skipped by `--no-prompt`) — normally: "physically
   remove this QWP, insert the other one, press Enter."
7. **Repeat Steps 0b-4 for the second QWP.**
8. **Writes output**:
   `Data/QWP_Calibration/<today>_PSG_QWPandPSA_QWP_01/Config/calibration_result.json`
   (both QWPs' `s,f,r,delta_deg,T`, per-pixel `.npy` maps + ROI summary)
   and `Config/discovered_zero_offsets.json` (diffed against
   `ZERO_OFFSETS_DEG`, so you should see a nonzero, non-huge diff — that's
   the deliberately-wrong assumed offset in the synthetic model being
   correctly discovered and corrected).
9. Prints the final folder path — **copy this exact path**, you'll pass
   it as `--psg-calibration-dir`/`--psa-calibration-dir`/
   `--rotating-calibration-dir` to every reconstruction script next.

**What "looks sane" means here**: `delta_deg` roughly 80-100°, `s` close
to 0, `f` close to 0.5, no sanity-check warnings printed, the discovered
zero-offset diff is small (a few tenths of a degree, matching the
synthetic model's deliberately-injected error) rather than wildly large.

## Walkthrough 2 — `measure.py --mode 4x4 --acquisition discrete --dry-run --no-prompt --run-label sample1`

Run:

```powershell
python common\measure.py --mode 4x4 --acquisition discrete --dry-run --no-prompt --run-label sample1
```

What happens, in order:

1. **Environment verification** (same table as above).
2. **Only the motors this mode actually needs connect and home** — for
   4x4 that's all four axes (`PSG_Polarizer`, `PSG_QWP`, `PSA_QWP`,
   `PSA_Analyzer`); a 3x3 run would skip the two QWP axes entirely.
3. Since `--no-prompt` is set, the operator/sample-name/comments prompts
   are skipped — the run label you passed on the command line is used
   directly, and the session runs exactly one sample then exits (no
   "measure another sample?" loop).
4. `Data/<today>_sample1_01/` is created; prints `Mode: 4x4 (discrete)`,
   `Fixed angles: none` (4x4 has no fixed axis — both sides sweep),
   `Total states: N` (36 for the default 6x6 angle grid).
5. **Pre-flight angle-grid rank check** (`CHECK_ANGLE_GRID_RANK`) — a fast
   ideal-optics proxy check on your `FIRST_ANGLES_DEG`/`SECOND_ANGLES_DEG`
   grid; only prints a problem if the grid is degenerate (e.g. angles
   spaced exactly 90° apart in a QWP mode) — silent success otherwise
   apart from the "Pre-flight rank check: OK" line.
6. **Automatic bright/dark reference capture** — two simulated frames,
   with automatic ROI selection and saturation/contrast checks. In
   dry-run, "insert the sample" prompt is skipped (`--no-prompt`), and any
   warning would normally pause for confirmation but is auto-accepted.
7. **Acquisition loop** — one line per state, `[k/36] <first>_<second>.tiff`,
   each with a simulated motor move + simulated capture. This is the
   slowest part even in dry-run, since simulated timing still elapses.
8. **Completion**: `Measurement complete: 36 images, 0 failed.` and a
   written `Reports/ExperimentReport.txt`.
9. Because it's a single-sample `--no-prompt` run, the script exits
   immediately after — no further prompts.

**Resulting folder** (`Data/<today>_sample1_01/`):

```
Images/PSG_Polarizer_PSA_Analyzer... .tiff   (or PSG_QWP_... for QWP axes)
Config/experiment_config.json                 <- mode, fixed_angles, state_inputs
Config/roi.json
Logs/terminal_transcript.txt, measurement_log.csv
Reports/ExperimentReport.txt
Results/BrightReference.tiff, DarkReference.tiff
```

Feed this folder straight into
`section3_discrete_reconstruction/discrete_reconstruction.py` (see
[RECIPE.md](RECIPE.md) Step 3a) — the dry-run's synthetic gradient frames
are not a real Mueller matrix, so the reconstruction's numeric *output*
won't mean anything physically, but every file it reads and every code
path it runs is identical to a real sample run. This is the point of
dry-run: verifying the *pipeline*, not the physics, before you touch real
hardware.

### Continuous mode differs like this

`measure.py --mode 4x4 --acquisition continuous ...` instead: spins the
simulated motors continuously and captures on a simulated angle trigger,
writing `Images/frame_NNNN_....tiff` and `Logs/experiment_log.csv` (the
per-frame logged angle pairs Section IV/V's reconstruction reads) instead
of `measurement_log.csv`. No `Checkpoints/` folder — continuous acquisition
has no resume support, matching `common/README.md`.

## Where to look when you're ready to change behavior

| You want to... | Edit this |
|---|---|
| Change which/how many angles get measured | `common/measure.py`'s `FIRST_ANGLES_DEG`/`SECOND_ANGLES_DEG` (discrete) or `OUTER_ANGLES_DEG`/`ROTATION_RATIO` (continuous), in the **USER SETTINGS** block near the top |
| Point at real motors | `common/measure.py`'s `MOTOR_SERIALS`, `ZERO_OFFSETS_DEG`; `common/motor_communication.KINESIS_DIR` if Kinesis isn't at the default install path |
| Change camera exposure/gain/format | `common/measure.py`'s `CAMERA_EXPOSURE_US`, `CAMERA_FRAME_RATE_FPS`, `CAMERA_GAIN`, `CAMERA_PIXEL_FORMAT` |
| Get lower-noise QWP calibration | `qwp_calibration.py --calibration-angle-mode least_squares --num-calibration-angles 12` (no code edit needed — see `section2_qwp_calibration/README.md`) |
| Use a denser/different reconstruction angle grid | `section3_discrete_reconstruction.suggest_angle_grid(mode, num_angles)` — generates a verified full-rank grid to paste into `measure.py`'s `FIRST_ANGLES_DEG`/`SECOND_ANGLES_DEG` |
| Select which region of the frame gets summarized | Any reconstruction script's `--roi X Y W H` flag |
| Disable the pre-flight rank check | `common/measure.py`'s `CHECK_ANGLE_GRID_RANK = False` (not recommended — it exists specifically to catch aliased grids before wasting a real acquisition) |

Every one of these is a plain module-level constant or a CLI flag — no
hidden state, so re-running `--dry-run --no-prompt` after any edit is the
fastest way to confirm you didn't break anything before spending real
bench time.

## Turning off `--dry-run` for real

Nothing about the *shape* of the run changes — same prompts, same output
folders, same downstream reconstruction commands. What actually changes:

- Real Kinesis/IDS Peak SDK calls replace the simulations — this is why
  Step 1 (environment verification) must show `OK` for every dependency
  before a real run is allowed to proceed (it hard-stops otherwise).
- The null-search in `qwp_calibration.py` searches against **real**
  camera ROI readings — expect it to take noticeably longer than the
  instant dry-run version, and to converge to whatever your bench's true
  optical zero actually is, not a scripted synthetic value.
- Drop `--no-prompt` for real runs so the safety confirmations
  (illumination-on check, bright/dark verification warnings, physical
  QWP-swap confirmation) are actually enforced rather than auto-accepted.
- Insert the real sample when prompted, instead of nothing.

Everything else — file layout, reconstruction commands, calibration-dir
arguments — is identical to what you just walked through above.
