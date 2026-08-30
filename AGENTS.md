# arroyo-to-influxdb repository instructions

Use the canonical `to-influxdb-development` skill from the
`Sinclair-Agent-Skills` repository for every task that changes this workspace.
Run its source-refresh preflight before substantive work and follow its relay
corpus and provenance workflow. Do not copy the skill into this repository.

## Repository boundary

The reusable control library is owned by the independently published
`SinclairQuantumLab/pyarroyo` repository and pinned here as the `pyarroyo` Git
submodule. Its Git directory is managed under the parent repository's
`.git/modules/`; do not duplicate its implementation or tests in this relay.

Manual-derived command catalogs, coverage, API decisions, and library
validation belong in `pyarroyo`; relay schema and operator deployment
documentation belong here.

## Current stage

On 2026-08-29 the user accepted the reviewed `pyarroyo` direction and
explicitly authorized relay-app planning and development. The relay now has an
offline-tested implementation, while `README.md` remains its operator contract.

Further relay and library development must preserve the Git submodule boundary.
Existing authorization does not convert offline evidence into live evidence and
does not authorize an InfluxDB upload, Supervisor activation, arbitrary serial
probing, or instrument state changes. Keep
manual-reviewed, offline-tested, live-read-tested, live-upload-tested, and
live-service-tested evidence distinct.

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
