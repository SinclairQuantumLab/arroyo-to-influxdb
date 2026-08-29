# arroyo-to-influxdb repository instructions

Use the canonical `to-influxdb-development` skill from the
`Sinclair-Agent-Skills` repository for every task that changes this workspace.
Run its source-refresh preflight before substantive work and follow its relay
corpus and provenance workflow. Do not copy the skill into this repository.

## Repository boundary

The reusable control library is owned by the independent nested repository at
`C:\Users\Joon\Projects\arroyo-to-influxdb\pyarroyo`. It keeps its own `.git`
history and is ignored by the parent repository during local co-development.
Do not duplicate its implementation or tests in the relay repository. This
repository will register and pin `pyarroyo` as a Git submodule after a
reproducible remote URL is chosen.

`Manuals/` remains read-only protocol evidence. Derived command catalogs,
coverage, API decisions, and library validation belong in `pyarroyo`; relay
schema and operator deployment documentation belong here after the stage gate.

## Current stage gate

Do not create `main.py`, an InfluxDB schema, credentials, settings, launchers,
or Supervisor configuration until both conditions hold:

1. the library completion criteria in `README.md` and the `pyarroyo`
   documentation have been met; and
2. the user explicitly declares the library satisfactory and authorizes the
   relay-app stage.

Passing offline tests alone does not satisfy the live-hardware criterion. Keep
manual-reviewed, offline-tested, live-read-tested, and
live-state-change-tested evidence distinct.

## Safety and artifacts

- Keep PDF extractions, renders, packet captures, and task-only evidence under
  ignored `tmp/` paths.
- Never put credentials, actual device serial numbers, or private deployment
  values in maintained files or fixtures.
- Any live qualification is read-only with respect to outputs, setpoints,
  limits, calibration, saved configurations, scripts, and persistent settings.
- Do not probe arbitrary serial ports. Require an explicit port or compare a
  newly attached port against a recorded baseline.

## README contract

Once relay work is authorized, `README.md` is the operator manual. Keep its
prerequisites, clone/submodule flow, settings, commands, exact InfluxDB schema,
timestamps, acquisition/recovery behavior, startup/Supervisor paths,
validation, and troubleshooting synchronized with the finished repository.
Put contributor-only details last under the exact heading
`## Developer's note`.
