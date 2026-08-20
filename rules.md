# Development Rules — MMIE

## Coding Standards

- Python ≥ 3.10 (project's actual development/test environment).
- Pure Python + NumPy for all reconstruction/calibration math (Sections
  II–V) — no hardware dependency there. Hardware I/O is isolated to
  `common/motor_communication.py`/`camera_communication.py` and
  `qwp_calibration.py`'s/`measure.py`'s acquisition halves.
- **All per-pixel math vectorized.** No per-pixel Python loop anywhere —
  every reconstruction is one batched `numpy.linalg.solve`/`.lstsq`/
  `.inv` call across the full `height x width` frame.
- One folder per Hauge paper section (`section2_qwp_calibration/`,
  `section3_discrete_reconstruction/`, etc.), so a reader can open just
  the piece they care about instead of a flat pile of files
  (`architecture.md`).
- `common/measure.py` is never modified by any reconstruction-side change
  — Sections III/IV/V only ever *read* its output format. If
  `measure.py` itself needs a change, that's a `common/`-scoped change on
  its own, not something a reconstruction module's PR should bundle.
- Reuse `motor_communication.py`/`camera_communication.py` as-is — they
  were already correct at project start (`MMIE_FULL_PROJECT_SPEC.md`'s
  explicit "reuse as-is" instruction); don't rewrite working hardware
  wrappers while adding reconstruction math.

## Naming Conventions

Match Hauge (1978)'s own notation throughout, rather than inventing
parallel terminology for the same quantity:

- `s, f, r, p, delta_deg, T` — QWP defect parameters (diattenuation-linked
  `s`, `f` with `p=1-2f`, retardance-linked `r`, retardance angle, linear
  transmission ratio).
- `P, C` — PSG (generator) polarizer/QWP azimuths; `C', A` — PSA
  (analyzer) QWP/polarizer azimuths (primed = analyzer side, matching
  Hauge's own convention).
- `S0, S1, S2, S3` — Stokes vector components; `M` (or `M00`..`M33`) —
  Mueller matrix, always M00-normalized before being reported.
- Mode names `3x3`/`3x4`/`4x3`/`4x4` — first number is the PSG side's
  arity, second is the PSA side's, matching `common/README.md`'s mode
  table exactly (never re-ordered or relabeled elsewhere).

## Documentation Standards

- **Every function implementing a paper formula cites the Hauge equation
  number(s) in its docstring** — traceability for a thesis-adjacent tool.
  A suspected bug should be checkable against the paper directly, not
  against someone's memory of the derivation.
- Every section folder's `README.md` states: what question that section
  answers, the physics/math in brief, what its output schema is, and its
  exact run/test commands — a reader should never need to open the `.py`
  source just to find the CLI invocation.
- A "bug found and fixed" is documented in the relevant section's
  `README.md` *and* `decisions.md`, with the concrete before/after
  numbers (e.g. "gave a max error of 0.59 instead of machine precision")
  — not just "fixed a bug," which loses the evidence that it was real.

## Testing Requirements

See `testing.md`. The one non-negotiable gate, inherited directly from
`MMIE_FULL_PROJECT_SPEC.md` Part 5: **synthetic round-trip is the primary
correctness gate for Sections II–V.** Generate "measured" intensities from
a KNOWN Mueller matrix and KNOWN QWP defects using the *same* forward
formulas the module implements, feed them through reconstruction, confirm
the known answer comes back to near machine precision (noiseless) and
degrades gracefully under added noise. A module is not "done" until this
passes — a script that runs without error is not the same thing as a
script that recovers the right answer.

## Git Workflow

New commits over amends; no force-push without an explicit request;
review staged content before committing (matches this workspace's general
convention, see `sougata_solver/rules.md` for the fuller statement this
project inherits the spirit of). Own repo, own history
(`sougataresearch/Mueller-Algebra` on GitHub), MIT licensed.

## AI Coding Rules (must never be violated)

1. **Never invent a reconstruction formula without citing the Hauge
   equation it implements.** If the paper doesn't cover a case being
   added, say so explicitly and derive/cite the extension's own
   reasoning — don't present an uncited, plausible-looking formula as
   equivalent to the paper's.
2. **Never trust `numpy.linalg.lstsq` on an unchecked system size.** It
   does not detect or warn about an underdetermined fit — it silently
   returns *a* (minimum-norm, wrong) solution. This has already caused two
   real bugs in this project (Section IV's outer-angle fit, Section V's
   below-25-frame fit — `design.md` §2/§3). Any new Fourier-fit or
   least-squares code must explicitly check its equation count against
   its unknown count before trusting the result.
3. **Never assume an angle grid is full-rank because it looks evenly
   spaced.** Evenly-spaced grids can alias in `cos(2θ)`/`cos(4θ)`
   (`design.md` §4) — a genuinely new grid choice must be rank-checked
   (`check_angle_grid_rank`/`suggest_angle_grid`'s pattern), not assumed
   safe by inspection.
4. **Never assume a per-pixel-vectorized function actually works on
   genuine multi-pixel input just because its existing tests pass.** A
   hand-written synthetic test using 1-D (scalar-per-frame) arrays can
   pass while a hidden `float(...)` cast crashes the instant real `(H, W)`
   camera frames arrive — found three times in one afternoon while
   building the Section IV/V calibration CLI (`decisions.md` ADR-011).
   Before trusting a "vectorized" function against real data, run it
   against a genuinely multi-pixel array at least once, not just the
   scalar case its unit tests happened to construct.
5. **Never leave a mode's structurally-unrecoverable rows/columns as
   anything other than `NaN`.** Fabricating a plausible-looking number for
   a sub-block 3×3/3×4/4×3 cannot actually see is worse than an explicit
   `NaN` — it looks like a real measurement.
6. **Never modify `common/measure.py` from a reconstruction-side change.**
   If a reconstruction module needs `measure.py` to write something it
   currently doesn't, that's a `common/`-scoped change, made and tested on
   its own merits, not folded silently into a Section III/IV/V PR.
7. **Measure before optimizing.** Per-pixel vectorization is already the
   project's real performance requirement and is already met — there is
   no unmet performance need here to speculatively address (no GPU, no
   parallelism, no caching layer needed at this project's actual scale).
