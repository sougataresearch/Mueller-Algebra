# Deployment — MMIE

## Environment Setup

```powershell
pip install numpy Pillow pythonnet
```

For real (non-dry-run) hardware use only, also install:

1. Thorlabs Kinesis (64-bit).
2. IDS Peak SDK (its own Python packages come bundled with the SDK
   installer).
3. Edit `common/measure.py`'s **USER SETTINGS** block: `MOTOR_SERIALS`,
   `ZERO_OFFSETS_DEG`, and `motor_communication.KINESIS_DIR` if Kinesis
   isn't installed at the default path.

None of the above is required for `--dry-run` sessions or any of the 97
tests (`testing.md`) — this project's actual development environment has
never had Kinesis/IDS Peak installed.

## Build Steps

None — no compiled artifacts, no packaging/distribution target. This is a
bench-control script collection run directly with `python <script>.py`,
not a published library (no `pyproject.toml`/`setup.py` exists, and none
is planned unless a real packaging need arises).

## CI/CD

None set up — single-user, single-bench project with no team and no
remote build environment with hardware access anyway (CI couldn't run the
hardware-touching halves regardless). The 97 hardware-independent tests
(`testing.md`) are run manually, locally, before trusting any code change.
Revisit only if this project gains contributors or a genuine need for
automated regression checking on every push.

## Production Deployment

Not applicable — this runs on one lab bench's own machine, not a deployed
service. "Deployment" here means: clone/pull the repo onto the bench
machine, install per Environment Setup above, run `RECIPE.md`'s Step 1
(tests) and Step 2 (`--dry-run --no-prompt` calibration) to confirm the
local setup is sane before ever running against real hardware.

## Monitoring / Logging

- Every acquisition/calibration session writes its own
  `Logs/terminal_transcript.txt` (every `print()` and every operator
  answer, duplicated to disk) — this is this project's actual logging
  mechanism, not an external log aggregator.
- No external monitoring — a bench-control tool run interactively by one
  operator doesn't need one. If unattended/scheduled runs are ever added,
  revisit this section then, not speculatively now.
