"""Poll one Arroyo controller and relay read-only snapshots to InfluxDB."""

from __future__ import annotations

import argparse
import math
import signal
import threading
import time
import tomllib
from datetime import timedelta
from pathlib import Path

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

from pyarroyo import ArroyoClient, ArroyoError, LaserCondition, TECCondition
from supervisor.supervisor_helper import log, log_error, log_warn

print()
print("----- Arroyo Instruments controller -> InfluxDB uploader -----")
print()


# >>>>> app configuration >>>>>

MEASUREMENT = "arroyo"
EX_THRESHOLD = 3

PARSER = argparse.ArgumentParser(description="Relay Arroyo Instruments snapshots to InfluxDB")
PARSER.add_argument("--settings", type=Path, default=Path("settings.toml"))
PARSER.add_argument("--once", action="store_true")
PARSER.add_argument("--dry-run", action="store_true")
ARGS = PARSER.parse_args()

SETTINGS_PATH = ARGS.settings.expanduser().resolve()
with SETTINGS_PATH.open("rb") as f:
    SETTINGS = tomllib.load(f)

INTERVAL_s = SETTINGS["interval_s"]
if (
    isinstance(INTERVAL_s, bool)
    or not isinstance(INTERVAL_s, (int, float))
    or not math.isfinite(INTERVAL_s)
    or INTERVAL_s <= 0
):
    raise ValueError("interval_s must be a positive finite number")
INTERVAL_s = float(INTERVAL_s)

CONNECTION = SETTINGS["connection"]
if not isinstance(CONNECTION, dict):
    raise ValueError("connection must be a TOML table")
HAS_SERIAL = "serial" in CONNECTION
HAS_NETWORK = "network" in CONNECTION
if HAS_SERIAL == HAS_NETWORK:
    raise ValueError("configure exactly one of connection.serial or connection.network")

if HAS_SERIAL:
    SERIAL_SETTINGS = CONNECTION["serial"]
    if not isinstance(SERIAL_SETTINGS, dict):
        raise ValueError("connection.serial must be a TOML table")
    SERIAL_PORT = SERIAL_SETTINGS["port"]
    SERIAL_BAUDRATE = SERIAL_SETTINGS.get("baudrate", 38_400)
    RESPONSE_TIMEOUT_s = SERIAL_SETTINGS.get("response_timeout_s", 1.0)
    if not isinstance(SERIAL_PORT, str) or not SERIAL_PORT.strip():
        raise ValueError("connection.serial.port must be a nonempty string")
    if (
        isinstance(SERIAL_BAUDRATE, bool)
        or not isinstance(SERIAL_BAUDRATE, int)
        or SERIAL_BAUDRATE <= 0
    ):
        raise ValueError("connection.serial.baudrate must be a positive integer")
else:
    NETWORK_SETTINGS = CONNECTION["network"]
    if not isinstance(NETWORK_SETTINGS, dict):
        raise ValueError("connection.network must be a TOML table")
    NETWORK_HOST = NETWORK_SETTINGS["host"]
    NETWORK_PORT = NETWORK_SETTINGS.get("port", 10_001)
    CONNECT_TIMEOUT_s = NETWORK_SETTINGS.get("connect_timeout_s", 3.0)
    RESPONSE_TIMEOUT_s = NETWORK_SETTINGS.get("response_timeout_s", 1.0)
    if not isinstance(NETWORK_HOST, str) or not NETWORK_HOST.strip():
        raise ValueError("connection.network.host must be a nonempty string")
    if (
        isinstance(NETWORK_PORT, bool)
        or not isinstance(NETWORK_PORT, int)
        or not 1 <= NETWORK_PORT <= 65_535
    ):
        raise ValueError("connection.network.port must be between 1 and 65535")
    if (
        isinstance(CONNECT_TIMEOUT_s, bool)
        or not isinstance(CONNECT_TIMEOUT_s, (int, float))
        or not math.isfinite(CONNECT_TIMEOUT_s)
        or CONNECT_TIMEOUT_s <= 0
    ):
        raise ValueError("connection.network.connect_timeout_s must be positive and finite")

if (
    isinstance(RESPONSE_TIMEOUT_s, bool)
    or not isinstance(RESPONSE_TIMEOUT_s, (int, float))
    or not math.isfinite(RESPONSE_TIMEOUT_s)
    or RESPONSE_TIMEOUT_s <= 0
):
    raise ValueError("connection response_timeout_s must be positive and finite")

SNAPSHOT_SETTINGS = []
SEEN_SELECTORS = set()
for SUBSYSTEM, ENTRIES in (("tec", SETTINGS.get("tec", [])), ("laser", SETTINGS.get("laser", []))):
    if not isinstance(ENTRIES, list):
        raise ValueError(f"{SUBSYSTEM} must be an array of TOML tables")
    for ENTRY_INDEX, ENTRY in enumerate(ENTRIES, start=1):
        if not isinstance(ENTRY, dict):
            raise ValueError(f"{SUBSYSTEM}[{ENTRY_INDEX}] must be a TOML table")
        CHANNEL = ENTRY.get("channel")
        if CHANNEL is not None and (
            isinstance(CHANNEL, bool) or not isinstance(CHANNEL, int) or CHANNEL <= 0
        ):
            raise ValueError(f"{SUBSYSTEM}[{ENTRY_INDEX}].channel must be a positive integer")
        SENSOR_INDEX = ENTRY.get("sensor_index")
        if SUBSYSTEM == "tec":
            if SENSOR_INDEX is not None and (
                isinstance(SENSOR_INDEX, bool)
                or not isinstance(SENSOR_INDEX, int)
                or SENSOR_INDEX <= 0
            ):
                raise ValueError(f"tec[{ENTRY_INDEX}].sensor_index must be a positive integer")
        elif "sensor_index" in ENTRY:
            raise ValueError(f"laser[{ENTRY_INDEX}].sensor_index is not supported")
        SELECTOR = (SUBSYSTEM, CHANNEL, SENSOR_INDEX)
        if SELECTOR in SEEN_SELECTORS:
            raise ValueError(f"duplicate snapshot selector: {SELECTOR!r}")
        SEEN_SELECTORS.add(SELECTOR)
        SNAPSHOT_SETTINGS.append(
            {"subsystem": SUBSYSTEM, "channel": CHANNEL, "sensor_index": SENSOR_INDEX}
        )

if not SNAPSHOT_SETTINGS:
    raise ValueError("configure at least one [[tec]] or [[laser]] snapshot")

print(f"Polling interval = {INTERVAL_s:g} s, exception threshold = {EX_THRESHOLD}.")
print(f"Configured snapshots = {len(SNAPSHOT_SETTINGS)}.")
print(f"Settings file = {SETTINGS_PATH}.")
print(f"InfluxDB upload = {'disabled (dry-run)' if ARGS.dry_run else 'enabled'}.")
print()

# <<<<< app configuration <<<<<


# >>> InfluxDB configuration >>>
INFLUXDB_CLIENT = None
INFLUXDB_WRITE_API = None
INFLUXDB_ORG = None
INFLUXDB_BUCKET = None

if not ARGS.dry_run:
    with open("imaq-secret/auth.toml", "rb") as f:
        AUTH = tomllib.load(f)
    INFLUXDB_OPTIONS = dict(AUTH["influxdb"])
    INFLUXDB_BUCKET = INFLUXDB_OPTIONS.pop("bucket")
    INFLUXDB_ORG = INFLUXDB_OPTIONS["org"]
    INFLUXDB_CLIENT = influxdb_client.InfluxDBClient(**INFLUXDB_OPTIONS)
    INFLUXDB_WRITE_API = INFLUXDB_CLIENT.write_api(write_options=SYNCHRONOUS)
    print("InfluxDB client initialized.")
    print()
# <<< InfluxDB configuration <<<


STOP_EVENT = threading.Event()
for SIGNAL_NUMBER in (signal.SIGINT, signal.SIGTERM):
    signal.signal(SIGNAL_NUMBER, lambda _signum, _frame: STOP_EVENT.set())


# >>> Arroyo connection >>>
if HAS_SERIAL:
    SOURCE_CLIENT = ArroyoClient.for_serial(
        SERIAL_PORT,
        baudrate=SERIAL_BAUDRATE,
        response_timeout_s=RESPONSE_TIMEOUT_s,
    )
else:
    SOURCE_CLIENT = ArroyoClient.for_network(
        NETWORK_HOST,
        port=NETWORK_PORT,
        connect_timeout_s=CONNECT_TIMEOUT_s,
        response_timeout_s=RESPONSE_TIMEOUT_s,
    )
# <<< Arroyo connection <<<


exit_code = 0
try:
    SOURCE_CLIENT.connect()
    IDENTITY = SOURCE_CLIENT.identify()
    for IDENTITY_FIELD, IDENTITY_VALUE in (
        ("manufacturer", IDENTITY.manufacturer),
        ("model", IDENTITY.model),
        ("serial_number", IDENTITY.serial_number),
        ("firmware_version", IDENTITY.firmware_version),
        ("build", IDENTITY.build),
    ):
        if not isinstance(IDENTITY_VALUE, str) or not IDENTITY_VALUE:
            raise ValueError(f"identity {IDENTITY_FIELD} must be a nonempty string")
    IDENTITY_KEY = (IDENTITY.manufacturer, IDENTITY.model, IDENTITY.serial_number)
    print(
        "Arroyo controller identified: "
        f"manufacturer={IDENTITY.manufacturer!r}, model={IDENTITY.model!r}, "
        f"firmware={IDENTITY.firmware_version!r}, build={IDENTITY.build!r}."
    )
    print()

    lifetime_exception_count = 0
    iteration = 1
    next_poll = time.monotonic()
    print("Entering main polling loop...")
    print()

    while not STOP_EVENT.is_set():
        msg_il = f"Iteration {iteration}: "

        try:
            CYCLE_IDENTITY = IDENTITY
            for SOURCE_ATTEMPT in range(2):
                try:
                    INFLUXDB_RECORDS = []
                    for SNAPSHOT in SNAPSHOT_SETTINGS:
                        CHANNEL = SNAPSHOT["channel"]
                        SENSOR_INDEX = SNAPSHOT["sensor_index"]

                        if SNAPSHOT["subsystem"] == "tec":
                            SAMPLE = SOURCE_CLIENT.read_tec_sample(
                                channel=CHANNEL,
                                sensor_index=SENSOR_INDEX,
                            )
                            if SAMPLE.channel != CHANNEL or SAMPLE.sensor_index != SENSOR_INDEX:
                                raise ValueError(
                                    "TEC sample selector does not match its settings entry"
                                )
                            if not isinstance(SAMPLE.mode, str) or not SAMPLE.mode:
                                raise ValueError("TEC mode must be a nonempty string")
                            if not isinstance(SAMPLE.output_enabled, bool):
                                raise ValueError("TEC output_enabled must be a boolean")
                            NUMERIC_VALUES = (
                                ("TEC temperature_C", SAMPLE.temperature_C),
                                ("TEC temperature_setpoint_C", SAMPLE.temperature_setpoint_C),
                                ("TEC current_A", SAMPLE.current_A),
                                ("TEC voltage_V", SAMPLE.voltage_V),
                            )
                            for VALUE_NAME, VALUE in NUMERIC_VALUES:
                                if (
                                    isinstance(VALUE, bool)
                                    or not isinstance(VALUE, (int, float))
                                    or not math.isfinite(VALUE)
                                ):
                                    raise ValueError(f"{VALUE_NAME} must be finite and numeric")
                            if (
                                isinstance(SAMPLE.condition, bool)
                                or not isinstance(SAMPLE.condition, int)
                                or SAMPLE.condition < 0
                            ):
                                raise ValueError("TEC condition must be a nonnegative integer")
                            CONDITION = int(SAMPLE.condition)
                            TAGS = {
                                "Manufacturer": CYCLE_IDENTITY.manufacturer,
                                "Model": CYCLE_IDENTITY.model,
                                "Serial number": CYCLE_IDENTITY.serial_number,
                                "Subsystem": "TEC",
                            }
                            if CHANNEL is not None:
                                TAGS["Channel"] = str(CHANNEL)
                            if SENSOR_INDEX is not None:
                                TAGS["Sensor index"] = str(SENSOR_INDEX)
                            FIELDS = {
                                "FirmwareVersion": CYCLE_IDENTITY.firmware_version,
                                "Build": CYCLE_IDENTITY.build,
                                "Mode": SAMPLE.mode,
                                "OutputEnabled": SAMPLE.output_enabled,
                                "Condition": CONDITION,
                                "OutputOnCondition": bool(CONDITION & TECCondition.OUTPUT_ON),
                                "Current[A]": float(SAMPLE.current_A),
                                "Voltage[V]": float(SAMPLE.voltage_V),
                                "Temperature[degC]": float(SAMPLE.temperature_C),
                                "TemperatureSetpoint[degC]": float(SAMPLE.temperature_setpoint_C),
                                "CurrentLimit": bool(CONDITION & TECCondition.CURRENT_LIMIT),
                                "VoltageLimit": bool(CONDITION & TECCondition.VOLTAGE_LIMIT),
                                "SensorLimit": bool(CONDITION & TECCondition.SENSOR_LIMIT),
                                "TemperatureHighLimit": bool(
                                    CONDITION & TECCondition.TEMPERATURE_HIGH_LIMIT
                                ),
                                "TemperatureLowLimit": bool(
                                    CONDITION & TECCondition.TEMPERATURE_LOW_LIMIT
                                ),
                                "SensorShorted": bool(CONDITION & TECCondition.SENSOR_SHORTED),
                                "SensorOpen": bool(CONDITION & TECCondition.SENSOR_OPEN),
                                "TECOpenCircuit": bool(CONDITION & TECCondition.TEC_OPEN_CIRCUIT),
                                "OutOfTolerance": bool(CONDITION & TECCondition.OUT_OF_TOLERANCE),
                                "ThermalRunaway": bool(CONDITION & TECCondition.THERMAL_RUNAWAY),
                            }
                        else:
                            SAMPLE = SOURCE_CLIENT.read_laser_sample(channel=CHANNEL)
                            if SAMPLE.channel != CHANNEL:
                                raise ValueError(
                                    "laser sample selector does not match its settings entry"
                                )
                            if not isinstance(SAMPLE.mode, str) or not SAMPLE.mode:
                                raise ValueError("laser mode must be a nonempty string")
                            if not isinstance(SAMPLE.output_enabled, bool):
                                raise ValueError("laser output_enabled must be a boolean")
                            NUMERIC_VALUES = (
                                ("laser current_A", SAMPLE.current_A),
                                ("laser current_setpoint_A", SAMPLE.current_setpoint_A),
                                ("laser voltage_V", SAMPLE.voltage_V),
                                ("laser voltage_setpoint_V", SAMPLE.voltage_setpoint_V),
                            )
                            for VALUE_NAME, VALUE in NUMERIC_VALUES:
                                if (
                                    isinstance(VALUE, bool)
                                    or not isinstance(VALUE, (int, float))
                                    or not math.isfinite(VALUE)
                                ):
                                    raise ValueError(f"{VALUE_NAME} must be finite and numeric")
                            if (
                                isinstance(SAMPLE.condition, bool)
                                or not isinstance(SAMPLE.condition, int)
                                or SAMPLE.condition < 0
                            ):
                                raise ValueError("laser condition must be a nonnegative integer")
                            CONDITION = int(SAMPLE.condition)
                            TAGS = {
                                "Manufacturer": CYCLE_IDENTITY.manufacturer,
                                "Model": CYCLE_IDENTITY.model,
                                "Serial number": CYCLE_IDENTITY.serial_number,
                                "Subsystem": "Laser",
                            }
                            if CHANNEL is not None:
                                TAGS["Channel"] = str(CHANNEL)
                            FIELDS = {
                                "FirmwareVersion": CYCLE_IDENTITY.firmware_version,
                                "Build": CYCLE_IDENTITY.build,
                                "Mode": SAMPLE.mode,
                                "OutputEnabled": SAMPLE.output_enabled,
                                "Condition": CONDITION,
                                "OutputOnCondition": bool(CONDITION & LaserCondition.OUTPUT_ON),
                                "Current[A]": float(SAMPLE.current_A),
                                "Voltage[V]": float(SAMPLE.voltage_V),
                                "CurrentSetpoint[A]": float(SAMPLE.current_setpoint_A),
                                "VoltageSetpoint[V]": float(SAMPLE.voltage_setpoint_V),
                                "CurrentLimit": bool(CONDITION & LaserCondition.CURRENT_LIMIT),
                                "VoltageLimit": bool(CONDITION & LaserCondition.VOLTAGE_LIMIT),
                                "PhotodiodeCurrentLimit": bool(
                                    CONDITION & LaserCondition.PHOTODIODE_CURRENT_LIMIT
                                ),
                                "PhotodiodePowerLimit": bool(
                                    CONDITION & LaserCondition.PHOTODIODE_POWER_LIMIT
                                ),
                                "InterlockDisabled": bool(
                                    CONDITION & LaserCondition.INTERLOCK_DISABLED
                                ),
                                "OpenCircuit": bool(CONDITION & LaserCondition.OPEN_CIRCUIT),
                                "ShortCircuit": bool(CONDITION & LaserCondition.SHORT_CIRCUIT),
                                "OutOfTolerance": bool(CONDITION & LaserCondition.OUT_OF_TOLERANCE),
                                "ResistanceLimit": bool(
                                    CONDITION & LaserCondition.RESISTANCE_LIMIT
                                ),
                                "TemperatureLimit": bool(
                                    CONDITION & LaserCondition.TEMPERATURE_LIMIT
                                ),
                            }

                        if (
                            SAMPLE.observed_at.tzinfo is None
                            or SAMPLE.observed_at.utcoffset() is None
                        ):
                            raise ValueError("observed_at must be an aware datetime")
                        if SAMPLE.observed_at.utcoffset() != timedelta(0):
                            raise ValueError("observed_at must use UTC")
                        INFLUXDB_RECORDS.append(
                            {
                                "measurement": MEASUREMENT,
                                "tags": TAGS,
                                "fields": FIELDS,
                                "time": SAMPLE.observed_at,
                            }
                        )
                    break
                except Exception as ex:
                    if SOURCE_ATTEMPT == 1:
                        raise
                    log_error(msg_il)
                    log_error(f"Arroyo query failed: {type(ex).__name__}: {ex}")
                    log_warn(
                        "Re-establishing Arroyo connection and retrying the complete batch once..."
                    )
                    SOURCE_CLIENT.reconnect()
                    CYCLE_IDENTITY = SOURCE_CLIENT.identify()
                    if (
                        CYCLE_IDENTITY.manufacturer,
                        CYCLE_IDENTITY.model,
                        CYCLE_IDENTITY.serial_number,
                    ) != IDENTITY_KEY:
                        raise ArroyoError(
                            "controller identity changed after reconnect; refusing to continue"
                        ) from ex
                    log_warn("Arroyo reconnection and identity verification succeeded.")

            if ARGS.dry_run:
                log(msg_il + f"Dry-run records, not uploaded: {INFLUXDB_RECORDS!r}")
            else:
                INFLUXDB_WRITE_API.write(
                    bucket=INFLUXDB_BUCKET,
                    org=INFLUXDB_ORG,
                    record=INFLUXDB_RECORDS,
                )
                log(msg_il + f"Uploaded {len(INFLUXDB_RECORDS)} complete snapshot record(s).")
        except Exception as ex:
            if ARGS.once:
                raise
            lifetime_exception_count += 1
            log_error(msg_il)
            log_error(
                "Error during measurement/upload "
                f"({lifetime_exception_count}/{EX_THRESHOLD} lifetime): "
                f"{type(ex).__name__}: {ex}"
            )
            if lifetime_exception_count >= EX_THRESHOLD:
                log_error("Exception threshold reached. Raising to Supervisor.")
                raise

        if ARGS.once:
            break

        iteration += 1
        next_poll += INTERVAL_s
        now = time.monotonic()
        if next_poll <= now:
            next_poll = now + INTERVAL_s
        STOP_EVENT.wait(next_poll - now)
except KeyboardInterrupt:
    log_warn("KeyboardInterrupt received.")
    exit_code = 130
except Exception as ex:
    log_error(f"Fatal collector error: {type(ex).__name__}: {ex}")
    exit_code = 1
finally:
    log("Shutting down gracefully...", end=" ")
    try:
        SOURCE_CLIENT.close()
    except Exception as ex:
        log_warn(f"Arroyo cleanup failed: {type(ex).__name__}: {ex}")
    try:
        if INFLUXDB_WRITE_API is not None:
            INFLUXDB_WRITE_API.close()
    except Exception as ex:
        log_warn(f"InfluxDB write API cleanup failed: {type(ex).__name__}: {ex}")
    try:
        if INFLUXDB_CLIENT is not None:
            INFLUXDB_CLIENT.close()
    except Exception as ex:
        log_warn(f"InfluxDB client cleanup failed: {type(ex).__name__}: {ex}")
    print("Done")

if exit_code:
    raise SystemExit(exit_code)
