# Maintainer continuation

This is the durable handoff for a new thread, maintainer, or autonomous agent.
It records what has been settled, what is verified, and what remains gated so
work can continue without reconstructing the project from conversation history.

Last reviewed: 2026-09-16 (signal shutdown and offline relay checks).

## Current snapshot

| Area | State |
| --- | --- |
| Parent source | `SinclairQuantumLab/arroyo-to-influxdb`, `main`; implementation baseline through `c9de78e` plus this handoff |
| Library source | `SinclairQuantumLab/pyarroyo`, pinned here as submodule commit `89627c0018c353f024192ee27618316d57287cae` |
| Credential source | Private `imaq-secret` Git submodule; never inspect or copy its credential values into maintained artifacts |
| Runtime | Python 3.11+, uv-managed environment, synchronous direct top-level relay |
| Measurement | Fixed `arroyo`; exact schema is the contract in the root README |
| Offline relay evidence | 21 whole-script tests pass; targeted Ruff checks pass with the literal shared-block exceptions described below |
| Offline library evidence | Ruff passes, 347 tests pass, and all 296 catalog forms audit consistently; 290 forms are implemented through 215 static entities |
| Live source evidence | None; no attached Arroyo controller has been read in this workspace |
| Live upload evidence | None; no InfluxDB write or query-back has been authorized or recorded |
| Deployment evidence | Startup wrappers and Supervisor files are offline-tested only; neither platform path has been run here |

The library stage gate was accepted on 2026-08-29 and relay planning and
offline implementation were authorized. That decision did not authorize an
instrument command, upload, continuous foreground run, startup wrapper, or
Supervisor activation.

## Read order

1. Read the parent `AGENTS.md`, root `README.md`, this file, and
   [`decisions.md`](decisions.md).
2. For protocol or source-client work, read `pyarroyo/AGENTS.md`,
   `pyarroyo/docs/continuation.md`, `pyarroyo/docs/qualification.md`, and the
   evidence document routed from `pyarroyo/docs/README.md`.
3. Inspect the current Git status, submodule status, and recent history before
   editing. A modified `pyarroyo` gitlink may be an intentional child-first
   update rather than ordinary parent content.
4. Run the canonical `to-influxdb-development` skill preflight and refresh its
   relay inventory before changing this workspace.

## Runtime contract worth preserving

- `main.py` has no application functions, classes, or `main()` wrapper. It is a
  readable sequential script: arguments and trusted settings, shared secrets
  and InfluxDB setup, signal handling, source construction, polling loop, and
  cleanup.
- Exactly one explicit serial or network endpoint is expected. The relay never
  scans serial ports or probes alternative hosts.
- One persistent `ArroyoClient` connects and identifies the controller. The
  stable identity key is manufacturer, model, and serial number.
- Each attempt reads configured TEC entries in TOML order and then configured
  laser entries in TOML order. `pyarroyo` owns the individual snapshot lock,
  wire parsing, unit normalization, per-sample UTC timestamp, and any explicit
  channel selection/restoration.
- A cycle is all-or-nothing. Records are accumulated in memory and one
  synchronous write occurs only after every configured snapshot succeeds.
- On an acquisition failure, the relay discards the partial batch, reconnects,
  re-identifies the controller, refuses an identity change, and retries the
  complete batch once. An InfluxDB failure never reconnects the controller.
- Continuous operation counts unresolved source or upload failures over the
  process lifetime; successes do not reset the count. The third failure raises
  for Supervisor. `--once` exits nonzero on the first unresolved failure.
- Polling uses monotonic cycle-start deadlines. Acquisition time is subtracted
  from the wait; an overrun waits one full interval rather than issuing a
  catch-up read.
- SIGINT/SIGTERM invoke `signal.default_int_handler`, interrupt current work
  with `KeyboardInterrupt`, and exit 130 after `finally` cleanup. The source,
  write API, and InfluxDB client are closed without sending `LOCAL`. There is
  no signal-handler lock or requirement to finish the current polling cycle.

## Literal shared configuration blocks

The user explicitly required the `load IMAQ secret` and `InfluxDB
configuration` Python examples from `imaq-secret/README.md` to be copied
without any modification. The production block therefore intentionally keeps:

- the duplicate/mid-file `tomllib` import and mid-file InfluxDB imports;
- construction with the entire `AUTH["influxdb"]` table;
- the otherwise unused `INFLUXDB_QUERY_API`;
- `INFLUXDB_ORG` and `INFLUXDB_BUCKET` on one semicolon-separated line; and
- the example's exact comments and initialization print.

The installed InfluxDB client currently accepts the table's extra `bucket` key
through `**kwargs`, but that does not select the write destination. The relay
correctly supplies `bucket=INFLUXDB_BUCKET` and `org=INFLUXDB_ORG` to every
`INFLUXDB_WRITE_API.write(...)` call. Do not restore the former `pop("bucket")`
variant or otherwise clean the shared block unless the canonical secret README
and the user direction change together.

`--dry-run` still loads `imaq-secret/auth.toml` and constructs the client,
write API, and query API. It performs real controller reads and record mapping
but skips the write call. This is deliberate and documented in the operator
README.

## Milestone history

Use `git log --oneline` in each repository as the authority. Parent milestones
that explain the current shape are:

- `552a7aa`: initialize the Arroyo relay qualification workspace.
- `5896cab`: colocate the then-independent local library checkout.
- `3ad6534` and `144f517`: record and synchronize the accepted offline library
  gate before relay implementation.
- `57d4897`: register both submodules and add the initial relay, schema, tests,
  wrappers, and Supervisor templates.
- `0d63e22`: replace modular/application-style orchestration with the requested
  direct sequential script.
- `7a53c59`: align the configuration blocks and direct trusted settings style
  with the selected Sinclair references.
- `c9de78e`: use the canonical shared credential/InfluxDB form and finalize the
  reviewed configuration-template behavior.

The current child handoff is `pyarroyo` commit `89627c0`. Its earlier design
milestones and complete manual/API history are listed in
`pyarroyo/docs/continuation.md`.

## Reproducible offline checks

From the parent repository root:

```powershell
uv sync
uv run pytest -q
uv run ruff check tests supervisor
uv run ruff check main.py --ignore E402,I001,E702
```

The September 16 signal-shutdown run produced 21 passing relay tests. Full `uv run ruff check .`
reports six known findings in `main.py`: mid-file/import-order findings `E402`
and `I001`, plus semicolon finding `E702`. They arise only from the mandatory
literal IMAQ blocks. Do not auto-fix those blocks; all other checked parent code
passes, and the targeted `main.py` command above verifies the remaining rules.

From the child repository:

```powershell
cd pyarroyo
uv run ruff check .
uv run pytest -q
uv run python tools/audit_command_surface.py
cd ..
```

The 2026-08-30 handoff run passed Ruff, 347 tests, and the command audit. A
fresh recursive clone and fresh-environment `uv sync` have not yet been
recorded as reproducibility evidence.

## Next evidence gates

Proceed one gate at a time and keep each result distinct:

1. Obtain an explicit port or host plus operator-confirmed model, subsystem,
   channel, and sensor capabilities. Run the child validator's identity-only
   procedure, then only the confirmed read-only TEC/laser options. The
   validator may select/restore a channel and sends `LOCAL`; read its disclosed
   procedure before running it.
2. Run one parent `--once --dry-run` against that known endpoint. Review the
   sanitized identity, exact tags, field values/types, units, timestamps,
   condition flags, and any channel restoration. This sends real instrument
   queries but performs no InfluxDB write.
3. Only after separate upload authorization, run one non-dry one-shot, query
   the points back from InfluxDB, and compare the exact schema and timestamps.
4. Run a bounded continuous foreground session and observe interval, recovery,
   shutdown, and resource cleanup.
5. Validate the matching `Startup.ps1` or `Startup.sh` path in the target
   environment.
6. Only after separate service authorization, install/activate Supervisor and
   observe restart and log behavior. Templates intentionally use
   `autostart=false`.

Append sanitized results to [`live-validation.md`](live-validation.md) and the
child log where applicable. A failed run is still evidence; preserve its
sanitized symptom instead of broadening the procedure or probing alternatives.

## Cross-repository Git workflow

For a change spanning the library and relay:

1. Change and validate `pyarroyo` inside the submodule.
2. Commit and push the child repository first.
3. Return to the parent and confirm the child worktree is at the pushed commit.
4. Stage the `pyarroyo` gitlink, update parent context or integration code, and
   run the parent checks.
5. Commit and push the parent.

Never leave the parent pointing at an unpushed child commit. Do not duplicate
library source or tests in the parent. `imaq-secret` is a separate private
submodule; its contents are outside relay-corpus inspection and must never be
copied into a commit.

## Reference provenance and open items

The 2026-08-30 organization refresh found 12 accessible relay repositories:
the prior 11 in the skill corpus plus this newly published relay. Ten use
direct top-level orchestration; IQAir and Pico remain structural
counterexamples. For this relay, the closest implementation references remain
HiCube Neo and SEAS for synchronous snapshots, LFI-3751 for a serial
temperature-controller client, and Koheron for multiple configured
laser/controller records. Arroyo is now part of the inventory but is target
evidence, not independent precedent for the decisions that created it.

Known unresolved or intentionally deferred items are:

- no live controller, upload/query-back, wrapper, or Supervisor evidence;
- six manual-named laser calibration forms with no defined signatures, the
  dangling manual `TEC:VTE?` cross-reference, and the undocumented
  manufacturer-sample `TEC:VBULK?` extension; none is used by the relay;
- no fresh recursive-clone qualification;
- the six intentional Ruff findings caused by the verbatim shared blocks; and
- `192.168.50.172` in `settings.toml.template` is a random example address, not
  a discovered or deployed endpoint.
