from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from pyarroyo import (
    ArroyoCommunicationError,
    ArroyoError,
    InstrumentIdentity,
    LaserCondition,
    LaserSample,
    TECCondition,
    TECSample,
)

IDENTITY = InstrumentIdentity(
    manufacturer="Arroyo",
    model="MODEL",
    serial_number="SERIAL",
    firmware_version="1.2.3",
    build="BUILD",
    raw="Arroyo MODEL SERIAL 1.2.3 BUILD",
)
OBSERVED_AT = datetime(2026, 8, 29, 12, 34, 56, tzinfo=UTC)


def write_settings(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def serial_settings(tmp_path: Path, *, interval_s: str = "30") -> Path:
    return write_settings(
        tmp_path,
        f"""
interval_s = {interval_s}

[connection.serial]
port = "COM_TEST"
baudrate = 38400
response_timeout_s = 1.5

[[tec]]
channel = 2
sensor_index = 1

[[laser]]
channel = 3
""",
    )


def tec_sample(**changes: object) -> TECSample:
    values = {
        "observed_at": OBSERVED_AT,
        "channel": 2,
        "sensor_index": 1,
        "mode": "T",
        "output_enabled": True,
        "temperature_C": 24.5,
        "temperature_setpoint_C": 25.0,
        "current_A": 0.125,
        "voltage_V": 1.75,
        "condition": (
            TECCondition.CURRENT_LIMIT | TECCondition.OUTPUT_ON | TECCondition.THERMAL_RUNAWAY
        ),
    }
    values.update(changes)
    return TECSample(**values)


def laser_sample(**changes: object) -> LaserSample:
    values = {
        "observed_at": OBSERVED_AT + timedelta(seconds=1),
        "channel": 3,
        "mode": "I",
        "output_enabled": False,
        "current_A": 0.012,
        "current_setpoint_A": 0.013,
        "voltage_V": 2.5,
        "voltage_setpoint_V": 2.6,
        "condition": (
            LaserCondition.VOLTAGE_LIMIT
            | LaserCondition.INTERLOCK_DISABLED
            | LaserCondition.RESISTANCE_LIMIT
        ),
    }
    values.update(changes)
    return LaserSample(**values)


class FakeSource:
    def __init__(
        self,
        *,
        identity: InstrumentIdentity = IDENTITY,
        tec: TECSample | Exception | None = None,
        laser: LaserSample | Exception | None = None,
    ) -> None:
        self.identity = identity
        self.tec = tec if tec is not None else tec_sample()
        self.laser = laser if laser is not None else laser_sample()
        self.connect_count = 0
        self.reconnect_count = 0
        self.close_count = 0
        self.calls: list[tuple[object, ...]] = []

    def connect(self) -> None:
        self.connect_count += 1

    def reconnect(self) -> None:
        self.reconnect_count += 1

    def identify(self) -> InstrumentIdentity:
        return self.identity

    def read_tec_sample(self, *, channel: int | None, sensor_index: int | None) -> TECSample:
        self.calls.append(("tec", channel, sensor_index))
        if isinstance(self.tec, Exception):
            raise self.tec
        return self.tec

    def read_laser_sample(self, *, channel: int | None) -> LaserSample:
        self.calls.append(("laser", channel))
        if isinstance(self.laser, Exception):
            raise self.laser
        return self.laser

    def close(self) -> None:
        self.close_count += 1


class FakeWriteAPI:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.close_count = 0

    def write(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.close_count += 1


class FakeInfluxClient:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def silence_signal_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.signal, "signal", lambda *_args: None)


def test_load_settings_normalizes_connection_and_snapshot_order(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        """
interval_s = 12.5

[connection.network]
host = "controller.local"

[[laser]]
channel = 4

[[tec]]

[[tec]]
channel = 2
sensor_index = 3
""",
    )

    settings = main.load_settings(path)

    assert settings["interval_s"] == 12.5
    assert settings["connection"] == {
        "kind": "network",
        "host": "controller.local",
        "port": 10001,
        "connect_timeout_s": 3.0,
        "response_timeout_s": 1.0,
    }
    assert settings["snapshots"] == [
        {"subsystem": "tec", "channel": None, "sensor_index": None},
        {"subsystem": "tec", "channel": 2, "sensor_index": 3},
        {"subsystem": "laser", "channel": 4, "sensor_index": None},
    ]


@pytest.mark.parametrize(
    "body, match",
    [
        ("interval_s = 0\n[connection.serial]\nport = 'COM1'\n[[tec]]\n", "interval_s"),
        (
            "interval_s = 1\n[connection.serial]\nport = 'COM1'\n"
            "[connection.network]\nhost = 'host'\n[[tec]]\n",
            "exactly one",
        ),
        ("interval_s = 1\n[connection.serial]\nport = 'COM1'\n", "at least one"),
        (
            "interval_s = 1\n[connection.serial]\nport = 'COM1'\n"
            "[[laser]]\nchannel = 1\nsensor_index = 1\n",
            "not supported",
        ),
        (
            "interval_s = 1\n[connection.serial]\nport = 'COM1'\n"
            "[[tec]]\nchannel = 1\n[[tec]]\nchannel = 1\n",
            "duplicate",
        ),
    ],
)
def test_load_settings_rejects_invalid_contract(tmp_path: Path, body: str, match: str) -> None:
    with pytest.raises(main.SettingsError, match=match):
        main.load_settings(write_settings(tmp_path, body))


def test_create_source_client_passes_exact_serial_and_network_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serial_factory = SimpleNamespace(calls=[])
    network_factory = SimpleNamespace(calls=[])
    monkeypatch.setattr(
        main.ArroyoClient,
        "for_serial",
        lambda *args, **kwargs: serial_factory.calls.append((args, kwargs)) or "serial-client",
    )
    monkeypatch.setattr(
        main.ArroyoClient,
        "for_network",
        lambda *args, **kwargs: network_factory.calls.append((args, kwargs)) or "network-client",
    )

    assert (
        main.create_source_client(
            {
                "kind": "serial",
                "port": "COM7",
                "baudrate": 9600,
                "response_timeout_s": 2.0,
            }
        )
        == "serial-client"
    )
    assert (
        main.create_source_client(
            {
                "kind": "network",
                "host": "controller",
                "port": 10002,
                "connect_timeout_s": 4.0,
                "response_timeout_s": 2.0,
            }
        )
        == "network-client"
    )
    assert serial_factory.calls == [(("COM7",), {"baudrate": 9600, "response_timeout_s": 2.0})]
    assert network_factory.calls == [
        (
            ("controller",),
            {"port": 10002, "connect_timeout_s": 4.0, "response_timeout_s": 2.0},
        )
    ]


def test_tec_record_matches_documented_schema() -> None:
    record = main.tec_record(
        IDENTITY,
        tec_sample(),
        {"subsystem": "tec", "channel": 2, "sensor_index": 1},
    )

    assert record["measurement"] == "arroyo"
    assert record["tags"] == {
        "Manufacturer": "Arroyo",
        "Model": "MODEL",
        "Serial number": "SERIAL",
        "Subsystem": "TEC",
        "Channel": "2",
        "Sensor index": "1",
    }
    assert record["time"] is OBSERVED_AT
    assert record["fields"] == {
        "FirmwareVersion": "1.2.3",
        "Build": "BUILD",
        "Mode": "T",
        "OutputEnabled": True,
        "Condition": 5121,
        "OutputOnCondition": True,
        "Current[A]": 0.125,
        "Voltage[V]": 1.75,
        "Temperature[degC]": 24.5,
        "TemperatureSetpoint[degC]": 25.0,
        "CurrentLimit": True,
        "VoltageLimit": False,
        "SensorLimit": False,
        "TemperatureHighLimit": False,
        "TemperatureLowLimit": False,
        "SensorShorted": False,
        "SensorOpen": False,
        "TECOpenCircuit": False,
        "OutOfTolerance": False,
        "ThermalRunaway": True,
    }


def test_laser_record_matches_documented_schema() -> None:
    record = main.laser_record(
        IDENTITY,
        laser_sample(),
        {"subsystem": "laser", "channel": 3, "sensor_index": None},
    )

    assert record["tags"]["Subsystem"] == "Laser"
    assert record["tags"]["Channel"] == "3"
    assert "Sensor index" not in record["tags"]
    assert record["time"] == OBSERVED_AT + timedelta(seconds=1)
    assert record["fields"] == {
        "FirmwareVersion": "1.2.3",
        "Build": "BUILD",
        "Mode": "I",
        "OutputEnabled": False,
        "Condition": 8210,
        "OutputOnCondition": False,
        "Current[A]": 0.012,
        "Voltage[V]": 2.5,
        "CurrentSetpoint[A]": 0.013,
        "VoltageSetpoint[V]": 2.6,
        "CurrentLimit": False,
        "VoltageLimit": True,
        "PhotodiodeCurrentLimit": False,
        "PhotodiodePowerLimit": False,
        "InterlockDisabled": True,
        "OpenCircuit": False,
        "ShortCircuit": False,
        "OutOfTolerance": False,
        "ResistanceLimit": True,
        "TemperatureLimit": False,
    }


@pytest.mark.parametrize(
    "sample, selector, match",
    [
        (
            tec_sample(observed_at=datetime(2026, 8, 29)),
            {"subsystem": "tec", "channel": 2, "sensor_index": 1},
            "aware",
        ),
        (
            tec_sample(observed_at=datetime(2026, 8, 29, tzinfo=timezone(timedelta(hours=1)))),
            {"subsystem": "tec", "channel": 2, "sensor_index": 1},
            "UTC",
        ),
        (
            tec_sample(current_A=float("nan")),
            {"subsystem": "tec", "channel": 2, "sensor_index": 1},
            "finite",
        ),
        (
            tec_sample(channel=1),
            {"subsystem": "tec", "channel": 2, "sensor_index": 1},
            "selector",
        ),
    ],
)
def test_tec_record_rejects_invalid_or_mismatched_samples(
    sample: TECSample, selector: dict[str, object], match: str
) -> None:
    with pytest.raises(main.SampleValidationError, match=match):
        main.tec_record(IDENTITY, sample, selector)


def test_acquire_records_is_sequential_and_returns_only_a_complete_batch() -> None:
    source = FakeSource()
    snapshots = [
        {"subsystem": "tec", "channel": 2, "sensor_index": 1},
        {"subsystem": "laser", "channel": 3, "sensor_index": None},
    ]

    records = main.acquire_records(source, IDENTITY, snapshots)

    assert source.calls == [("tec", 2, 1), ("laser", 3)]
    assert [record["tags"]["Subsystem"] for record in records] == ["TEC", "Laser"]

    source = FakeSource(laser=ArroyoCommunicationError("incomplete"))
    with pytest.raises(ArroyoCommunicationError, match="incomplete"):
        main.acquire_records(source, IDENTITY, snapshots)


def test_source_failure_reconnects_reidentifies_and_retries_complete_batch_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource()
    calls = []

    def acquire(*_args: object) -> list[dict[str, object]]:
        calls.append("acquire")
        if len(calls) == 1:
            raise ArroyoCommunicationError("first read failed")
        return [{"complete": True}]

    monkeypatch.setattr(main, "acquire_records", acquire)

    assert main.acquire_with_recovery(source, IDENTITY, [], "Iteration 1: ") == [{"complete": True}]
    assert calls == ["acquire", "acquire"]
    assert source.reconnect_count == 1


def test_identity_change_after_reconnect_rejects_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = InstrumentIdentity("Arroyo", "OTHER", "SERIAL", "1", "B", "raw")
    source = FakeSource(identity=changed)
    monkeypatch.setattr(
        main,
        "acquire_records",
        lambda *_args: (_ for _ in ()).throw(ArroyoCommunicationError("failed")),
    )

    with pytest.raises(ArroyoError, match="identity changed"):
        main.acquire_with_recovery(source, IDENTITY, [], "Iteration 1: ")
    assert source.reconnect_count == 1


def test_load_influxdb_removes_bucket_before_constructing_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.toml"
    auth_path.write_text(
        "[influxdb]\nurl = 'https://influx.invalid'\ntoken = 'TOKEN'\norg = 'ORG'\nbucket = 'BUCKET'\n",
        encoding="utf-8",
    )
    created = []
    write_api = object()

    class Client:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        def write_api(self, *, write_options: object) -> object:
            assert write_options is main.SYNCHRONOUS
            return write_api

    monkeypatch.setattr(main, "AUTH_PATH", auth_path)
    monkeypatch.setattr(main.influxdb_client, "InfluxDBClient", Client)

    client, actual_write_api, org, bucket = main.load_influxdb()

    assert isinstance(client, Client)
    assert actual_write_api is write_api
    assert org == "ORG"
    assert bucket == "BUCKET"
    assert created == [{"url": "https://influx.invalid", "token": "TOKEN", "org": "ORG"}]


def test_once_dry_run_never_loads_credentials_or_influxdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = FakeSource()
    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(
        main,
        "load_influxdb",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must remain unopened")),
    )
    silence_signal_registration(monkeypatch)

    result = main.run(["--settings", str(serial_settings(tmp_path)), "--once", "--dry-run"])

    assert result == 0
    assert source.connect_count == 1
    assert source.close_count == 1
    assert "Dry-run records, not uploaded" in capsys.readouterr().out


def test_once_upload_writes_one_complete_batch_and_closes_every_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    influx = FakeInfluxClient()
    write_api = FakeWriteAPI()
    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(main, "load_influxdb", lambda: (influx, write_api, "ORG", "BUCKET"))
    silence_signal_registration(monkeypatch)

    result = main.run(["--settings", str(serial_settings(tmp_path)), "--once"])

    assert result == 0
    assert len(write_api.calls) == 1
    call = write_api.calls[0]
    assert call["org"] == "ORG"
    assert call["bucket"] == "BUCKET"
    assert len(call["record"]) == 2
    assert source.close_count == write_api.close_count == influx.close_count == 1


def test_upload_failure_does_not_reconnect_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    influx = FakeInfluxClient()
    write_api = FakeWriteAPI(RuntimeError("upload failed"))
    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(main, "load_influxdb", lambda: (influx, write_api, "ORG", "BUCKET"))
    silence_signal_registration(monkeypatch)

    result = main.run(["--settings", str(serial_settings(tmp_path)), "--once"])

    assert result == 1
    assert source.reconnect_count == 0
    assert source.close_count == write_api.close_count == influx.close_count == 1


def test_incomplete_source_batch_retries_once_but_never_uploads_partial_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource(laser=ArroyoCommunicationError("laser read failed"))
    influx = FakeInfluxClient()
    write_api = FakeWriteAPI()
    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(main, "load_influxdb", lambda: (influx, write_api, "ORG", "BUCKET"))
    silence_signal_registration(monkeypatch)

    result = main.run(["--settings", str(serial_settings(tmp_path)), "--once"])

    assert result == 1
    assert source.calls == [("tec", 2, 1), ("laser", 3), ("tec", 2, 1), ("laser", 3)]
    assert source.reconnect_count == 1
    assert write_api.calls == []


def test_lifetime_failures_are_cumulative_across_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    outcomes: list[object] = [
        RuntimeError("failure one"),
        [{"success": True}],
        RuntimeError("failure two"),
        RuntimeError("failure three"),
    ]

    def acquire(*_args: object) -> list[dict[str, object]]:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(main, "acquire_with_recovery", acquire)
    silence_signal_registration(monkeypatch)

    result = main.run(
        ["--settings", str(serial_settings(tmp_path, interval_s="0.001")), "--dry-run"]
    )

    assert result == 1
    assert outcomes == []
    assert source.close_count == 1


def test_polling_uses_cycle_start_deadlines_and_skips_catch_up_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource()
    waits: list[float] = []

    class ControlledEvent:
        def __init__(self) -> None:
            self.stopped = False

        def set(self) -> None:
            self.stopped = True

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, timeout: float) -> bool:
            waits.append(timeout)
            if len(waits) == 2:
                self.stopped = True
            return self.stopped

    monotonic_values = iter([100.0, 103.0, 125.0])
    monkeypatch.setattr(main, "create_source_client", lambda _settings: source)
    monkeypatch.setattr(main.threading, "Event", ControlledEvent)
    monkeypatch.setattr(main.time, "monotonic", lambda: next(monotonic_values))
    silence_signal_registration(monkeypatch)

    result = main.run(["--settings", str(serial_settings(tmp_path, interval_s="10")), "--dry-run"])

    assert result == 0
    assert waits == [7.0, 10.0]
    assert source.calls == [
        ("tec", 2, 1),
        ("laser", 3),
        ("tec", 2, 1),
        ("laser", 3),
    ]


def test_startup_and_supervisor_files_preserve_service_contract() -> None:
    powershell = (main.PROJECT_DIR / "Startup.ps1").read_text(encoding="utf-8")
    shell = (main.PROJECT_DIR / "Startup.sh").read_text(encoding="utf-8")
    windows = (main.PROJECT_DIR / "supervisor/arroyo-to-influxdb.windows.conf").read_text(
        encoding="utf-8"
    )
    linux = (main.PROJECT_DIR / "supervisor/arroyo-to-influxdb.linux.conf").read_text(
        encoding="utf-8"
    )

    assert "uv sync" not in powershell.split("Run uv sync first.")[-1]
    assert '".\\main.py" --settings ".\\settings.toml"' in powershell
    assert 'exec "${venv_python}" ./main.py --settings ./settings.toml' in shell
    for config in (windows, linux):
        assert "autostart=false" in config
        assert "startsecs=5" in config
        assert "startretries=5" in config
        assert "autorestart=unexpected" in config
