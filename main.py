"""Poll one Arroyo controller and relay read-only snapshots to InfluxDB."""

from __future__ import annotations

import argparse
import math
import signal
import threading
import time
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

from pyarroyo import (
    ArroyoClient,
    ArroyoError,
    InstrumentIdentity,
    LaserCondition,
    LaserSample,
    TECCondition,
    TECSample,
)
from supervisor.supervisor_helper import log, log_error, log_warn

MEASUREMENT = "arroyo"
EX_THRESHOLD = 3
PROJECT_DIR = Path(__file__).resolve().parent
AUTH_PATH = PROJECT_DIR / "imaq-secret" / "auth.toml"


class SettingsError(ValueError):
    """Report a settings value that violates the relay contract."""


class SampleValidationError(ValueError):
    """Report a source sample that cannot become a complete InfluxDB point."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsError(f"{name} must be a TOML table")
    return value


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{name} must be a nonempty string")
    return value.strip()


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{name} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise SettingsError(f"{name} must be a positive finite number")
    return result


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SettingsError(f"{name} must be a positive integer")
    return value


def _optional_positive_int(table: Mapping[str, Any], key: str, name: str) -> int | None:
    value = table.get(key)
    return None if value is None else _positive_int(value, name)


def load_settings(path: Path) -> dict[str, Any]:
    """Load and validate one trusted local relay settings file."""

    with path.open("rb") as settings_file:
        raw = tomllib.load(settings_file)

    interval_s = _positive_float(raw.get("interval_s"), "interval_s")
    connection = _mapping(raw.get("connection"), "connection")
    has_serial = "serial" in connection
    has_network = "network" in connection
    if has_serial == has_network:
        raise SettingsError("configure exactly one of connection.serial or connection.network")

    if has_serial:
        serial_settings = _mapping(connection["serial"], "connection.serial")
        connection_settings: dict[str, Any] = {
            "kind": "serial",
            "port": _nonempty_string(serial_settings.get("port"), "connection.serial.port"),
            "baudrate": _positive_int(
                serial_settings.get("baudrate", 38_400), "connection.serial.baudrate"
            ),
            "response_timeout_s": _positive_float(
                serial_settings.get("response_timeout_s", 1.0),
                "connection.serial.response_timeout_s",
            ),
        }
    else:
        network_settings = _mapping(connection["network"], "connection.network")
        port = _positive_int(network_settings.get("port", 10_001), "connection.network.port")
        if port > 65_535:
            raise SettingsError("connection.network.port must be at most 65535")
        connection_settings = {
            "kind": "network",
            "host": _nonempty_string(network_settings.get("host"), "connection.network.host"),
            "port": port,
            "connect_timeout_s": _positive_float(
                network_settings.get("connect_timeout_s", 3.0),
                "connection.network.connect_timeout_s",
            ),
            "response_timeout_s": _positive_float(
                network_settings.get("response_timeout_s", 1.0),
                "connection.network.response_timeout_s",
            ),
        }

    snapshots: list[dict[str, Any]] = []
    seen_selectors: set[tuple[str, int | None, int | None]] = set()
    for subsystem in ("tec", "laser"):
        entries = raw.get(subsystem, [])
        if not isinstance(entries, list):
            raise SettingsError(f"{subsystem} must be an array of TOML tables")
        for index, entry_value in enumerate(entries, start=1):
            entry = _mapping(entry_value, f"{subsystem}[{index}]")
            channel = _optional_positive_int(entry, "channel", f"{subsystem}[{index}].channel")
            sensor_index = None
            if subsystem == "tec":
                sensor_index = _optional_positive_int(
                    entry, "sensor_index", f"tec[{index}].sensor_index"
                )
            elif "sensor_index" in entry:
                raise SettingsError(f"laser[{index}].sensor_index is not supported")
            selector = (subsystem, channel, sensor_index)
            if selector in seen_selectors:
                raise SettingsError(f"duplicate snapshot selector: {selector!r}")
            seen_selectors.add(selector)
            snapshots.append(
                {"subsystem": subsystem, "channel": channel, "sensor_index": sensor_index}
            )

    if not snapshots:
        raise SettingsError("configure at least one [[tec]] or [[laser]] snapshot")
    return {
        "interval_s": interval_s,
        "connection": connection_settings,
        "snapshots": snapshots,
    }


def create_source_client(connection: Mapping[str, Any]) -> ArroyoClient:
    """Construct a closed Arroyo client for the validated connection settings."""

    if connection["kind"] == "serial":
        return ArroyoClient.for_serial(
            connection["port"],
            baudrate=connection["baudrate"],
            response_timeout_s=connection["response_timeout_s"],
        )
    return ArroyoClient.for_network(
        connection["host"],
        port=connection["port"],
        connect_timeout_s=connection["connect_timeout_s"],
        response_timeout_s=connection["response_timeout_s"],
    )


def _sample_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SampleValidationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SampleValidationError(f"{name} must be finite")
    return result


def _sample_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SampleValidationError(f"{name} must be a nonempty string")
    return value


def _sample_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise SampleValidationError(f"{name} must be a boolean")
    return value


def _sample_time(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SampleValidationError("observed_at must be an aware datetime")
    if value.utcoffset() != timedelta(0):
        raise SampleValidationError("observed_at must use UTC")
    return value


def _condition_word(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SampleValidationError("condition must be an integer condition word")
    result = int(value)
    if result < 0:
        raise SampleValidationError("condition cannot be negative")
    return result


def _identity_tags(identity: InstrumentIdentity, subsystem: str) -> dict[str, str]:
    return {
        "Manufacturer": _sample_string(identity.manufacturer, "identity.manufacturer"),
        "Model": _sample_string(identity.model, "identity.model"),
        "Serial number": _sample_string(identity.serial_number, "identity.serial_number"),
        "Subsystem": subsystem,
    }


def tec_record(
    identity: InstrumentIdentity,
    sample: TECSample,
    selector: Mapping[str, Any],
) -> dict[str, Any]:
    """Map one validated TEC sample to the fixed InfluxDB schema."""

    channel = selector["channel"]
    sensor_index = selector["sensor_index"]
    if sample.channel != channel or sample.sensor_index != sensor_index:
        raise SampleValidationError("TEC sample selector does not match its settings entry")
    tags = _identity_tags(identity, "TEC")
    if channel is not None:
        tags["Channel"] = str(channel)
    if sensor_index is not None:
        tags["Sensor index"] = str(sensor_index)
    condition = _condition_word(sample.condition)
    fields = {
        "FirmwareVersion": _sample_string(identity.firmware_version, "identity.firmware_version"),
        "Build": _sample_string(identity.build, "identity.build"),
        "Mode": _sample_string(sample.mode, "TEC mode"),
        "OutputEnabled": _sample_bool(sample.output_enabled, "TEC output_enabled"),
        "Condition": condition,
        "OutputOnCondition": bool(condition & TECCondition.OUTPUT_ON),
        "Current[A]": _sample_float(sample.current_A, "TEC current_A"),
        "Voltage[V]": _sample_float(sample.voltage_V, "TEC voltage_V"),
        "Temperature[degC]": _sample_float(sample.temperature_C, "TEC temperature_C"),
        "TemperatureSetpoint[degC]": _sample_float(
            sample.temperature_setpoint_C, "TEC temperature_setpoint_C"
        ),
        "CurrentLimit": bool(condition & TECCondition.CURRENT_LIMIT),
        "VoltageLimit": bool(condition & TECCondition.VOLTAGE_LIMIT),
        "SensorLimit": bool(condition & TECCondition.SENSOR_LIMIT),
        "TemperatureHighLimit": bool(condition & TECCondition.TEMPERATURE_HIGH_LIMIT),
        "TemperatureLowLimit": bool(condition & TECCondition.TEMPERATURE_LOW_LIMIT),
        "SensorShorted": bool(condition & TECCondition.SENSOR_SHORTED),
        "SensorOpen": bool(condition & TECCondition.SENSOR_OPEN),
        "TECOpenCircuit": bool(condition & TECCondition.TEC_OPEN_CIRCUIT),
        "OutOfTolerance": bool(condition & TECCondition.OUT_OF_TOLERANCE),
        "ThermalRunaway": bool(condition & TECCondition.THERMAL_RUNAWAY),
    }
    return {
        "measurement": MEASUREMENT,
        "tags": tags,
        "fields": fields,
        "time": _sample_time(sample.observed_at),
    }


def laser_record(
    identity: InstrumentIdentity,
    sample: LaserSample,
    selector: Mapping[str, Any],
) -> dict[str, Any]:
    """Map one validated laser sample to the fixed InfluxDB schema."""

    channel = selector["channel"]
    if sample.channel != channel:
        raise SampleValidationError("laser sample selector does not match its settings entry")
    tags = _identity_tags(identity, "Laser")
    if channel is not None:
        tags["Channel"] = str(channel)
    condition = _condition_word(sample.condition)
    fields = {
        "FirmwareVersion": _sample_string(identity.firmware_version, "identity.firmware_version"),
        "Build": _sample_string(identity.build, "identity.build"),
        "Mode": _sample_string(sample.mode, "laser mode"),
        "OutputEnabled": _sample_bool(sample.output_enabled, "laser output_enabled"),
        "Condition": condition,
        "OutputOnCondition": bool(condition & LaserCondition.OUTPUT_ON),
        "Current[A]": _sample_float(sample.current_A, "laser current_A"),
        "Voltage[V]": _sample_float(sample.voltage_V, "laser voltage_V"),
        "CurrentSetpoint[A]": _sample_float(sample.current_setpoint_A, "laser current_setpoint_A"),
        "VoltageSetpoint[V]": _sample_float(sample.voltage_setpoint_V, "laser voltage_setpoint_V"),
        "CurrentLimit": bool(condition & LaserCondition.CURRENT_LIMIT),
        "VoltageLimit": bool(condition & LaserCondition.VOLTAGE_LIMIT),
        "PhotodiodeCurrentLimit": bool(condition & LaserCondition.PHOTODIODE_CURRENT_LIMIT),
        "PhotodiodePowerLimit": bool(condition & LaserCondition.PHOTODIODE_POWER_LIMIT),
        "InterlockDisabled": bool(condition & LaserCondition.INTERLOCK_DISABLED),
        "OpenCircuit": bool(condition & LaserCondition.OPEN_CIRCUIT),
        "ShortCircuit": bool(condition & LaserCondition.SHORT_CIRCUIT),
        "OutOfTolerance": bool(condition & LaserCondition.OUT_OF_TOLERANCE),
        "ResistanceLimit": bool(condition & LaserCondition.RESISTANCE_LIMIT),
        "TemperatureLimit": bool(condition & LaserCondition.TEMPERATURE_LIMIT),
    }
    return {
        "measurement": MEASUREMENT,
        "tags": tags,
        "fields": fields,
        "time": _sample_time(sample.observed_at),
    }


def acquire_records(
    client: ArroyoClient,
    identity: InstrumentIdentity,
    snapshots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Acquire and validate the complete configured snapshot batch."""

    records: list[dict[str, Any]] = []
    for selector in snapshots:
        if selector["subsystem"] == "tec":
            sample = client.read_tec_sample(
                channel=selector["channel"], sensor_index=selector["sensor_index"]
            )
            records.append(tec_record(identity, sample, selector))
        else:
            sample = client.read_laser_sample(channel=selector["channel"])
            records.append(laser_record(identity, sample, selector))
    return records


def _identity_key(identity: InstrumentIdentity) -> tuple[str, str, str]:
    return identity.manufacturer, identity.model, identity.serial_number


def acquire_with_recovery(
    client: ArroyoClient,
    identity: InstrumentIdentity,
    snapshots: Sequence[Mapping[str, Any]],
    message_prefix: str,
) -> list[dict[str, Any]]:
    """Reconnect, verify identity, and retry one unresolved source batch once."""

    try:
        return acquire_records(client, identity, snapshots)
    except (ArroyoError, SampleValidationError) as error:
        log_error(message_prefix)
        log_error(f"Arroyo query failed: {type(error).__name__}: {error}")
        log_warn("Re-establishing Arroyo connection and retrying the complete batch once...")
        client.reconnect()
        recovered_identity = client.identify()
        if _identity_key(recovered_identity) != _identity_key(identity):
            raise ArroyoError(
                "controller identity changed after reconnect; refusing to continue"
            ) from error
        log_warn("Arroyo reconnection and identity verification succeeded.")
        return acquire_records(client, recovered_identity, snapshots)


def load_influxdb() -> tuple[Any, Any, str, str]:
    """Load the private InfluxDB settings and construct a synchronous writer."""

    with AUTH_PATH.open("rb") as auth_file:
        auth = tomllib.load(auth_file)
    influx_settings = dict(_mapping(auth.get("influxdb"), "auth.influxdb"))
    bucket = _nonempty_string(influx_settings.pop("bucket", None), "auth.influxdb.bucket")
    org = _nonempty_string(influx_settings.get("org"), "auth.influxdb.org")
    client = influxdb_client.InfluxDBClient(**influx_settings)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    return client, write_api, org, bucket


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Relay Arroyo Instruments snapshots to InfluxDB")
    parser.add_argument("--settings", type=Path, default=Path("settings.toml"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Run the foreground relay and return its process exit code."""

    args = build_parser().parse_args(argv)
    settings_path = args.settings.expanduser().resolve()
    source_client: ArroyoClient | None = None
    influx_client: Any = None
    write_api: Any = None
    exit_code = 0

    print()
    print("----- Arroyo Instruments controller -> InfluxDB uploader -----")
    print()

    try:
        settings = load_settings(settings_path)
        interval_s = settings["interval_s"]
        snapshots = settings["snapshots"]
        connection = settings["connection"]

        print(f"Polling interval = {interval_s:g} s, exception threshold = {EX_THRESHOLD}.")
        print(f"Configured snapshots = {len(snapshots)}.")
        print(f"Settings file = {settings_path}.")
        print(f"InfluxDB upload = {'disabled (dry-run)' if args.dry_run else 'enabled'}.")
        print()

        influx_org: str | None = None
        influx_bucket: str | None = None
        if not args.dry_run:
            influx_client, write_api, influx_org, influx_bucket = load_influxdb()
            print("InfluxDB client initialized.")
            print()

        source_client = create_source_client(connection)
        source_client.connect()
        identity = source_client.identify()
        print(
            "Arroyo controller identified: "
            f"manufacturer={identity.manufacturer!r}, model={identity.model!r}, "
            f"firmware={identity.firmware_version!r}, build={identity.build!r}."
        )
        print()

        stop_event = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signal_number, request_stop)

        lifetime_exception_count = 0
        iteration = 1
        next_poll = time.monotonic()
        print("Entering main polling loop...")
        print()

        while not stop_event.is_set():
            message_prefix = f"Iteration {iteration}: "
            try:
                records = acquire_with_recovery(source_client, identity, snapshots, message_prefix)
                if args.dry_run:
                    log(message_prefix + f"Dry-run records, not uploaded: {records!r}")
                else:
                    write_api.write(
                        bucket=influx_bucket,
                        org=influx_org,
                        record=records,
                    )
                    log(message_prefix + f"Uploaded {len(records)} complete snapshot record(s).")
            except Exception as error:
                if args.once:
                    raise
                lifetime_exception_count += 1
                log_error(message_prefix)
                log_error(
                    "Error during measurement/upload "
                    f"({lifetime_exception_count}/{EX_THRESHOLD} lifetime): "
                    f"{type(error).__name__}: {error}"
                )
                if lifetime_exception_count >= EX_THRESHOLD:
                    log_error("Exception threshold reached. Raising to Supervisor.")
                    raise

            if args.once:
                break

            iteration += 1
            next_poll += interval_s
            now = time.monotonic()
            if next_poll <= now:
                next_poll = now + interval_s
            stop_event.wait(next_poll - now)
    except KeyboardInterrupt:
        log_warn("KeyboardInterrupt received.")
        exit_code = 130
    except Exception as error:
        log_error(f"Fatal collector error: {type(error).__name__}: {error}")
        exit_code = 1
    finally:
        log("Shutting down gracefully...", end=" ")
        try:
            if source_client is not None:
                source_client.close()
        except Exception as error:
            log_warn(f"Arroyo cleanup failed: {type(error).__name__}: {error}")
        try:
            if write_api is not None:
                write_api.close()
        except Exception as error:
            log_warn(f"InfluxDB write API cleanup failed: {type(error).__name__}: {error}")
        try:
            if influx_client is not None:
                influx_client.close()
        except Exception as error:
            log_warn(f"InfluxDB client cleanup failed: {type(error).__name__}: {error}")
        print("Done")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())
