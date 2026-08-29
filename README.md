# arroyo-to-influxdb

This workspace will become the SinclairQuantumLab relay that reads an Arroyo
Instruments controller through `pyarroyo` and writes selected observations to
InfluxDB. Relay implementation is intentionally paused while the reusable
control library is completed and qualified.

## Current stage

The active implementation lives in the independent nested `pyarroyo`
repository at `C:\Users\Joon\Projects\arroyo-to-influxdb\pyarroyo`. It has its
own `.git` directory, history, tests, and documentation while remaining next to
the future relay during local co-development. The parent repository ignores
this directory for now. After a reproducible remote is selected, the directory
will be registered as a real Git submodule and this repository will pin one
reviewed `pyarroyo` commit.

No InfluxDB schema, settings, credentials, polling loop, startup wrapper, or
Supervisor configuration has been selected yet. Those surfaces will be derived
from the completed library, the target Arroyo controller, and the closest
current Sinclair relay references.

## Prerequisites for the qualification stage

- Git
- Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/)
- The local `pyarroyo` repository when running offline library checks
- An explicitly identified Arroyo controller and serial port for later live
  read-only qualification

## Library qualification

Run the current offline checks from the library repository:

```powershell
cd C:\Users\Joon\Projects\arroyo-to-influxdb\pyarroyo
uv sync
uv run ruff check .
uv run pytest
uv run python tools/audit_command_surface.py
```

The library README records its supported command coverage, safety boundaries,
and validation status. Commands whose official documentation is incomplete are
listed there briefly and explained item by item in the library's `docs/`
evidence files.

The manual review and offline contract stages currently pass for all 296
normalized forms: 290 have generated typed methods and six documentation-gap
forms fail before I/O. Runtime metadata carries response shapes, wire units,
risks, and manual-derived model/firmware/feature availability notes. Live
read-only qualification remains outstanding, so the relay gate is still
closed; see `pyarroyo/docs/qualification.md` for the evidence split.

## Relay development gate

Relay development starts only after all of the following are true:

1. The reviewed Arroyo command catalog accounts for every command form in the
   selected official manual.
2. Every sufficiently documented command has a typed API and offline contract
   tests; documentation gaps are explicit.
3. The intended deployment transport and applicable read-only snapshots have
   passed live qualification on the target controller.
4. Model-, firmware-, sensor-, multi-channel-, and hazardous-command limits are
   recorded.
5. The user reviews the evidence and explicitly declares `pyarroyo`
   satisfactory for relay use.

After that declaration, this README will become the self-contained operator
manual for installation, configuration, exact InfluxDB schema, first upload,
continuous operation, deployment, and troubleshooting.

## Safety

- `Manuals/` is read-only protocol evidence.
- Do not scan or probe arbitrary serial ports.
- Live qualification remains read-only unless a separate bounded procedure is
  explicitly authorized.
- Never record credentials, actual device serial numbers, or private deployment
  values in maintained files.

## Developer's note

The reusable library and relay intentionally have separate Git histories even
while their working trees are colocated. The library owns instrument
communication and normalized values; the future relay will own acquisition
timing, recovery policy, InfluxDB mapping, credentials, and deployment. The
nested repository is ignored rather than recorded as a local-path dependency,
because such a dependency would not be reproducible on another machine.
