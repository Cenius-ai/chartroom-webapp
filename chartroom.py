#!/usr/bin/env python3
"""Chartroom - offline chart inventory, course plotting and tide log.

One standard-library program over one local JSON document:

    python3 chartroom.py charts add --number 1234 --title 'Solent Approach'
    python3 chartroom.py course plot --name Solent --waypoint Start:50.70,-1.20 \
        --waypoint End:50.80,-1.60
    python3 chartroom.py tide add --station Portsmouth --time 2024-06-01T09:00 --height 2.4

Nothing leaves the machine: no sockets, no HTTP, no telemetry, no remote assets.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Design tokens - committed direction: "calm-precise", terminal dark surface,
# system monospace type, compact density, aligned columns, box-drawing rules
# and exactly one accent for primary status. Every signal is textual as well
# as coloured, so nothing depends on colour alone.
# --------------------------------------------------------------------------
PROGRAM = "chartroom.py"
VERSION = "1.0.0"

ACCENT_RGB = (176, 100, 45)  # oklch(0.58 0.12 53) resolved to sRGB (#b0642d)
DIM_SGR = "2"
BOLD_SGR = "1"
RULE_CHAR = "\u2500"  # box drawing light horizontal
INDENT = "  "

SCHEMA_VERSION = 1
EARTH_RADIUS_NM = 3440.065
DEFAULT_STORE_PATH = Path.home() / ".chartroom" / "store.json"
STORE_ENV_VAR = "CHARTROOM_STORE"

EXIT_OK = 0
EXIT_STORE_ERROR = 1
EXIT_USAGE_ERROR = 2

TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")

# Empty-state invitations. These are the literal commands a new user can paste;
# the README quickstart repeats them word for word.
EMPTY_CHARTS_HINT = (
    "add one with: python chartroom.py charts add --number 1234 "
    "--title 'Solent Approach' --region Solent --scale 25000"
)
EMPTY_COURSES_HINT = (
    "plot one with: python chartroom.py course plot --name Solent "
    "--waypoint Start:50.70,-1.20 --waypoint End:50.80,-1.60"
)
EMPTY_TIDES_HINT = (
    "add one with: python chartroom.py tide add --station Portsmouth "
    "--time 2024-06-01T09:00 --height 2.4"
)


class ChartroomError(Exception):
    """A usage or validation failure: one stderr line, exit code 2, no writes."""


class StoreError(Exception):
    """The store exists but cannot be read: one stderr line, exit code 1."""


class Palette:
    """ANSI styling that switches itself off for pipes, files and NO_COLOR."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, sgr: str, text: str) -> str:
        if not self.enabled or not text:
            return text
        return "\x1b[{}m{}\x1b[0m".format(sgr, text)

    def accent(self, text: str) -> str:
        red, green, blue = ACCENT_RGB
        return self._wrap("38;2;{};{};{}".format(red, green, blue), text)

    def dim(self, text: str) -> str:
        return self._wrap(DIM_SGR, text)

    def bold(self, text: str) -> str:
        return self._wrap(BOLD_SGR, text)


def color_enabled(no_color_flag: bool) -> bool:
    if no_color_flag:
        return False
    if "NO_COLOR" in os.environ:
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Store: one JSON document, written through a temp file + os.replace
# --------------------------------------------------------------------------
def resolve_store_path(explicit: Optional[str]) -> Path:
    """`--store PATH` wins, then CHARTROOM_STORE, then ~/.chartroom/store.json."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(STORE_ENV_VAR, "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_STORE_PATH


def empty_store() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "charts": [],
        "courses": [],
        "tides": [],
        "next_tide_id": 1,
    }


def _unreadable(path: Path, reason: str) -> StoreError:
    return StoreError("store is unreadable: {} ({})".format(path, reason))


def _normalise_store(data: Any, path: Path) -> Dict[str, Any]:
    """Accept only a sane store document; anything else is exit code 1."""
    if not isinstance(data, dict):
        raise _unreadable(path, "top level is not a JSON object")
    charts = data.get("charts", [])
    courses = data.get("courses", [])
    tides = data.get("tides", [])
    for name, records in (("charts", charts), ("courses", courses), ("tides", tides)):
        if not isinstance(records, list):
            raise _unreadable(path, "'{}' is not a list".format(name))
        if any(not isinstance(record, dict) for record in records):
            raise _unreadable(path, "'{}' contains a non-object record".format(name))

    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int):
        raise _unreadable(path, "schema_version is not an integer")

    highest_id = max([int(entry.get("id") or 0) for entry in tides] + [0])
    next_tide_id = data.get("next_tide_id")
    if not isinstance(next_tide_id, int) or next_tide_id <= highest_id:
        next_tide_id = highest_id + 1

    return {
        "schema_version": version,
        "charts": charts,
        "courses": courses,
        "tides": tides,
        "next_tide_id": next_tide_id,
    }


def load_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return empty_store()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _unreadable(path, exc.strerror or str(exc))
    if not raw.strip():
        raise _unreadable(path, "file is empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _unreadable(path, "invalid JSON at line {}".format(exc.lineno))
    return _normalise_store(data, path)


def save_store(path: Path, store: Dict[str, Any]) -> None:
    """Atomic write: an interrupted command cannot leave half-written JSON."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreError(
            "store could not be written: {} ({})".format(path, exc.strerror or str(exc))
        )
    payload = json.dumps(store, indent=2) + "\n"
    handle = None
    temp_path = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".store-", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp_name, str(path))
    except OSError as exc:
        raise StoreError(
            "store could not be written: {} ({})".format(path, exc.strerror or str(exc))
        )
    finally:
        if handle is not None:
            handle.close()
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Great-circle geometry
# --------------------------------------------------------------------------
def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (mean Earth radius 3440.065 nm)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from waypoint 1 to waypoint 2, degrees in [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)
    x = math.sin(delta_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        delta_lambda
    )
    if x == 0.0 and y == 0.0:
        return 0.0
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def course_legs(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Leg geometry for a course, derived at read time from its waypoints."""
    waypoints = course.get("waypoints", [])
    legs: List[Dict[str, Any]] = []
    for seq, (start, end) in enumerate(zip(waypoints, waypoints[1:]), start=1):
        lat1, lon1 = float(start["latitude"]), float(start["longitude"])
        lat2, lon2 = float(end["latitude"]), float(end["longitude"])
        legs.append(
            {
                "seq": seq,
                "from": start.get("name", ""),
                "to": end.get("name", ""),
                "distance_nm": round(haversine_nm(lat1, lon1, lat2, lon2), 2),
                "bearing_deg": round(initial_bearing_deg(lat1, lon1, lat2, lon2), 1),
            }
        )
    return legs


def total_distance_nm(legs: Sequence[Dict[str, Any]]) -> float:
    return round(sum(leg["distance_nm"] for leg in legs), 2)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def natural_key(text: str) -> List[Tuple[int, Any]]:
    """Sort key so chart numbers order naturally: 7 before 12 before BA99."""
    parts = re.split(r"(\d+)", str(text))
    return [(0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts if part]


IRREGULAR_PLURALS = {"tide entry": "tide entries"}


def plural(count: int, singular: str) -> str:
    """'1 chart' / '3 charts' - counts are part of the greppable contract."""
    if count == 1:
        return singular
    return IRREGULAR_PLURALS.get(singular, singular + "s")


def counted(count: int, singular: str) -> str:
    return "{} {}".format(count, plural(count, singular))


def display(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def format_height(height: float) -> str:
    return "{:.2f} m".format(height)


def format_scale(scale: Any) -> str:
    if not isinstance(scale, int) or isinstance(scale, bool):
        return "-"
    return str(scale)


def rule(widths: Sequence[int]) -> str:
    return INDENT + "  ".join(RULE_CHAR * width for width in widths)


def render_table(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], palette: Palette
) -> List[str]:
    """Aligned columns under a box-drawing rule - the one table surface."""
    text_rows = [[str(cell) for cell in row] for row in rows]
    widths = [
        max([len(str(headers[index]))] + [len(row[index]) for row in text_rows])
        for index in range(len(headers))
    ]

    def line(cells: Sequence[str]) -> str:
        padded = [cell.ljust(widths[index]) for index, cell in enumerate(cells)]
        return (INDENT + "  ".join(padded)).rstrip()

    output = [palette.bold(line([str(header) for header in headers]))]
    output.append(palette.dim(rule(widths)))
    output.extend(line(row) for row in text_rows)
    output.append(palette.dim(rule(widths)))
    return output


def leg_lines(course: Dict[str, Any]) -> List[str]:
    legs = course_legs(course)
    labels = ["{} -> {}".format(leg["from"], leg["to"]) for leg in legs]
    width = max([len(label) for label in labels] + [0])
    lines = [
        "leg {seq}  {label}  {distance:.2f} nm   bearing {bearing:05.1f}\u00b0 true".format(
            seq=leg["seq"],
            label=labels[index].ljust(width),
            distance=leg["distance_nm"],
            bearing=leg["bearing_deg"],
        )
        for index, leg in enumerate(legs)
    ]
    lines.append("total distance {:.2f} nm".format(total_distance_nm(legs)))
    return lines


def waypoint_lines(course: Dict[str, Any]) -> List[str]:
    waypoints = course.get("waypoints", [])
    width = max([len(str(point.get("name", ""))) for point in waypoints] + [0])
    return [
        "waypoint {seq}  {name}  {latitude:.5f}, {longitude:.5f}".format(
            seq=point.get("seq", index + 1),
            name=str(point.get("name", "")).ljust(width),
            latitude=float(point.get("latitude", 0.0)),
            longitude=float(point.get("longitude", 0.0)),
        )
        for index, point in enumerate(waypoints)
    ]


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Validation helpers - every failure raises before anything is written
# --------------------------------------------------------------------------
def require_text(value: Optional[str], flag: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ChartroomError("{} must not be blank".format(flag))
    return text


def parse_whole_number(value: str, flag: str, minimum: int = 1) -> int:
    text = (value or "").strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ChartroomError("{} must be a whole number (got {!r})".format(flag, value))
    number = int(text)
    if number < minimum:
        raise ChartroomError(
            "{} must be a positive whole number (got {})".format(flag, number)
        )
    return number


def parse_waypoint(spec: str, seq: int) -> Dict[str, Any]:
    malformed = (
        "waypoint {} ({!r}) is malformed: a waypoint must look like Name:lat,lon".format(
            seq, spec
        )
    )
    if ":" not in spec:
        raise ChartroomError(malformed)
    name, _, coordinates = spec.rpartition(":")
    name = name.strip()
    parts = coordinates.split(",")
    if not name or len(parts) != 2:
        raise ChartroomError(malformed)
    try:
        latitude = float(parts[0].strip())
        longitude = float(parts[1].strip())
    except ValueError:
        raise ChartroomError(malformed)
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise ChartroomError(malformed)
    if not -90.0 <= latitude <= 90.0:
        raise ChartroomError(
            "waypoint {} ({!r}) latitude must be between -90 and 90 (got {})".format(
                seq, name, parts[0].strip()
            )
        )
    if not -180.0 <= longitude <= 180.0:
        raise ChartroomError(
            "waypoint {} ({!r}) longitude must be between -180 and 180 (got {})".format(
                seq, name, parts[1].strip()
            )
        )
    return {
        "seq": seq,
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
    }


def parse_station(value: str) -> str:
    station = (value or "").strip()
    if not re.search(r"[0-9A-Za-z]", station):
        raise ChartroomError(
            "--station must contain at least one letter or digit (got {!r})".format(value)
        )
    return station


def parse_time(value: str) -> str:
    text = (value or "").strip()
    if not TIME_PATTERN.match(text):
        raise ChartroomError("the time must look like YYYY-MM-DDTHH:MM (got {!r})".format(value))
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ChartroomError("the time must look like YYYY-MM-DDTHH:MM (got {!r})".format(value))
    return text


def parse_height(value: str) -> float:
    try:
        height = float((value or "").strip())
    except ValueError:
        raise ChartroomError("the height must be a number (got {!r})".format(value))
    if not math.isfinite(height):
        raise ChartroomError("the height must be a number (got {!r})".format(value))
    return height


# --------------------------------------------------------------------------
# Command context and results
# --------------------------------------------------------------------------
class Context:
    """Everything a command handler needs to read and render."""

    def __init__(self, store_path: Path, palette: Palette, as_json: bool) -> None:
        self.store_path = store_path
        self.palette = palette
        self.as_json = as_json


class Result:
    def __init__(self, lines: Sequence[str], payload: Dict[str, Any]) -> None:
        self.lines = list(lines)
        self.payload = payload


def status_line(palette: Palette, word: str, rest: str) -> str:
    """The one accent marks the primary status word; the text carries the meaning."""
    return "{} {}".format(palette.accent(word), rest)


# --------------------------------------------------------------------------
# charts add | list | remove
# --------------------------------------------------------------------------
def cmd_charts_add(args: argparse.Namespace, ctx: Context) -> Result:
    number = require_text(args.number, "--number")
    title = require_text(args.title, "--title")
    region = (args.region or "").strip() or "-"
    scale = parse_whole_number(args.scale, "--scale") if args.scale is not None else None

    store = load_store(ctx.store_path)
    if any(str(chart.get("number")) == number for chart in store["charts"]):
        raise ChartroomError("chart {} already exists".format(number))

    chart = {
        "number": number,
        "title": title,
        "region": region,
        "scale": scale,
        "added_at": timestamp(),
    }
    store["charts"].append(chart)
    save_store(ctx.store_path, store)

    details = []
    if region != "-":
        details.append("region " + region)
    if scale is not None:
        details.append("scale " + format_scale(scale))
    suffix = " ({})".format(", ".join(details)) if details else ""
    line = status_line(ctx.palette, "added", "chart {}: {}{}".format(number, title, suffix))
    return Result([line], {"status": "added", "chart": chart})


def cmd_charts_list(args: argparse.Namespace, ctx: Context) -> Result:
    store = load_store(ctx.store_path)
    charts = sorted(store["charts"], key=lambda chart: natural_key(chart.get("number", "")))
    if not charts:
        return Result(
            ["no charts yet", EMPTY_CHARTS_HINT],
            {"charts": [], "count": 0, "hint": EMPTY_CHARTS_HINT},
        )
    rows = [
        [
            display(chart.get("number")),
            display(chart.get("title")),
            display(chart.get("region")),
            format_scale(chart.get("scale")),
        ]
        for chart in charts
    ]
    lines = render_table(["NUMBER", "TITLE", "REGION", "SCALE"], rows, ctx.palette)
    lines.append(ctx.palette.accent(counted(len(charts), "chart")))
    return Result(lines, {"charts": charts, "count": len(charts)})


def cmd_charts_remove(args: argparse.Namespace, ctx: Context) -> Result:
    number = require_text(args.number, "--number")
    store = load_store(ctx.store_path)
    kept = [chart for chart in store["charts"] if str(chart.get("number")) != number]
    if len(kept) == len(store["charts"]):
        raise ChartroomError("chart {} not found".format(number))
    store["charts"] = kept
    save_store(ctx.store_path, store)
    return Result(
        [status_line(ctx.palette, "removed", "chart {}".format(number))],
        {"status": "removed", "number": number},
    )


# --------------------------------------------------------------------------
# course plot | list | show | remove
# --------------------------------------------------------------------------
def _course_payload(course: Dict[str, Any]) -> Dict[str, Any]:
    legs = course_legs(course)
    return {
        "course": course,
        "legs": legs,
        "total_distance_nm": total_distance_nm(legs),
    }


def cmd_course_plot(args: argparse.Namespace, ctx: Context) -> Result:
    name = require_text(args.name, "--name")
    raw_waypoints = list(args.waypoint or [])
    if len(raw_waypoints) < 2:
        raise ChartroomError(
            "a course needs at least 2 waypoints (got {})".format(len(raw_waypoints))
        )
    waypoints = [
        parse_waypoint(spec, seq) for seq, spec in enumerate(raw_waypoints, start=1)
    ]

    store = load_store(ctx.store_path)
    chart_number = None
    if args.chart is not None:
        chart_number = require_text(args.chart, "--chart")
        known = {str(chart.get("number")) for chart in store["charts"]}
        if chart_number not in known:
            raise ChartroomError("chart {} not found".format(chart_number))

    now = timestamp()
    existing = next(
        (course for course in store["courses"] if course.get("name") == name), None
    )
    if existing is None:
        course = {
            "name": name,
            "chart_number": chart_number,
            "created_at": now,
            "updated_at": now,
            "waypoints": waypoints,
        }
        store["courses"].append(course)
        word, rest = "plotted", "{} with {} waypoints".format(name, len(waypoints))
    else:
        existing["chart_number"] = chart_number
        existing["updated_at"] = now
        existing["waypoints"] = waypoints
        course = existing
        word, rest = "updated", "course {} with {} waypoints".format(name, len(waypoints))
    save_store(ctx.store_path, store)

    payload = _course_payload(course)
    lines = [status_line(ctx.palette, word, rest)]
    if chart_number:
        lines.append("chart {}".format(chart_number))
    lines.append("")
    lines.extend(leg_lines(course))
    payload["status"] = "plotted" if word == "plotted" else "updated"
    return Result(lines, payload)


def cmd_course_list(args: argparse.Namespace, ctx: Context) -> Result:
    store = load_store(ctx.store_path)
    courses = sorted(store["courses"], key=lambda course: str(course.get("name", "")).lower())
    if not courses:
        return Result(
            ["no courses yet", EMPTY_COURSES_HINT],
            {"courses": [], "count": 0, "hint": EMPTY_COURSES_HINT},
        )
    rows = [
        [
            display(course.get("name")),
            counted(len(course.get("waypoints", [])), "waypoint"),
            display(course.get("chart_number")),
        ]
        for course in courses
    ]
    lines = render_table(["COURSE", "WAYPOINTS", "CHART"], rows, ctx.palette)
    lines.append(ctx.palette.accent(counted(len(courses), "course")))
    summary = [
        {
            "name": course.get("name"),
            "waypoint_count": len(course.get("waypoints", [])),
            "chart_number": course.get("chart_number"),
        }
        for course in courses
    ]
    return Result(lines, {"courses": summary, "count": len(courses)})


def cmd_course_show(args: argparse.Namespace, ctx: Context) -> Result:
    name = require_text(args.name, "--name")
    store = load_store(ctx.store_path)
    course = next(
        (item for item in store["courses"] if item.get("name") == name), None
    )
    if course is None:
        raise ChartroomError("course {} not found".format(name))
    lines = [
        "name {}".format(display(course.get("name"))),
        "chart {}".format(display(course.get("chart_number"))),
        counted(len(course.get("waypoints", [])), "waypoint"),
        "created {}".format(display(course.get("created_at"))),
        "updated {}".format(display(course.get("updated_at"))),
        "",
    ]
    lines.extend(waypoint_lines(course))
    lines.append("")
    lines.extend(leg_lines(course))
    return Result(lines, _course_payload(course))


def cmd_course_remove(args: argparse.Namespace, ctx: Context) -> Result:
    name = require_text(args.name, "--name")
    store = load_store(ctx.store_path)
    kept = [course for course in store["courses"] if course.get("name") != name]
    if len(kept) == len(store["courses"]):
        raise ChartroomError("course {} not found".format(name))
    store["courses"] = kept
    save_store(ctx.store_path, store)
    return Result(
        [status_line(ctx.palette, "removed", "course {}".format(name))],
        {"status": "removed", "name": name},
    )


# --------------------------------------------------------------------------
# tide add | list | remove
# --------------------------------------------------------------------------
def cmd_tide_add(args: argparse.Namespace, ctx: Context) -> Result:
    station = parse_station(args.station)
    time_text = parse_time(args.time)
    height = parse_height(args.height)

    store = load_store(ctx.store_path)
    entry = {
        "id": int(store["next_tide_id"]),
        "station": station,
        "time": time_text,
        "height_m": height,
    }
    store["tides"].append(entry)
    store["next_tide_id"] = entry["id"] + 1
    save_store(ctx.store_path, store)

    line = status_line(
        ctx.palette,
        "added",
        "tide entry {id}: {station} {time} {height}".format(
            id=entry["id"],
            station=station,
            time=time_text,
            height=format_height(height),
        ),
    )
    return Result([line], {"status": "added", "entry": entry})


def cmd_tide_list(args: argparse.Namespace, ctx: Context) -> Result:
    store = load_store(ctx.store_path)
    tides = sorted(
        store["tides"], key=lambda entry: (str(entry.get("time", "")), int(entry.get("id") or 0))
    )
    if not tides:
        return Result(
            ["no tide entries", EMPTY_TIDES_HINT],
            {"tides": [], "count": 0, "hint": EMPTY_TIDES_HINT},
        )
    rows = [
        [
            display(entry.get("id")),
            display(entry.get("station")),
            display(entry.get("time")),
            format_height(float(entry.get("height_m", 0.0))),
        ]
        for entry in tides
    ]
    lines = render_table(["ID", "STATION", "TIME", "HEIGHT"], rows, ctx.palette)
    lines.append(ctx.palette.accent(counted(len(tides), "tide entry")))
    return Result(lines, {"tides": tides, "count": len(tides)})


def cmd_tide_remove(args: argparse.Namespace, ctx: Context) -> Result:
    entry_id = parse_whole_number(args.id, "--id")
    store = load_store(ctx.store_path)
    kept = [entry for entry in store["tides"] if int(entry.get("id") or 0) != entry_id]
    if len(kept) == len(store["tides"]):
        raise ChartroomError("tide entry {} not found".format(entry_id))
    store["tides"] = kept
    save_store(ctx.store_path, store)
    return Result(
        [status_line(ctx.palette, "removed", "tide entry {}".format(entry_id))],
        {"status": "removed", "id": entry_id},
    )


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
HELP_EPILOG = """\
examples:
  python chartroom.py charts add --number 1234 --title 'Solent Approach' --region Solent --scale 25000
  python chartroom.py charts list
  python chartroom.py course plot --name Solent --chart 1234 --waypoint Start:50.70,-1.20 --waypoint Mid:50.75,-1.40 --waypoint End:50.80,-1.60
  python chartroom.py course show --name Solent
  python chartroom.py tide add --station Portsmouth --time 2024-06-01T09:00 --height 2.4
  python chartroom.py tide list

store:
  the JSON store defaults to ~/.chartroom/store.json; use --store PATH or the
  CHARTROOM_STORE variable to keep demos and checks away from real data.

exit codes:
  0 success | 1 unreadable or unwritable store | 2 usage or validation error
"""

STORE_HELP = "JSON store file (default: ~/.chartroom/store.json, or $CHARTROOM_STORE)"
JSON_HELP = "print machine-readable JSON instead of the human-readable table"
COLOR_HELP = "disable ANSI colour (NO_COLOR is honoured as well)"


def _shared_options() -> argparse.ArgumentParser:
    """Options accepted before or after the command, e.g. `charts list --store X`."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--store", metavar="PATH", default=argparse.SUPPRESS, help=STORE_HELP)
    shared.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help=JSON_HELP
    )
    shared.add_argument(
        "--no-color", action="store_true", default=argparse.SUPPRESS, help=COLOR_HELP
    )
    return shared


def _action_parser(
    actions: "argparse._SubParsersAction", name: str, help_text: str, handler: Any,
    group_parser: argparse.ArgumentParser, shared: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    parser = actions.add_parser(
        name,
        parents=[shared],
        help=help_text,
        description=help_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(handler=handler, group_parser=group_parser, action=name)
    return parser


def build_parser() -> argparse.ArgumentParser:
    shared = _shared_options()
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Chartroom keeps a local inventory of the nautical charts aboard, the "
            "courses plotted from them and a log of observed tide heights."
        ),
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="chartroom {}".format(VERSION))
    parser.add_argument("--store", metavar="PATH", default=None, help=STORE_HELP)
    parser.add_argument("--json", action="store_true", default=False, help=JSON_HELP)
    parser.add_argument("--no-color", action="store_true", default=False, help=COLOR_HELP)

    groups = parser.add_subparsers(dest="group", metavar="{charts,course,tide}")

    charts = groups.add_parser(
        "charts",
        parents=[shared],
        help="chart inventory: add, list and remove charts aboard",
        description="Chart inventory: add, list and remove the charts aboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    charts.set_defaults(group_parser=charts)
    charts_actions = charts.add_subparsers(dest="action", metavar="{add,list,remove}")
    chart_add = _action_parser(
        charts_actions, "add", "record a chart aboard (number and title required)",
        cmd_charts_add, charts, shared,
    )
    chart_add.add_argument("--number", required=True, metavar="N", help="chart number, e.g. 1234 or BA1234")
    chart_add.add_argument("--title", required=True, metavar="T", help="chart title, e.g. Solent Approach")
    chart_add.add_argument("--region", metavar="R", default=None, help="chart region, e.g. Solent")
    chart_add.add_argument("--scale", metavar="S", default=None, help="positive whole-number scale, e.g. 25000")
    _action_parser(
        charts_actions, "list", "list the charts aboard with a total count",
        cmd_charts_list, charts, shared,
    )
    chart_remove = _action_parser(
        charts_actions, "remove", "remove a chart by number",
        cmd_charts_remove, charts, shared,
    )
    chart_remove.add_argument("--number", required=True, metavar="N", help="chart number to remove")

    course = groups.add_parser(
        "course",
        parents=[shared],
        help="course plotting: plot, list, show and remove courses",
        description="Courses: plot waypoints, list saved courses, show leg geometry, remove.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    course.set_defaults(group_parser=course)
    course_actions = course.add_subparsers(dest="action", metavar="{plot,list,show,remove}")
    course_plot = _action_parser(
        course_actions, "plot", "plot a named course from ordered Name:lat,lon waypoints",
        cmd_course_plot, course, shared,
    )
    course_plot.add_argument("--name", required=True, metavar="N", help="course name, e.g. Solent")
    course_plot.add_argument(
        "--waypoint", action="append", metavar="NAME:LAT,LON", default=None,
        help="repeatable, at least two: e.g. --waypoint Start:50.70,-1.20",
    )
    course_plot.add_argument("--chart", metavar="NUMBER", default=None, help="chart number this course was plotted on")
    _action_parser(
        course_actions, "list", "list saved courses with waypoint counts and charts",
        cmd_course_list, course, shared,
    )
    course_show = _action_parser(
        course_actions, "show", "show a course's waypoints, legs and total distance",
        cmd_course_show, course, shared,
    )
    course_show.add_argument("--name", required=True, metavar="N", help="course name to show")
    course_remove = _action_parser(
        course_actions, "remove", "remove a saved course by name",
        cmd_course_remove, course, shared,
    )
    course_remove.add_argument("--name", required=True, metavar="N", help="course name to remove")

    tide = groups.add_parser(
        "tide",
        parents=[shared],
        help="tide table log: add, list and remove tide heights",
        description="Tide table log: add, list and remove observed tide heights.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    tide.set_defaults(group_parser=tide)
    tide_actions = tide.add_subparsers(dest="action", metavar="{add,list,remove}")
    tide_add = _action_parser(
        tide_actions, "add", "record an observed tide height at a station",
        cmd_tide_add, tide, shared,
    )
    tide_add.add_argument("--station", required=True, metavar="S", help="station name, e.g. Portsmouth")
    tide_add.add_argument("--time", required=True, metavar="YYYY-MM-DDTHH:MM", help="naive local timestamp")
    tide_add.add_argument("--height", required=True, metavar="H", help="height in metres (zero or negative allowed)")
    _action_parser(
        tide_actions, "list", "list tide entries in chronological order",
        cmd_tide_list, tide, shared,
    )
    tide_remove = _action_parser(
        tide_actions, "remove", "remove a tide entry by id",
        cmd_tide_remove, tide, shared,
    )
    tide_remove.add_argument("--id", required=True, metavar="N", help="tide entry id to remove")

    return parser


def emit(ctx: Context, result: Result) -> None:
    if ctx.as_json:
        print(json.dumps(result.payload, indent=2))
        return
    for line in result.lines:
        print(line)


def report_error(as_json: bool, message: str, code: int) -> None:
    """Exactly one stderr line per failure, then the process exits."""
    if as_json:
        print(json.dumps({"error": message}), file=sys.stderr)
    else:
        print("error: {}".format(message), file=sys.stderr)
    raise SystemExit(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "group", None):
        parser.print_help()
        return EXIT_OK
    handler = getattr(args, "handler", None)
    if handler is None:
        args.group_parser.print_help()
        return EXIT_OK

    ctx = Context(
        store_path=resolve_store_path(getattr(args, "store", None)),
        palette=Palette(color_enabled(bool(getattr(args, "no_color", False)))),
        as_json=bool(getattr(args, "json", False)),
    )
    result = None
    try:
        result = handler(args, ctx)
    except ChartroomError as exc:
        report_error(ctx.as_json, str(exc), EXIT_USAGE_ERROR)
    except StoreError as exc:
        report_error(ctx.as_json, str(exc), EXIT_STORE_ERROR)
    if result is not None:
        emit(ctx, result)
    return EXIT_OK


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Downstream closed the pipe (e.g. `chartroom.py charts list | head`).
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        raise SystemExit(EXIT_OK)
