# arroyo-to-influxdb

Read configured TEC and laser snapshots from one Arroyo Instruments controller
through `pyarroyo`, then write them to InfluxDB for Grafana.

The relay never changes outputs, setpoints, limits, calibration, scripts, saved
configurations, or event/error registers. An explicitly configured
multi-channel snapshot temporarily selects its requested channel and restores
the previously active channel before returning.

## Requirements

- A powered Arroyo controller connected via USB virtual-COM, RS-232 port, or TCP endpoint.
- Git and [`uv`](https://docs.astral.sh/uv/)
- Access to the private `imaq-secret` repository

## Installation

1. Clone the repository and its submodules into the standard project directory:

    ```bash
    cd "$HOME/Projects"
    git clone --recurse-submodules https://github.com/SinclairQuantumLab/arroyo-to-influxdb.git
    cd arroyo-to-influxdb
    ```

    > **NOTE**: the `--recurse-submodules` option clones the `pyarroyo` library
    > and [`imaq-secret`](https://github.com/SinclairQuantumLab/imaq-secret.git)
    > credential repository together at the expected locations in this repo.

2. Install the project dependencies:

    ```bash
    uv sync
    ```

3. Create and edit a `settings.toml` file from the
   `settings.toml.template` template:

    ```bash
    cp settings.toml.template settings.toml
    ```

    `settings.toml` is ignored by Git. Configure exactly one connection table
    and at least one `[[tec]]` or `[[laser]]` snapshot. This serial example
    reads one TEC snapshot from the currently active channel and laser channel
    1:

    ```toml
    # Seconds between polling-cycle start times.
    interval_s = 30

    [connection.serial]
    port = "<PORT>"          # e.g. "COM4" or "/dev/ttyUSB0"
    baudrate = 38400         # Arroyo USB virtual-COM default
    response_timeout_s = 1.0

    [[tec]]
    # channel = 1            # Omit to leave the active channel unchanged.
    # sensor_index = 1       # Omit when the controller has one TEC sensor.

    [[laser]]
    channel = 1
    ```

    For a direct TCP connection, replace `[connection.serial]` with:

    ```toml
    [connection.network]
    host = "<HOST>"
    port = 10001
    connect_timeout_s = 3.0
    response_timeout_s = 1.0
    ```

    Repeat `[[tec]]` or `[[laser]]` for every required channel. The relay reads
    all TEC entries in their listed order, followed by all laser entries in
    their listed order. Direct RS-232 commonly uses 9600 baud; select the value
    required by the controller configuration instead of assuming the USB
    default.

4. Perform initial testing. First read the real controller and print the exact
   records without uploading them to InfluxDB:

    ```bash
    uv run python main.py --settings settings.toml --once --dry-run
    ```

    Review the identity, subsystem/channel tags, fields, units, condition
    flags, and timestamps. This is real device I/O, even though the upload is
    disabled.

    After the dry-run output has been reviewed and the upload is explicitly
    authorized, upload one snapshot:

    ```bash
    uv run python main.py --settings settings.toml --once
    ```

    The relay loads `imaq-secret/auth.toml` and initializes the InfluxDB client
    at startup, including in dry-run mode. Query the new points back from
    InfluxDB and verify the schema and timestamps before starting continuous
    operation.

5. Optional: after foreground validation, install the matching template from
   `supervisor/`. It starts the prepared environment through `Startup.ps1` or
   `Startup.sh` and has `autostart=false` until deliberately enabled.

## Usage

Run continuously:

```bash
uv run python main.py --settings settings.toml
```

`--settings PATH` defaults to `settings.toml`. `--once` performs one cycle and
exits. `--dry-run` skips InfluxDB writes; it is valid with either one-shot or
continuous operation.

Stop a foreground process with `Ctrl+C`. Normal shutdown closes the Arroyo and
InfluxDB clients but does not send `LOCAL` or another instrument command.

The first cycle starts immediately, and `interval_s` controls the time between
later cycle starts. The third unresolved lifetime source or upload failure exits
the process so Supervisor can restart it; successful cycles do not reset that
counter. A one-shot failure exits nonzero immediately.

## Data written to InfluxDB

Each configured snapshot becomes one point in the fixed `arroyo` measurement.
All fields for one point come from one sequential `pyarroyo` snapshot. TEC and
laser snapshots, and their individual protocol queries, are not simultaneous.
The relay uploads all configured points together in one synchronous request
only after every snapshot in the cycle succeeds; it never uploads a partial
batch.

### Tags

| Exact InfluxDB name | Type or value | Presence |
| --- | --- | --- |
| `Manufacturer` | string from `*IDN?` | always |
| `Model` | string from `*IDN?` | always |
| `Serial number` | string from `*IDN?` | always |
| `Subsystem` | `TEC` or `Laser` | always |
| `Channel` | configured positive integer encoded as a string | only when `channel` is configured |
| `Sensor index` | configured positive integer encoded as a string | only for a TEC entry with `sensor_index` |

Omitting `channel` means “read the controller's current channel without
selecting one”; it does not create a `Channel=default` tag. Model, subsystem,
channel, and sensor identity are tags because they select stable series. The
firmware and build strings are fields so a firmware update does not silently
create a new series.

### Common fields

| Exact InfluxDB name | Type | Meaning |
| --- | --- | --- |
| `FirmwareVersion` | string | firmware field from `*IDN?` |
| `Build` | string | build field from `*IDN?` |
| `Mode` | string | reported TEC or laser operating mode |
| `OutputEnabled` | boolean | result of the subsystem output-state query |
| `Condition` | integer | complete non-destructive condition-register word |
| `OutputOnCondition` | boolean | `OUTPUT_ON` bit decoded from `Condition` |
| `Current[A]` | float | measured TEC or laser current in amperes |
| `Voltage[V]` | float | measured TEC or laser voltage in volts |

The raw `Condition` integer preserves defined and future unknown bits. Decoded
booleans make the currently documented bits directly usable in Grafana.
`OutputEnabled` and `OutputOnCondition` intentionally remain separate readings.

### TEC-only fields

| Exact InfluxDB name | Type |
| --- | --- |
| `Temperature[degC]` | float |
| `TemperatureSetpoint[degC]` | float |
| `CurrentLimit` | boolean |
| `VoltageLimit` | boolean |
| `SensorLimit` | boolean |
| `TemperatureHighLimit` | boolean |
| `TemperatureLowLimit` | boolean |
| `SensorShorted` | boolean |
| `SensorOpen` | boolean |
| `TECOpenCircuit` | boolean |
| `OutOfTolerance` | boolean |
| `ThermalRunaway` | boolean |

### Laser-only fields

| Exact InfluxDB name | Type |
| --- | --- |
| `CurrentSetpoint[A]` | float |
| `VoltageSetpoint[V]` | float |
| `CurrentLimit` | boolean |
| `VoltageLimit` | boolean |
| `PhotodiodeCurrentLimit` | boolean |
| `PhotodiodePowerLimit` | boolean |
| `InterlockDisabled` | boolean |
| `OpenCircuit` | boolean |
| `ShortCircuit` | boolean |
| `OutOfTolerance` | boolean |
| `ResistanceLimit` | boolean |
| `TemperatureLimit` | boolean |

Laser current values are converted by `pyarroyo` from the manual's milliamperes
to amperes before they reach the relay. TEC temperature is reported in degrees
Celsius. No relay-side engineering-unit conversion is otherwise applied.

### Timestamps and batch validity

Each point uses the aware UTC host timestamp captured by `pyarroyo` immediately
after the final protocol query for that specific snapshot. It does not use the
InfluxDB write time or one shared cycle timestamp.

The relay maps the typed, unit-normalized `pyarroyo` samples directly. It sends
the batch only after every configured snapshot has been acquired and mapped, so
no point from an incomplete batch is uploaded.

## Troubleshooting

- If settings fail to load, confirm there is one connection table and at least
  one snapshot table with values supported by the selected controller.
- If a serial connection fails, confirm the explicitly selected port and baud
  rate. The relay never searches other ports. Arroyo USB virtual-COM commonly
  uses 38400 baud, while direct RS-232 commonly uses 9600 baud.
- If a network connection fails, verify the configured host, TCP port 10001 or
  the controller-specific alternative, routing, and firewall. The documented
  direct interface is plain trusted-LAN TCP without TLS or authentication.
- If a TEC, laser, channel, or sensor query is rejected, verify that the exact
  subsystem and feature exist on that model and firmware. Remove unsupported
  snapshot entries rather than probing commands automatically.
- If active-channel restoration fails, the snapshot is rejected. Stop
  continuous operation and verify the front-panel channel before retrying.
- If a controller read fails, the relay reconnects immediately, verifies the
  same manufacturer, model, and serial number, and retries the complete batch
  once. An identity mismatch or second failure leaves the cycle unresolved;
  verify the configured endpoint rather than trying other serial ports.
- If uploads fail, update both submodules, verify `imaq-secret/auth.toml`, and
  return to `--once --dry-run` to separate controller access from InfluxDB.
- If Supervisor cannot start the app, run the platform startup wrapper manually
  and inspect the configured stderr log. Do not make the wrapper synchronize or
  modify the environment on restart.

## Validation status

`pyarroyo` has 347 passing offline tests, including manual-derived coverage and
wire-contract tests for 290 of the selected manual's 296 normalized forms. The
remaining six laser
calibration forms are named but not syntactically defined by that manual and
fail before I/O. The manual's undefined `TEC:VTE?` cross-reference and the
manufacturer sample's undocumented `TEC:VBULK?` extension are recorded as
evidence gaps rather than silently added to the public contract. None of those
forms is used by this relay; see
[`pyarroyo/docs/command-evidence.md`](pyarroyo/docs/command-evidence.md).

The relay has 15 offline whole-script tests covering its settings, both
connection factories, exact TEC/laser schema, timestamps, complete batches,
recovery, identity continuity, failure accounting, polling deadlines, cleanup,
and startup/Supervisor contracts. No attached Arroyo controller, InfluxDB
upload, startup wrapper, or Supervisor service has yet been validated in this
workspace. Manual review, library and relay offline tests, live read-only source
checks, one-point upload verification, and continuous-service validation remain
separate evidence levels. Update this section with model/firmware-neutral
evidence only; never commit actual endpoint values or device serial numbers.

## Developer's note

- `pyarroyo/` owns transport framing, the manual command API, identity parsing,
  normalized TEC/laser samples, condition flags, and channel restoration.
- `main.py` is one direct, top-level synchronous sequence for settings,
  credentials, connection, acquisition, mapping, upload, scheduling, signals,
  and cleanup. It contains no application classes, helper functions, or
  `main()` wrapper.
- One persistent `ArroyoClient` reads configured TEC snapshots in listed order,
  then configured laser snapshots in listed order. Source recovery discards an incomplete batch,
  reconnects and re-identifies the controller, then retries that idempotent
  snapshot batch once; an InfluxDB failure does not reconnect the controller.
- Polling uses monotonic cycle-start deadlines. An overrun schedules the
  next cycle one full interval later instead of issuing catch-up reads.
- `supervisor/supervisor_helper.py` and the startup/config templates follow
  the current Sinclair relay family while remaining repository-local.
- Offline tests execute the production file with
  `runpy.run_path(..., run_name="__main__")` and replace only its source,
  InfluxDB, signal, and timing boundaries.
- Maintainer handoff, settled decisions, and sanitized live-evidence records
  are indexed in [`docs/README.md`](docs/README.md).
- The closest implementation references are the current
  `hicube-neo-to-influxdb` and `seas-pump-to-influxdb` snapshot relays, with
  `LFI3751-to-influxdb` for serial temperature-controller precedent and
  `koheron_ctl-to-influxdb` for multiple laser/TEC records. This relay does not
  adopt Koheron's serial-port discovery behavior.
