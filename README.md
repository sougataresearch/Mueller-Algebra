# MMIE — Mueller Matrix Imaging Ellipsometry

A polarimetry-bench control and analysis project, following Hauge (1978),
*"Mueller matrix ellipsometry with imperfect compensators"* (JOSA 68(11),
1519 — `josa-68-11-1519.pdf`, this project's primary reference). The full
task specification is `MMIE_FULL_PROJECT_SPEC.md`.

**New here?** [RECIPE.md](RECIPE.md) is a step-by-step cookbook of exactly
which script to run first, second, third, etc. [DRY_RUN_WALKTHROUGH.md](DRY_RUN_WALKTHROUGH.md)
walks through what actually happens on screen and on disk when you run
each script in `--dry-run` mode, so you know what to expect before you
touch real hardware.

The project is organized **one folder per paper section**, so you can open
just the piece you're trying to understand instead of one flat pile of
files:

```
common/                          <- shared hardware layer + measure.py (acquisition, all sections)
section2_qwp_calibration/        <- Section II:  QWP retardance calibration
section3_discrete_reconstruction/<- Section III: discrete-mode Mueller matrix
section4_single_arm_continuous/  <- Section IV:  one QWP spins, other stepped (validation stepping-stone)
section5_dual_arm_continuous/    <- Section V:   both QWPs spin at 5:1 (production path)
```

Each folder has its own `README.md` with that section's physics, math,
inputs/outputs, and run/test commands — start there for the piece you
care about. This file only covers how the pieces fit together as a whole.

## Physics background, from zero

**Polarization** is the shape a light wave's oscillation traces: a
straight line at some angle (linear), a circle (circular), or something in
between (elliptical). A **polarizer** only lets one linear angle through;
a **quarter-wave plate (QWP)** delays one axis of oscillation relative to
the other, which is what turns linear polarization into circular and back
— but only if it's truly a *perfect* 90° retarder, which no real QWP is.
Section II exists to measure how far from ideal your actual QWPs are.

A polarization state is a **Stokes vector** `(S0,S1,S2,S3)`; what a sample
does to polarization is its **Mueller matrix** `M`, where
`Stokes_out = M @ Stokes_in`. The camera only ever reads plain brightness
(`S0`); generating known input states and analyzing the output at enough
different (generator, analyzer) combinations gives enough equations to
solve for every entry of `M` — that's Sections III/IV/V, just via three
different acquisition strategies (see `common/README.md` for the full
mode table and discrete-vs-continuous breakdown).

## How the five pieces connect

```
section2_qwp_calibration/qwp_calibration.py
  (Section II: finds real s,f,r,delta,T for BOTH QWPs,             \
   once per bench/wavelength -- no external polarimeter needed,     \
   the camera is its own null detector)                              v
                                                    <calibration_result.json>
                                                                       |
common/measure.py  --->  Data/<run>/  -----------------------------> section3_discrete_reconstruction/     (Section III, discrete mode)
  (Section III/IV/V acquisition,                                ---> section4_single_arm_continuous/       (Section IV, 3x4/4x3 continuous)
   one script, every mode)                                      ---> section5_dual_arm_continuous/         (Section V, 4x4 continuous -- production)
```

Section IV and V each also have their own built-in calibration module,
run as a *separate* sample-absent session, reporting a cross-check against
Section II's numbers for the same QWP (agreement = confidence in both;
disagreement = something to investigate).

## Running the whole pipeline end to end (dry-run first, always)

```powershell
# 1. Calibrate both QWPs once per bench/wavelength (Section II).
python section2_qwp_calibration\qwp_calibration.py --dry-run --no-prompt --target both

# 2a. Discrete-mode sample (Section III):
python common\measure.py --mode 4x4 --acquisition discrete --dry-run --no-prompt --run-label sample1
python section3_discrete_reconstruction\discrete_reconstruction.py "Data\2026-08-18_sample1_01" `
    --psg-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"

# 2b. 3x4/4x3 continuous-mode sample (Section IV):
python common\measure.py --mode 4x3 --acquisition continuous --dry-run --no-prompt --run-label sample2
python section4_single_arm_continuous\continuous_single_arm_reconstruction.py "Data\2026-08-18_sample2_01" `
    --rotating-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"

# 2c. 4x4 continuous-mode sample (Section V -- production path):
python common\measure.py --mode 4x4 --acquisition continuous --dry-run --no-prompt --run-label sample3
python section5_dual_arm_continuous\continuous_dual_arm_reconstruction.py "Data\2026-08-18_sample3_01" `
    --psg-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01" `
    --psa-calibration-dir "Data\QWP_Calibration\2026-08-18_PSG_QWPandPSA_QWP_01"
```

Run these from the project root (this file's own folder) — `Data/` always
lands here regardless of which section's script you invoke or what your
current directory is. Every reconstruction CLI writes
`Results/mueller_matrix.npy` (per-pixel, M00-normalized) and
`Results/mueller_matrix_summary.json` (an ROI-summarized single matrix,
`--roi X Y W H` to pick the region); 3x3/3x4/4x3 leave the structurally
unrecoverable rows/columns as `NaN` (see `common/README.md`'s mode table)
— expected, not a bug. All four reconstruction paths also expose a
`trace(M^T@M) <= 4*M00^2` physical-realizability diagnostic.

Swap `--dry-run` for real hardware only after every section's tests pass
(below) and a `--dry-run --no-prompt` pass looks sane for your setup.

## Testing

Each section's tests are self-contained in its own folder:

```powershell
cd common                            ; python -m unittest test_measure -v
cd section2_qwp_calibration           ; python -m unittest test_qwp_calibration -v
cd section3_discrete_reconstruction   ; python -m unittest test_discrete_reconstruction -v
cd section4_single_arm_continuous     ; python -m unittest test_continuous_single_arm -v
cd section5_dual_arm_continuous       ; python -m unittest test_continuous_dual_arm -v
```

All five suites are hardware-independent (no motors, camera, or
Kinesis/IDS Peak SDK required) and pass in `--dry-run`-equivalent mode.
The four reconstruction/calibration suites (Sections II-V) are synthetic
round-trip tests per the project spec's primary correctness gate: generate
"measured" intensities from a KNOWN Mueller matrix and KNOWN QWP defects
using the same forward formulas each module implements, feed them through
reconstruction, and confirm the known M comes back to near machine
precision (noiseless) and degrades gracefully under added noise.

## Setup checklist for a new machine

See `common/README.md` for the full checklist (Kinesis/IDS Peak SDK
install, `pip install`, motor serials/zero-offsets, and the recommended
dry-run-first order of operations).

## License

MIT — see [LICENSE](LICENSE).
