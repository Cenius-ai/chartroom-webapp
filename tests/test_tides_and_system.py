"""Tide-log, output-contract and system tests for chartroom.py."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chartroom  # noqa: E402  (imported after the path insert on purpose)

ROOT = Path(__file__).resolve().parents[1]
CHARTROOM = ROOT / "chartroom.py"


def run(args, store=None, env=None, cwd=None):
    argv = [sys.executable, str(CHARTROOM)]
    if store is not None:
        argv += ["--store", str(store)]
    argv += list(args)
    environment = dict(os.environ)
    environment.pop("CHARTROOM_STORE", None)
    environment["NO_COLOR"] = "1"
    if env:
        environment.update(env)
    return subprocess.run(
        argv, capture_output=True, text=True, env=environment, cwd=str(cwd or ROOT)
    )


def error_lines(result):
    """Usage noise filtered out; only lines carrying an error remain."""
    return [
        line
        for line in result.stderr.splitlines()
        if line.startswith("error:") or ": error:" in line
    ]


@pytest.fixture()
def store(tmp_path):
    return tmp_path / "store.json"


def add_tide(store, station="Portsmouth", when="2024-06-01T09:00", height="2.4"):
    return run(
        ["tide", "add", "--station", station, "--time", when, "--height", str(height)],
        store=store,
    )


# --------------------------------------------------------------------------
# tide
# --------------------------------------------------------------------------
def test_tide_empty_state_invites_the_next_command(store):
    result = run(["tide", "list"], store=store)

    assert result.returncode == 0
    assert "no tide entries" in result.stdout
    assert "add one with: python chartroom.py tide add --station Portsmouth" in result.stdout


def test_tide_add_confirms_with_id_and_list_shows_the_same_row(store):
    added = add_tide(store, height="2.40")

    assert added.returncode == 0
    assert "added tide entry 1" in added.stdout
    assert "Portsmouth" in added.stdout
    assert "2024-06-01T09:00" in added.stdout
    assert "2.40 m" in added.stdout

    listed = run(["tide", "list"], store=store).stdout
    assert "1" in listed and "Portsmouth" in listed and "2024-06-01T09:00" in listed
    assert "2.40 m" in listed
    assert listed.rstrip().endswith("1 tide entry")


def test_tide_add_requires_station(store):
    result = run(["tide", "add", "--time", "2024-06-01T09:00", "--height", "2.4"], store=store)

    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert "--station" in error_lines(result)[0]
    assert not store.exists()


@pytest.mark.parametrize("station", ["???", "   ", "...", "---"])
def test_tide_add_rejects_station_without_letters_or_digits(store, station):
    # `--station=VALUE` so argparse never reads a dash-leading value as an option.
    result = run(
        [
            "tide", "add", "--station={}".format(station),
            "--time", "2024-06-01T09:00", "--height", "2.4",
        ],
        store=store,
    )

    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert "at least one letter or digit" in error_lines(result)[0]
    assert not store.exists()


@pytest.mark.parametrize(
    "when", ["2024-06-01", "", "01/06/2024 09:00", "2024-13-01T09:00", "2024-06-01 09:00", "2024-06-01T09"]
)
def test_tide_add_rejects_bad_timestamps(store, when):
    result = add_tide(store, when=when)

    assert result.returncode == 2
    assert error_lines(result) == [
        "error: the time must look like YYYY-MM-DDTHH:MM (got {!r})".format(when)
    ]
    assert not store.exists()


@pytest.mark.parametrize("height", ["two", "", "1,5", "nan", "inf"])
def test_tide_add_rejects_non_numeric_heights(store, height):
    result = add_tide(store, height=height)

    assert result.returncode == 2
    assert "the height must be a number" in error_lines(result)[0]
    assert not store.exists()


def test_tide_add_accepts_zero_and_negative_heights(store):
    result = run(
        ["tide", "add", "--station", "Portsmouth", "--time", "2024-06-01T03:00", "--height", "-0.4"],
        store=store,
    )
    assert result.returncode == 0
    assert "-0.40 m" in result.stdout

    zero = run(
        ["tide", "add", "--station", "Portsmouth", "--time", "2024-06-01T04:00", "--height", "0"],
        store=store,
    )
    assert zero.returncode == 0
    assert "0.00 m" in zero.stdout
    assert "-0.40 m" in run(["tide", "list"], store=store).stdout


def test_tide_list_is_chronological_and_counted(store):
    add_tide(store, station="Cowes", when="2024-06-01T15:00", height="3.1")
    add_tide(store, station="Portsmouth", when="2024-06-01T09:00", height="2.4")

    stdout = run(["tide", "list"], store=store).stdout
    rows = [line for line in stdout.splitlines() if "2024-06-01T" in line]

    assert len(rows) == 2
    assert "2024-06-01T09:00" in rows[0]
    assert "2024-06-01T15:00" in rows[1]
    assert stdout.index("2024-06-01T09:00") < stdout.index("2024-06-01T15:00")
    assert stdout.rstrip().endswith("2 tide entries")


def test_tide_ids_are_never_reused_and_missing_id_errors(store):
    add_tide(store, when="2024-06-01T09:00")
    add_tide(store, when="2024-06-01T10:00")

    removed = run(["tide", "remove", "--id", "1"], store=store)
    assert removed.returncode == 0
    assert "removed tide entry 1" in removed.stdout

    fresh = add_tide(store, when="2024-06-01T11:00")
    assert "added tide entry 3" in fresh.stdout

    missing = run(["tide", "remove", "--id", "99"], store=store)
    assert missing.returncode == 2
    assert error_lines(missing) == ["error: tide entry 99 not found"]

    listed = run(["tide", "list"], store=store).stdout
    assert "2024-06-01T09:00" not in listed
    assert "2024-06-01T11:00" in listed


def test_tide_remove_rejects_non_numeric_id(store):
    result = run(["tide", "remove", "--id", "abc"], store=store)

    assert result.returncode == 2
    assert error_lines(result) == ["error: --id must be a whole number (got 'abc')"]


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------
def test_results_on_stdout_errors_on_stderr_without_ansi(store):
    ok = run(["charts", "add", "--number", "1234", "--title", "Solent Approach"], store=store)
    assert ok.stderr == ""
    assert "\x1b[" not in ok.stdout

    bad = run(["charts", "add", "--number", "1234", "--title", "Again"], store=store)
    assert bad.stdout == ""
    assert bad.stderr.count("error:") == 1


def test_json_mode_prints_machine_readable_output(store):
    run(["charts", "add", "--number", "1234", "--title", "Solent Approach", "--scale", "25000"], store=store)
    run(
        ["course", "plot", "--name", "Solent", "--waypoint", "A:50.70,-1.20", "--waypoint", "B:50.80,-1.60"],
        store=store,
    )
    add_tide(store)

    charts = json.loads(run(["charts", "list", "--json"], store=store).stdout)
    assert charts["count"] == 1
    assert charts["charts"][0]["number"] == "1234"

    course = json.loads(run(["course", "show", "--name", "Solent", "--json"], store=store).stdout)
    assert course["total_distance_nm"] > 0
    assert course["legs"][0]["bearing_deg"] >= 0

    tides = json.loads(run(["tide", "list", "--json"], store=store).stdout)
    assert tides["count"] == 1


def test_store_is_written_atomically(tmp_path):
    store = tmp_path / "store.json"
    assert run(["charts", "add", "--number", "1", "--title", "One"], store=store).returncode == 0

    leftovers = [path.name for path in tmp_path.iterdir() if path.name != "store.json"]
    assert leftovers == []
    assert json.loads(store.read_text(encoding="utf-8"))["charts"][0]["title"] == "One"


def test_large_store_still_answers_in_well_under_a_second(tmp_path):
    store = tmp_path / "big.json"
    store.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "charts": [
                    {"number": str(1000 + index), "title": "Chart %d" % index, "region": "Solent", "scale": 25000}
                    for index in range(1000)
                ],
                "courses": [
                    {
                        "name": "Course %d" % index,
                        "chart_number": None,
                        "created_at": "2024-06-01T00:00:00Z",
                        "updated_at": "2024-06-01T00:00:00Z",
                        "waypoints": [
                            {"seq": 1, "name": "A", "latitude": 50.0, "longitude": -1.0},
                            {"seq": 2, "name": "B", "latitude": 50.5, "longitude": -1.5},
                        ],
                    }
                    for index in range(200)
                ],
                "tides": [
                    {"id": index + 1, "station": "Portsmouth", "time": "2024-06-01T%02d:%02d" % (index % 24, index % 60), "height_m": 1.5}
                    for index in range(5000)
                ],
                "next_tide_id": 5001,
            }
        ),
        encoding="utf-8",
    )

    started = time.perf_counter()
    result = run(["charts", "list"], store=store)
    elapsed = time.perf_counter() - started

    assert result.returncode == 0
    assert "1000 charts" in result.stdout
    assert elapsed < 2.0


# --------------------------------------------------------------------------
# docs, help text and the offline demo
# --------------------------------------------------------------------------
def test_help_lists_every_command_group_with_a_description():
    result = run(["--help"])

    assert result.returncode == 0
    for group, description in (
        ("charts", "chart inventory"),
        ("course", "course plotting"),
        ("tide", "tide table log"),
    ):
        assert group in result.stdout
        assert description in result.stdout
    assert run(["--version"]).stdout.strip().startswith("chartroom ")


def test_group_without_action_prints_its_own_help(store):
    result = run(["charts"], store=store)

    assert result.returncode == 0
    assert "add" in result.stdout and "list" in result.stdout and "remove" in result.stdout


def test_readme_quickstart_commands_are_the_empty_state_invitations(tmp_path):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    store = tmp_path / "quickstart.json"

    invitations = [
        chartroom.EMPTY_CHARTS_HINT.split("with: ", 1)[1],
        chartroom.EMPTY_COURSES_HINT.split("with: ", 1)[1],
        chartroom.EMPTY_TIDES_HINT.split("with: ", 1)[1],
    ]

    for command in invitations:
        assert command in readme

    # Pasteability: run each invitation verbatim, with the interpreter swapped in.
    for command in invitations:
        arguments = shlex.split(command.replace("python chartroom.py", "", 1))
        argv = [sys.executable, str(CHARTROOM), "--store", str(store)] + arguments
        result = subprocess.run(
            argv, capture_output=True, text=True, env=dict(os.environ, NO_COLOR="1")
        )
        assert result.returncode == 0, (command, result.stderr)


def test_demo_script_prints_the_offline_walkthrough(tmp_path):
    transcript = tmp_path / "demo_output.txt"
    store = tmp_path / "demo-store.json"

    result = subprocess.run(
        ["bash", "demo.sh"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=dict(
            os.environ,
            NO_COLOR="1",
            CHARTROOM_STORE=str(store),
            CHARTROOM_DEMO_OUT=str(transcript),
        ),
    )

    assert result.returncode == 0, result.stderr
    text = transcript.read_text(encoding="utf-8")
    assert text == result.stdout

    order = ["charts add", "charts list", "course plot", "course list", "course show", "tide add", "tide list"]
    positions = [text.index("chartroom.py " + step) for step in order]
    assert positions == sorted(positions)

    assert text.count("[exit 2]") >= 3
    assert store.exists()
    assert "no charts yet" not in text


def test_geometry_helpers_are_reused_by_the_cli():
    assert chartroom.haversine_nm(0.0, 0.0, 0.0, 1.0) == pytest.approx(60.04, abs=0.05)
    assert chartroom.initial_bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.1)
    assert chartroom.initial_bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.1)

    course = {
        "waypoints": [
            {"seq": 1, "name": "A", "latitude": 50.70, "longitude": -1.20},
            {"seq": 2, "name": "B", "latitude": 50.80, "longitude": -1.60},
        ]
    }
    legs = chartroom.course_legs(course)
    assert len(legs) == 1
    assert legs[0]["distance_nm"] == pytest.approx(16.34, abs=0.05)
    assert chartroom.total_distance_nm(legs) == pytest.approx(16.34, abs=0.05)
