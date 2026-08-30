# arroyo-to-influxdb repository instructions

Use the canonical `to-influxdb-development` skill from the
`Sinclair-Agent-Skills` repository for every task that changes this workspace.
Run its source-refresh preflight before substantive work and follow its relay
corpus and provenance workflow. Do not copy the skill into this repository.

## Required context

Before changing this repository, read these files in order:

1. `README.md` for the supported operator workflow and exact deployed schema.
2. `docs/README.md` for the maintainer-document map.
3. `docs/continuation.md` for the current handoff, evidence, and next gates.
4. `docs/decisions.md` before changing application shape, configuration,
   InfluxDB bootstrap, acquisition, recovery, or deployment behavior.
5. `pyarroyo/AGENTS.md` and `pyarroyo/docs/continuation.md` before changing the
   library or updating its submodule pin.
6. Both repositories' qualification/live-validation documents before any
   hardware, upload, wrapper, or Supervisor work.

Do not reconstruct the current state from conversation history or an old
commit when these maintained handoffs are available. Update the relevant
handoff whenever its state changes.

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
explicitly authorized relay-app planning and development. The reusable library
and parent relay are now published GitHub source repositories, and the relay
has an offline-tested implementation. `README.md` remains its operator
contract.

Further relay and library development must preserve the Git submodule boundary.
Existing authorization does not convert offline evidence into live evidence and
does not authorize an InfluxDB upload, Supervisor activation, arbitrary serial
probing, or instrument state changes. Keep
manual-reviewed, offline-tested, live-read-tested, live-upload-tested, and
live-service-tested evidence distinct.

## Accepted implementation invariants

- `main.py` is deliberately one direct, top-level sequential script. Do not
  introduce application classes, a `main()` wrapper, or helper-based
  orchestration to make it look like a conventional modular application.
- Settings are trusted deployer-owned TOML and are consumed directly. Do not
  add an application configuration model or parsing/validation framework
  without explicit user direction.
- The IMAQ secret-loading and InfluxDB bootstrap blocks are literal copies of
  the canonical `imaq-secret/README.md` examples. Their imports, query API,
  semicolon, and whole-table client construction are intentional and locked by
  an offline test; do not lint-clean or reinterpret them independently.
- Dry-run loads the credential file and constructs the InfluxDB APIs but skips
  `write()`. It still performs real controller I/O.
- Preserve the fixed `arroyo` measurement, exact tags/fields/types, per-sample
  UTC timestamps, TEC-then-laser order, complete-batch write, one source
  reconnect/re-identification/retry, cumulative exception threshold, and
  monotonic deadline scheduling unless the user explicitly changes them.

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
