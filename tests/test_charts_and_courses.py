"""End-to-end tests for the charts and course screens of chartroom.py.

Every test drives the real program in a subprocess against a throwaway JSON
store, so what is asserted is exactly what a user sees in the terminal.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHARTROOM = ROOT / "chartroom.py"

LEG_RE = re.compile(r"leg (\d+)\s+(.+?)\s{2,}([\d.]+) nm\s+bearing (\d{3}\.\d)\u00b0 true")
TOTAL_RE = re.compile(r"total distance ([\d.]+) nm")


def run(args, store=None, env=None, cwd=None):
    """Run chartroom with an isolated environment and no colour codes."""
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
    """The error contract: usage noise filtered out, the message lines remain.

    argparse prints a (wrapped) usage block before its own message, so only lines
    carrying an error - `error: ...` for this program, `... : error: ...` for
    argparse - count towards the one-line promise.
    """
    return [
        line
        for line in result.stderr.splitlines()
        if line.startswith("error:") or ": error:" in line
    ]


def parse_legs(stdout):
    return [
        {
            "seq": int(match.group(1)),
            "label": match.group(2).strip(),
            "distance_nm": float(match.group(3)),
            "bearing_deg": float(match.group(4)),
        }
        for match in LEG_RE.finditer(stdout)
    ]


@pytest.fixture()
def store(tmp_path):
    return tmp_path / "store.json"


def add_chart(store, number="1234", title="Solent Approach", region="Solent", scale="25000"):
    args = ["charts", "add", "--number", str(number), "--title", title]
    if region is not None:
        args += ["--region", region]
    if scale is not None:
        args += ["--scale", str(scale)]
    return run(args, store=store)


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------
def test_charts_empty_state_invites_the_next_command(store):
    result = run(["charts", "list"], store=store)

    assert result.returncode == 0
    assert "no charts yet" in result.stdout
    assert "add one with: python chartroom.py charts add --number 1234" in result.stdout
    assert result.stderr == ""
    assert not store.exists()


def test_charts_add_confirms_and_list_renders_the_row(store):
    added = add_chart(store)

    assert added.returncode == 0
    assert "added chart 1234" in added.stdout
    assert "Solent Approach" in added.stdout
    assert added.stdout.count("\n") == 1
    assert added.stderr == ""

    listed = run(["charts", "list"], store=store)
    assert listed.returncode == 0
    for expected in ("1234", "Solent Approach", "Solent", "25000", "1 chart"):
        assert expected in listed.stdout

    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["charts"][0]["number"] == "1234"
    assert saved["charts"][0]["scale"] == 25000


def test_charts_add_rejects_duplicate_number_and_writes_nothing(store):
    add_chart(store)
    before = store.read_bytes()

    result = run(["charts", "add", "--number", "1234", "--title", "Other"], store=store)

    assert result.returncode == 2
    assert error_lines(result) == ["error: chart 1234 already exists"]
    assert store.read_bytes() == before
    assert "1 chart" in run(["charts", "list"], store=store).stdout


def test_charts_add_requires_number_and_leaves_the_store_empty(store):
    result = run(
        ["charts", "add", "--title", "Approach", "--region", "Solent", "--scale", "25000"],
        store=store,
    )

    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert "--number" in error_lines(result)[0]
    assert not store.exists()
    assert "no charts yet" in run(["charts", "list"], store=store).stdout


@pytest.mark.parametrize("title", ["", "   "])
def test_charts_add_rejects_blank_title(store, title):
    result = run(["charts", "add", "--number", "9999", "--title", title], store=store)

    assert result.returncode == 2
    assert error_lines(result) == ["error: --title must not be blank"]
    assert not store.exists()


@pytest.mark.parametrize("scale", ["abc", "25.5", "", "0", "-100"])
def test_charts_add_rejects_bad_scale(store, scale):
    result = run(
        ["charts", "add", "--number", "9999", "--title", "Approach", "--scale", scale],
        store=store,
    )

    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert error_lines(result)[0].startswith("error: ")
    assert "--scale" in error_lines(result)[0]
    assert not store.exists()


def test_charts_remove_then_missing_chart_is_an_error(store):
    add_chart(store)

    removed = run(["charts", "remove", "--number", "1234"], store=store)
    assert removed.returncode == 0
    assert "removed chart 1234" in removed.stdout
    assert "no charts yet" in run(["charts", "list"], store=store).stdout

    again = run(["charts", "remove", "--number", "1234"], store=store)
    assert again.returncode == 2
    assert error_lines(again) == ["error: chart 1234 not found"]


def test_charts_list_sorts_numbers_naturally_with_a_total(store):
    for number in ("BA99", "12", "7"):
        assert add_chart(store, number=number, title="Chart " + number, region=None, scale=None).returncode == 0

    stdout = run(["charts", "list"], store=store).stdout
    order = [line.split()[0] for line in stdout.splitlines() if line.strip() and line.split()[0] in {"7", "12", "BA99"}]

    assert order == ["7", "12", "BA99"]
    assert stdout.rstrip().endswith("3 charts")


def test_unknown_command_prints_usage_and_exits_two(store):
    result = run(["bogus"], store=store)

    assert result.returncode == 2
    assert "usage:" in result.stderr
    for group in ("charts", "course", "tide"):
        assert group in result.stderr


def test_corrupt_store_reports_once_and_exits_one(tmp_path):
    broken = tmp_path / "cr-broken.json"
    broken.write_text("{not json", encoding="utf-8")

    result = run(["charts", "list"], store=broken)

    assert result.returncode == 1
    assert len(error_lines(result)) == 1
    assert "store is unreadable" in error_lines(result)[0]
    assert broken.read_text(encoding="utf-8") == "{not json"


def test_store_with_wrong_shape_is_treated_as_unreadable(tmp_path):
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"schema_version": 1, "charts": "nope"}), encoding="utf-8")

    result = run(["charts", "list"], store=odd)

    assert result.returncode == 1
    assert "store is unreadable" in error_lines(result)[0]


def test_store_flag_keeps_the_default_store_untouched(tmp_path):
    home = tmp_path / "home"
    default_store = home / ".chartroom" / "store.json"
    default_store.parent.mkdir(parents=True)
    default_store.write_text(json.dumps({"schema_version": 1, "charts": [], "courses": [], "tides": [], "next_tide_id": 1}), encoding="utf-8")
    before = default_store.read_bytes()

    other = tmp_path / "cr-check.json"
    result = run(
        ["charts", "add", "--store", str(other), "--number", "7", "--title", "Check"],
        env={"HOME": str(home)},
    )

    assert result.returncode == 0
    assert default_store.read_bytes() == before
    assert json.loads(other.read_text(encoding="utf-8"))["charts"][0]["number"] == "7"


def test_store_env_var_is_honoured(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env_store = tmp_path / "env-store.json"

    result = run(
        ["charts", "add", "--number", "8", "--title", "Env Chart"],
        env={"HOME": str(home), "CHARTROOM_STORE": str(env_store)},
    )

    assert result.returncode == 0
    assert env_store.exists()
    assert not (home / ".chartroom").exists()


# --------------------------------------------------------------------------
# course
# --------------------------------------------------------------------------
def test_course_empty_state_invites_the_next_command(store):
    result = run(["course", "list"], store=store)

    assert result.returncode == 0
    assert "no courses yet" in result.stdout
    assert "plot one with: python chartroom.py course plot --name Solent" in result.stdout


def test_course_plot_prints_legs_and_total(store):
    result = run(
        [
            "course", "plot", "--name", "Solent",
            "--waypoint", "Start:50.70,-1.20",
            "--waypoint", "Mid:50.75,-1.40",
            "--waypoint", "End:50.80,-1.60",
        ],
        store=store,
    )

    assert result.returncode == 0
    assert "plotted Solent with 3 waypoints" in result.stdout
    legs = parse_legs(result.stdout)
    assert len(legs) == 2
    assert [leg["seq"] for leg in legs] == [1, 2]
    assert all(leg["distance_nm"] > 0 for leg in legs)
    total = float(TOTAL_RE.search(result.stdout).group(1))
    assert total == pytest.approx(sum(leg["distance_nm"] for leg in legs), abs=0.02)


def test_course_list_shows_waypoint_count_and_chart(store):
    add_chart(store)
    run(
        [
            "course", "plot", "--name", "Solent", "--chart", "1234",
            "--waypoint", "Start:50.70,-1.20",
            "--waypoint", "Mid:50.75,-1.40",
            "--waypoint", "End:50.80,-1.60",
        ],
        store=store,
    )

    stdout = run(["course", "list"], store=store).stdout

    assert "Solent" in stdout
    assert "3 waypoints" in stdout
    assert "1234" in stdout
    assert stdout.rstrip().endswith("1 course")


@pytest.mark.parametrize(
    "start,end,distance,bearing",
    [
        ("0.0000,0.0000", "0.0000,1.0000", 60.04, 90.0),
        ("0.0000,0.0000", "1.0000,0.0000", 60.04, 0.0),
        ("60.0000,0.0000", "60.0000,1.0000", 30.02, 89.6),
    ],
)
def test_course_leg_geometry_matches_the_great_circle(store, start, end, distance, bearing):
    result = run(
        ["course", "plot", "--name", "Leg", "--waypoint", "A:" + start, "--waypoint", "B:" + end],
        store=store,
    )

    assert result.returncode == 0
    leg = parse_legs(result.stdout)[0]
    assert leg["distance_nm"] == pytest.approx(distance, abs=0.05)
    assert leg["bearing_deg"] == pytest.approx(bearing, abs=0.5)


@pytest.mark.parametrize(
    "waypoints,expected",
    [
        (["Start:95.0,-1.0", "End:50.8,-1.6"], "latitude must be between -90 and 90"),
        (["Start:50.7,200.0", "End:50.8,-1.6"], "longitude must be between -180 and 180"),
        (["Start:abc", "End:50.8,-1.6"], "a waypoint must look like Name:lat,lon"),
        (["Start:50.7", "End:50.8,-1.6"], "a waypoint must look like Name:lat,lon"),
        ([":50.7,-1.2", "End:50.8,-1.6"], "a waypoint must look like Name:lat,lon"),
    ],
)
def test_course_plot_rejects_bad_waypoints_without_writing(store, waypoints, expected):
    args = ["course", "plot", "--name", "Bad"]
    for waypoint in waypoints:
        args += ["--waypoint", waypoint]

    result = run(args, store=store)

    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert expected in error_lines(result)[0]
    assert not store.exists()
    assert "no courses yet" in run(["course", "list"], store=store).stdout


def test_course_plot_requires_at_least_two_waypoints(store):
    result = run(
        ["course", "plot", "--name", "Short", "--waypoint", "Only:50.00,-1.00"], store=store
    )

    assert result.returncode == 2
    assert error_lines(result) == ["error: a course needs at least 2 waypoints (got 1)"]
    assert not store.exists()


def test_replotting_a_course_replaces_its_waypoints(store):
    run(
        [
            "course", "plot", "--name", "Solent",
            "--waypoint", "Start:50.70,-1.20",
            "--waypoint", "Mid:50.75,-1.40",
            "--waypoint", "End:50.80,-1.60",
        ],
        store=store,
    )

    again = run(
        ["course", "plot", "--name", "Solent", "--waypoint", "A:50.70,-1.20", "--waypoint", "B:50.80,-1.60"],
        store=store,
    )

    assert again.returncode == 0
    assert "updated course Solent with 2 waypoints" in again.stdout
    assert "2 waypoints" in run(["course", "list"], store=store).stdout


def test_course_chart_link_must_exist(store):
    add_chart(store)
    args = ["course", "plot", "--name", "Linked", "--waypoint", "A:50.70,-1.20", "--waypoint", "B:50.80,-1.60"]

    missing = run(args + ["--chart", "4242"], store=store)
    assert missing.returncode == 2
    assert error_lines(missing) == ["error: chart 4242 not found"]
    assert "no courses yet" in run(["course", "list"], store=store).stdout

    linked = run(args + ["--chart", "1234"], store=store)
    assert linked.returncode == 0
    assert "1234" in run(["course", "list"], store=store).stdout


def test_course_show_lists_waypoints_legs_and_total(store):
    run(
        [
            "course", "plot", "--name", "Solent",
            "--waypoint", "Start:50.70,-1.20",
            "--waypoint", "Mid:50.75,-1.40",
            "--waypoint", "End:50.80,-1.60",
        ],
        store=store,
    )

    shown = run(["course", "show", "--name", "Solent"], store=store)

    assert shown.returncode == 0
    assert shown.stdout.index("Start") < shown.stdout.index("Mid") < shown.stdout.index("End")
    assert "50.70000, -1.20000" in shown.stdout
    assert len(parse_legs(shown.stdout)) == 2
    assert TOTAL_RE.search(shown.stdout)


def test_course_remove_and_unknown_course_errors(store):
    run(
        ["course", "plot", "--name", "Solent", "--waypoint", "A:50.70,-1.20", "--waypoint", "B:50.80,-1.60"],
        store=store,
    )

    removed = run(["course", "remove", "--name", "Solent"], store=store)
    assert removed.returncode == 0
    assert "removed course Solent" in removed.stdout
    assert "no courses yet" in run(["course", "list"], store=store).stdout

    for action in ("show", "remove"):
        result = run(["course", action, "--name", "Missing"], store=store)
        assert result.returncode == 2
        assert error_lines(result) == ["error: course Missing not found"]


def test_charts_removal_leaves_courses_untouched(store):
    add_chart(store)
    run(
        ["course", "plot", "--name", "Solent", "--chart", "1234", "--waypoint", "A:50.70,-1.20", "--waypoint", "B:50.80,-1.60"],
        store=store,
    )

    run(["charts", "remove", "--number", "1234"], store=store)

    assert "Solent" in run(["course", "list"], store=store).stdout
