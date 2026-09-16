"""Poll one Arroyo controller and relay read-only snapshots to InfluxDB."""

from __future__ import annotations

import argparse
import signal
import time
import tomllib
from pathlib import Path

from pyarroyo import ArroyoClient, ArroyoError, LaserCondition, TECCondition
from supervisor.supervisor_helper import log, log_error, log_warn

print()
print("----- Arroyo Instruments controller -> InfluxDB uploader -----")
print()


# >>>>> app configuration >>>>>

MEASUREMENT = "arroyo"
EX_THRESHOLD = 3

# >>> load & parse config files >>>
PARSER = argparse.ArgumentParser(
    description="Relay Arroyo Instruments snapshots to InfluxDB"
)
PARSER.add_argument("--settings", type=Path, default=Path("settings.toml"))
PARSER.add_argument("--once", action="store_true")
PARSER.add_argument("--dry-run", action="store_true")
ARGS = PARSER.parse_args()

SETTINGS_PATH = ARGS.settings.expanduser().resolve()
with SETTINGS_PATH.open("rb") as f:
    SETTINGS = tomllib.load(f)

INTERVAL_s = SETTINGS["interval_s"]
CONNECTION = SETTINGS["connection"]
SERIAL_SETTINGS = CONNECTION.get("serial")
NETWORK_SETTINGS = CONNECTION.get("network")
TEC_SETTINGS = SETTINGS.get("tec", [])
LASER_SETTINGS = SETTINGS.get("laser", [])
# <<< load & parse config files <<<

print(f"Polling interval = {INTERVAL_s} s, exception threshold = {EX_THRESHOLD}.")
print(f"Configured snapshots = {len(TEC_SETTINGS) + len(LASER_SETTINGS)}.")
print(f"Settings file = {SETTINGS_PATH}.")
print(f"InfluxDB upload = {'disabled (dry-run)' if ARGS.dry_run else 'enabled'}.")
print()

# <<<<< app configuration <<<<<


# >>> load IMAQ secret >>>
import tomllib
with open("imaq-secret/auth.toml", "rb") as f:
    AUTH = tomllib.load(f)
# <<< load IMAQ secret <<<


# Use Python's normal Ctrl+C exception path for both termination signals.
for SIGNAL_NUMBER in (signal.SIGINT, signal.SIGTERM):
    signal.signal(SIGNAL_NUMBER, signal.default_int_handler)


# >>> InfluxDB configuration >>>
import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS
# Initialize the InfluxDB Client and the Write API
INFLUXDB_CLIENT = influxdb_client.InfluxDBClient(**AUTH["influxdb"])
INFLUXDB_WRITE_API = INFLUXDB_CLIENT.write_api(write_options=SYNCHRONOUS)
INFLUXDB_QUERY_API = INFLUXDB_CLIENT.query_api()
INFLUXDB_ORG = AUTH["influxdb"]["org"]; INFLUXDB_BUCKET = AUTH["influxdb"]["bucket"]
print(f"InfluxDB client initialized for org='{INFLUXDB_ORG}', bucket='{INFLUXDB_BUCKET}'.")
print()
# <<< InfluxDB configuration <<<


# >>> Arroyo connection >>>
if SERIAL_SETTINGS is not None:
    SOURCE_CLIENT = ArroyoClient.for_serial(
        SERIAL_SETTINGS["port"],
        baudrate=SERIAL_SETTINGS.get("baudrate", 38_400),
        response_timeout_s=SERIAL_SETTINGS.get("response_timeout_s", 1.0),
    )
else:
    SOURCE_CLIENT = ArroyoClient.for_network(
        NETWORK_SETTINGS["host"],
        port=NETWORK_SETTINGS.get("port", 10_001),
        connect_timeout_s=NETWORK_SETTINGS.get("connect_timeout_s", 3.0),
        response_timeout_s=NETWORK_SETTINGS.get("response_timeout_s", 1.0),
    )
# <<< Arroyo connection <<<


exit_code = 0
try:
    SOURCE_CLIENT.connect()
    IDENTITY = SOURCE_CLIENT.identify()
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

    while True:
        msg_il = f"Iteration {iteration}: "

        try:
            # >>>>> query readings >>>>>

            CYCLE_IDENTITY = IDENTITY
            for SOURCE_ATTEMPT in range(2):
                try:
                    INFLUXDB_RECORDS = []
                    for SUBSYSTEM, SUBSYSTEM_SETTINGS in (
                        ("tec", TEC_SETTINGS),
                        ("laser", LASER_SETTINGS),
                    ):
                        for SNAPSHOT in SUBSYSTEM_SETTINGS:
                            CHANNEL = SNAPSHOT.get("channel")
                            SENSOR_INDEX = SNAPSHOT.get("sensor_index")

                            if SUBSYSTEM == "tec":
                                SAMPLE = SOURCE_CLIENT.read_tec_sample(
                                    channel=CHANNEL,
                                    sensor_index=SENSOR_INDEX,
                                )
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
                                    "OutputOnCondition": bool(
                                        CONDITION & TECCondition.OUTPUT_ON
                                    ),
                                    "Current[A]": SAMPLE.current_A,
                                    "Voltage[V]": SAMPLE.voltage_V,
                                    "Temperature[degC]": SAMPLE.temperature_C,
                                    "TemperatureSetpoint[degC]": (
                                        SAMPLE.temperature_setpoint_C
                                    ),
                                    "CurrentLimit": bool(
                                        CONDITION & TECCondition.CURRENT_LIMIT
                                    ),
                                    "VoltageLimit": bool(
                                        CONDITION & TECCondition.VOLTAGE_LIMIT
                                    ),
                                    "SensorLimit": bool(
                                        CONDITION & TECCondition.SENSOR_LIMIT
                                    ),
                                    "TemperatureHighLimit": bool(
                                        CONDITION & TECCondition.TEMPERATURE_HIGH_LIMIT
                                    ),
                                    "TemperatureLowLimit": bool(
                                        CONDITION & TECCondition.TEMPERATURE_LOW_LIMIT
                                    ),
                                    "SensorShorted": bool(
                                        CONDITION & TECCondition.SENSOR_SHORTED
                                    ),
                                    "SensorOpen": bool(
                                        CONDITION & TECCondition.SENSOR_OPEN
                                    ),
                                    "TECOpenCircuit": bool(
                                        CONDITION & TECCondition.TEC_OPEN_CIRCUIT
                                    ),
                                    "OutOfTolerance": bool(
                                        CONDITION & TECCondition.OUT_OF_TOLERANCE
                                    ),
                                    "ThermalRunaway": bool(
                                        CONDITION & TECCondition.THERMAL_RUNAWAY
                                    ),
                                }
                            else:
                                SAMPLE = SOURCE_CLIENT.read_laser_sample(
                                    channel=CHANNEL
                                )
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
                                    "OutputOnCondition": bool(
                                        CONDITION & LaserCondition.OUTPUT_ON
                                    ),
                                    "Current[A]": SAMPLE.current_A,
                                    "Voltage[V]": SAMPLE.voltage_V,
                                    "CurrentSetpoint[A]": SAMPLE.current_setpoint_A,
                                    "VoltageSetpoint[V]": SAMPLE.voltage_setpoint_V,
                                    "CurrentLimit": bool(
                                        CONDITION & LaserCondition.CURRENT_LIMIT
                                    ),
                                    "VoltageLimit": bool(
                                        CONDITION & LaserCondition.VOLTAGE_LIMIT
                                    ),
                                    "PhotodiodeCurrentLimit": bool(
                                        CONDITION
                                        & LaserCondition.PHOTODIODE_CURRENT_LIMIT
                                    ),
                                    "PhotodiodePowerLimit": bool(
                                        CONDITION
                                        & LaserCondition.PHOTODIODE_POWER_LIMIT
                                    ),
                                    "InterlockDisabled": bool(
                                        CONDITION & LaserCondition.INTERLOCK_DISABLED
                                    ),
                                    "OpenCircuit": bool(
                                        CONDITION & LaserCondition.OPEN_CIRCUIT
                                    ),
                                    "ShortCircuit": bool(
                                        CONDITION & LaserCondition.SHORT_CIRCUIT
                                    ),
                                    "OutOfTolerance": bool(
                                        CONDITION & LaserCondition.OUT_OF_TOLERANCE
                                    ),
                                    "ResistanceLimit": bool(
                                        CONDITION & LaserCondition.RESISTANCE_LIMIT
                                    ),
                                    "TemperatureLimit": bool(
                                        CONDITION & LaserCondition.TEMPERATURE_LIMIT
                                    ),
                                }

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

            # <<<<< query readings <<<<<

            if ARGS.dry_run:
                log(msg_il + f"Dry-run records, not uploaded: {INFLUXDB_RECORDS!r}")
            else:
                INFLUXDB_WRITE_API.write(
                    bucket=INFLUXDB_BUCKET,
                    org=INFLUXDB_ORG,
                    record=INFLUXDB_RECORDS,
                )
                log(
                    msg_il
                    + f"Uploaded {len(INFLUXDB_RECORDS)} complete snapshot record(s)."
                )
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
        time.sleep(next_poll - now)
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
