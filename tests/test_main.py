"""Test the direct relay script, schema, policy, and cleanup offline."""

from __future__ import annotations

import ast
import runpy
import signal
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import influxdb_client
import pytest

import pyarroyo
from pyarroyo import (
    ArroyoCommunicationError,
    InstrumentIdentity,
    LaserCondition,
    LaserSample,
    TECCondition,
    TECSample,
)

SCRIPT_PATH = Path(__file__).parents[1] / "main.py"
SETTINGS_TEMPLATE_PATH = Path(__file__).parents[1] / "settings.toml.template"

IDENTITY = InstrumentIdentity(
    manufacturer="Arroyo",
    model="MODEL",
    serial_number="SERIAL",
    firmware_version="1.2.3",
    build="BUILD",
    raw="Arroyo MODEL SERIAL 1.2.3 BUILD",
)
OBSERVED_AT = datetime(2026, 8, 29, 12, 34, 56, tzinfo=UTC)


def tec_sample(**changes: object) -> TECSample:
    """Build one deterministic TEC snapshot."""

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
    """Build one deterministic laser snapshot."""

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


def write_settings(
    tmp_path: Path,
    *,
    interval_s: float = 30,
    connection: str = "serial",
) -> Path:
    """Write synthetic settings for one direct-script execution."""

    if connection == "serial":
        connection_text = """
[connection.serial]
port = "COM_TEST"
baudrate = 38400
response_timeout_s = 1.5
"""
    else:
        connection_text = """
[connection.network]
host = "controller.local"
port = 10002
connect_timeout_s = 4.0
response_timeout_s = 2.0
"""
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
interval_s = {interval_s}
{connection_text}
[[tec]]
channel = 2
sensor_index = 1

[[laser]]
channel = 3
""".strip(),
        encoding="utf-8",
    )
    return settings_path


def write_auth(tmp_path: Path) -> Path:
    """Write synthetic, nonsecret InfluxDB destination values."""

    auth_path = tmp_path / "imaq-secret" / "auth.toml"
    auth_path.parent.mkdir()
    auth_path.write_text(
        """
[influxdb]
url = "http://influxdb.example:8086"
token = "<SYNTHETIC_TEST_TOKEN>"
org = "lab"
bucket = "devices"
verify_ssl = false
""".strip(),
        encoding="utf-8",
    )
    return auth_path


class FakeSource:
    """Provide ordered identity and snapshot outcomes to one script run."""

    def __init__(
        self,
        *,
        identities: list[InstrumentIdentity] | None = None,
        tec_outcomes: list[TECSample | Exception] | None = None,
        laser_outcomes: list[LaserSample | Exception] | None = None,
        on_laser_read: Callable[[], None] | None = None,
    ) -> None:
        """Store queued outcomes and initialize lifecycle observations."""

        self.identities = identities or [IDENTITY]
        self.tec_outcomes = tec_outcomes or [tec_sample()]
        self.laser_outcomes = laser_outcomes or [laser_sample()]
        self.on_laser_read = on_laser_read
        self.connect_count = 0
        self.reconnect_count = 0
        self.close_count = 0
        self.calls: list[tuple[object, ...]] = []

    def connect(self) -> None:
        """Record initial connection acquisition."""

        self.connect_count += 1

    def reconnect(self) -> None:
        """Record one connection replacement."""

        self.reconnect_count += 1

    def identify(self) -> InstrumentIdentity:
        """Return the next identity while retaining the last for later calls."""

        if len(self.identities) > 1:
            return self.identities.pop(0)
        return self.identities[0]

    def read_tec_sample(self, *, channel: int | None, sensor_index: int | None) -> TECSample:
        """Return or raise the next TEC outcome."""

        self.calls.append(("tec", channel, sensor_index))
        outcome = self.tec_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def read_laser_sample(self, *, channel: int | None) -> LaserSample:
        """Return or raise the next laser outcome."""

        self.calls.append(("laser", channel))
        if self.on_laser_read is not None:
            self.on_laser_read()
        outcome = self.laser_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        """Record source cleanup."""

        self.close_count += 1


class FakeWriteAPI:
    """Capture synchronous InfluxDB writes and cleanup."""

    def __init__(self, *, fail: bool = False) -> None:
        """Select write behavior and initialize observations."""

        self.fail = fail
        self.writes: list[tuple[str, str, list[dict[str, object]]]] = []
        self.close_count = 0

    def write(
        self,
        *,
        bucket: str,
        org: str,
        record: list[dict[str, object]],
    ) -> None:
        """Capture one complete batch or raise the selected failure."""

        if self.fail:
            raise RuntimeError("write failed")
        self.writes.append((bucket, org, record))

    def close(self) -> None:
        """Record writer cleanup."""

        self.close_count += 1


class FakeInfluxClient:
    """Return one fake writer and expose construction and cleanup state."""

    def __init__(self, write_api: FakeWriteAPI, options: dict[str, object]) -> None:
        """Store supplied options and initialize cleanup state."""

        self.api = write_api
        self.options = options
        self.close_count = 0

    def write_api(self, *, write_options: object) -> FakeWriteAPI:
        """Return the supplied synchronous writer."""

        assert write_options is not None
        return self.api

    def close(self) -> None:
        """Record client cleanup."""

        self.close_count += 1


class FakeStopEvent:
    """Stop continuous runs after deterministic waits."""

    def __init__(self, clock: list[float], *, stop_after_waits: int) -> None:
        """Share one monotonic clock and configure the wait limit."""

        self.clock = clock
        self.stop_after_waits = stop_after_waits
        self.waits: list[float] = []
        self.requested = False

    def set(self) -> None:
        """Record a signal-driven stop request."""

        self.requested = True

    def is_set(self) -> bool:
        """Report a signal request or exhausted wait allowance."""

        return self.requested or len(self.waits) >= self.stop_after_waits

    def wait(self, timeout: float | None = None) -> bool:
        """Record a wait and advance the shared clock."""

        assert timeout is not None
        self.waits.append(timeout)
        self.clock[0] += timeout
        return self.is_set()


def use_fake_source(
    monkeypatch: pytest.MonkeyPatch,
    source: FakeSource,
) -> tuple[list[tuple[tuple[object, ...], dict[str, object]]], list[tuple]]:
    """Replace both Arroyo factories and capture their arguments."""

    serial_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    network_calls: list[tuple] = []

    def for_serial(*args: object, **kwargs: object) -> FakeSource:
        serial_calls.append((args, kwargs))
        return source

    def for_network(*args: object, **kwargs: object) -> FakeSource:
        network_calls.append((args, kwargs))
        return source

    monkeypatch.setattr(
        pyarroyo,
        "ArroyoClient",
        SimpleNamespace(for_serial=for_serial, for_network=for_network),
    )
    return serial_calls, network_calls


def use_fake_influx(
    monkeypatch: pytest.MonkeyPatch,
    write_api: FakeWriteAPI,
) -> list[FakeInfluxClient]:
    """Replace InfluxDB construction and retain every fake client."""

    created: list[FakeInfluxClient] = []

    def influx_factory(**options: object) -> FakeInfluxClient:
        client = FakeInfluxClient(write_api, options)
        created.append(client)
        return client

    monkeypatch.setattr(influxdb_client, "InfluxDBClient", influx_factory)
    return created


def run_script(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> tuple[int, dict[str, object]]:
    """Execute the production file as a script and capture its exit result."""

    monkeypatch.syspath_prepend(str(SCRIPT_PATH.parent))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), *arguments])
    monkeypatch.setattr(signal, "signal", lambda _number, _handler: None)
    try:
        namespace: dict[str, object] = runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
    except SystemExit as error:
        assert isinstance(error.code, int)
        return error.code, {}
    return 0, namespace


def test_main_is_a_direct_sequential_script() -> None:
    """Keep production orchestration free of classes, functions, and a main wrapper."""

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )
    assert "if __name__" not in source
    assert "asyncio" not in source
    assert "await " not in source


def test_direct_script_help_preserves_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose the three documented command-line options."""

    exit_code, _namespace = run_script(monkeypatch, ["--help"])

    assert exit_code == 0
    help_text = capsys.readouterr().out
    assert "--settings" in help_text
    assert "--once" in help_text
    assert "--dry-run" in help_text


def test_settings_template_is_valid_toml() -> None:
    """Keep the distributed settings template directly parseable."""

    with SETTINGS_TEMPLATE_PATH.open("rb") as f:
        settings = tomllib.load(f)

    assert settings["interval_s"] == 30
    assert settings["connection"]["serial"]["port"] == "<PORT>"
    assert settings["tec"] == [{}]
    assert settings["laser"] == [{"channel": 1}]


@pytest.mark.parametrize("connection", ["serial", "network"])
def test_direct_script_constructs_selected_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    connection: str,
) -> None:
    """Read trusted settings directly into the selected Arroyo factory."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path, connection=connection)
    serial_calls, network_calls = use_fake_source(monkeypatch, FakeSource())

    exit_code, namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 0
    assert namespace["INTERVAL_s"] == 30
    if connection == "serial":
        assert serial_calls == [(("COM_TEST",), {"baudrate": 38400, "response_timeout_s": 1.5})]
        assert network_calls == []
    else:
        assert serial_calls == []
        assert network_calls == [
            (
                ("controller.local",),
                {"port": 10002, "connect_timeout_s": 4.0, "response_timeout_s": 2.0},
            )
        ]


def test_direct_script_rejects_duplicate_snapshot_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject one ambiguous selector directly during startup."""

    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        """
interval_s = 1
[connection.serial]
port = "COM_TEST"
[[tec]]
channel = 1
[[tec]]
channel = 1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate snapshot selector"):
        run_script(monkeypatch, ["--settings", str(settings_path), "--once", "--dry-run"])


def test_direct_script_maps_and_uploads_the_complete_documented_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map both subsystems, exact names, types, flags, tags, and timestamps."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    write_auth(tmp_path)
    source = FakeSource()
    use_fake_source(monkeypatch, source)
    write_api = FakeWriteAPI()
    created = use_fake_influx(monkeypatch, write_api)

    exit_code, _namespace = run_script(monkeypatch, ["--settings", str(settings_path), "--once"])

    assert exit_code == 0
    assert created[0].options == {
        "url": "http://influxdb.example:8086",
        "token": "<SYNTHETIC_TEST_TOKEN>",
        "org": "lab",
        "verify_ssl": False,
    }
    bucket, org, records = write_api.writes[0]
    assert (bucket, org) == ("devices", "lab")
    assert len(records) == 2
    assert records[0] == {
        "measurement": "arroyo",
        "tags": {
            "Manufacturer": "Arroyo",
            "Model": "MODEL",
            "Serial number": "SERIAL",
            "Subsystem": "TEC",
            "Channel": "2",
            "Sensor index": "1",
        },
        "fields": {
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
        },
        "time": OBSERVED_AT,
    }
    assert records[1] == {
        "measurement": "arroyo",
        "tags": {
            "Manufacturer": "Arroyo",
            "Model": "MODEL",
            "Serial number": "SERIAL",
            "Subsystem": "Laser",
            "Channel": "3",
        },
        "fields": {
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
        },
        "time": OBSERVED_AT + timedelta(seconds=1),
    }
    assert source.close_count == write_api.close_count == created[0].close_count == 1


def test_dry_run_skips_credentials_and_influxdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Read the source without opening auth.toml or constructing InfluxDB."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    source = FakeSource()
    use_fake_source(monkeypatch, source)
    monkeypatch.setattr(
        influxdb_client,
        "InfluxDBClient",
        lambda **_options: (_ for _ in ()).throw(AssertionError("InfluxDB constructed")),
    )

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 0
    assert "Dry-run records, not uploaded" in capsys.readouterr().out
    assert source.connect_count == source.close_count == 1


def test_source_failure_reconnects_reidentifies_and_retries_the_complete_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard an incomplete first attempt and retry all configured snapshots."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    source = FakeSource(
        tec_outcomes=[tec_sample(), tec_sample()],
        laser_outcomes=[ArroyoCommunicationError("first laser read"), laser_sample()],
    )
    use_fake_source(monkeypatch, source)

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 0
    assert source.calls == [("tec", 2, 1), ("laser", 3), ("tec", 2, 1), ("laser", 3)]
    assert source.reconnect_count == 1


def test_incomplete_retry_never_uploads_a_partial_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject both acquired TEC points when each laser attempt fails."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    write_auth(tmp_path)
    source = FakeSource(
        tec_outcomes=[tec_sample(), tec_sample()],
        laser_outcomes=[
            ArroyoCommunicationError("first laser read"),
            ArroyoCommunicationError("retry laser read"),
        ],
    )
    use_fake_source(monkeypatch, source)
    write_api = FakeWriteAPI()
    use_fake_influx(monkeypatch, write_api)

    exit_code, _namespace = run_script(monkeypatch, ["--settings", str(settings_path), "--once"])

    assert exit_code == 1
    assert source.reconnect_count == 1
    assert write_api.writes == []


def test_changed_identity_after_reconnect_rejects_the_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse to continue when the configured endpoint resolves to another controller."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    changed = InstrumentIdentity("Arroyo", "OTHER", "SERIAL", "1", "B", "raw")
    source = FakeSource(
        identities=[IDENTITY, changed],
        tec_outcomes=[ArroyoCommunicationError("disconnected")],
    )
    use_fake_source(monkeypatch, source)

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 1
    assert source.reconnect_count == 1
    assert source.calls == [("tec", 2, 1)]


def test_upload_failure_does_not_reconnect_the_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an InfluxDB failure outside the source recovery boundary."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    write_auth(tmp_path)
    source = FakeSource()
    use_fake_source(monkeypatch, source)
    write_api = FakeWriteAPI(fail=True)
    created = use_fake_influx(monkeypatch, write_api)

    exit_code, _namespace = run_script(monkeypatch, ["--settings", str(settings_path), "--once"])

    assert exit_code == 1
    assert source.reconnect_count == 0
    assert source.close_count == write_api.close_count == created[0].close_count == 1


def test_naive_timestamp_retries_then_fails_without_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject source samples without aware UTC timestamps."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    source = FakeSource(
        tec_outcomes=[
            tec_sample(observed_at=datetime(2026, 8, 29)),
            tec_sample(observed_at=datetime(2026, 8, 29)),
        ],
        laser_outcomes=[laser_sample(), laser_sample()],
    )
    use_fake_source(monkeypatch, source)

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 1
    assert source.reconnect_count == 1
    assert source.close_count == 1


def test_non_utc_timestamp_retries_then_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the pyarroyo timestamp contract to remain UTC."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path)
    observed_at = datetime(2026, 8, 29, tzinfo=timezone(timedelta(hours=1)))
    source = FakeSource(
        tec_outcomes=[tec_sample(observed_at=observed_at), tec_sample(observed_at=observed_at)],
    )
    use_fake_source(monkeypatch, source)

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--once", "--dry-run"],
    )

    assert exit_code == 1
    assert source.reconnect_count == 1


def test_lifetime_failure_count_does_not_reset_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reach the cumulative threshold despite one successful middle cycle."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path, interval_s=1)
    source = FakeSource(
        tec_outcomes=[
            ArroyoCommunicationError("cycle one"),
            ArroyoCommunicationError("cycle one retry"),
            tec_sample(),
            ArroyoCommunicationError("cycle three"),
            ArroyoCommunicationError("cycle three retry"),
            ArroyoCommunicationError("cycle four"),
            ArroyoCommunicationError("cycle four retry"),
        ],
        laser_outcomes=[laser_sample()],
    )
    stop_event = FakeStopEvent([0.0], stop_after_waits=99)
    use_fake_source(monkeypatch, source)
    monkeypatch.setattr(threading, "Event", lambda: stop_event)

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--dry-run"],
    )

    assert exit_code == 1
    assert "(3/3 lifetime)" in capsys.readouterr().err
    assert source.reconnect_count == 3
    assert source.close_count == 1


def test_scheduler_uses_cycle_start_deadlines_and_skips_catch_up_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subtract acquisition time and wait a full interval after an overrun."""

    monkeypatch.chdir(tmp_path)
    settings_path = write_settings(tmp_path, interval_s=10)
    clock = [0.0]
    durations = [3.0, 12.0]

    def advance_work() -> None:
        clock[0] += durations.pop(0)

    source = FakeSource(
        tec_outcomes=[tec_sample(), tec_sample()],
        laser_outcomes=[laser_sample(), laser_sample()],
        on_laser_read=advance_work,
    )
    stop_event = FakeStopEvent(clock, stop_after_waits=2)
    use_fake_source(monkeypatch, source)
    monkeypatch.setattr(threading, "Event", lambda: stop_event)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    exit_code, _namespace = run_script(
        monkeypatch,
        ["--settings", str(settings_path), "--dry-run"],
    )

    assert exit_code == 0
    assert stop_event.waits == [7.0, 10.0]
    assert source.close_count == 1


def test_startup_and_supervisor_files_preserve_service_contract() -> None:
    """Keep service restarts offline, explicit, and based on the prepared environment."""

    project_dir = SCRIPT_PATH.parent
    powershell = (project_dir / "Startup.ps1").read_text(encoding="utf-8")
    shell = (project_dir / "Startup.sh").read_text(encoding="utf-8")
    windows = (project_dir / "supervisor/arroyo-to-influxdb.windows.conf").read_text(
        encoding="utf-8"
    )
    linux = (project_dir / "supervisor/arroyo-to-influxdb.linux.conf").read_text(encoding="utf-8")

    assert '".\\main.py" --settings ".\\settings.toml"' in powershell
    assert 'exec "${venv_python}" ./main.py --settings ./settings.toml' in shell
    for config in (windows, linux):
        assert "autostart=false" in config
        assert "startsecs=5" in config
        assert "startretries=5" in config
        assert "autorestart=unexpected" in config
