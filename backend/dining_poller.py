"""Poll the BU dining API for the upcoming one-week-ahead window.

Each run of this script:

    1. Reads ``API_KEY`` from the ``.env`` file.
    2. Walks the window of today through today + 7 (8 days total). The
       first and last day share the same weekday (e.g. Monday through the
       following Monday), so week-over-week comparisons line up. For each
       day that does not already have a folder, it requests the dining
       locations and each location's menu for that day and writes the JSON
       responses into the day's folder. Days that already exist are left
       untouched.
    3. Deletes any folder in the ``data`` directory whose date falls
       outside the window -- past days as well as anything beyond the
       end of the window.

The menu endpoint accepts a ``day`` query parameter, valid from
yesterday through 14 days ahead, so every day in the forward window can
be fetched with its own real menu. The script relies only on the Python
standard library, so no extra packages need to be installed.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# BU's dining calendar (and the web app) operate in Boston local time, so the
# day window must be computed in this zone, not UTC. Using UTC caused the
# poller to skip the real "today" for several hours each night.
CAMPUS_TZ = ZoneInfo("America/New_York")

# ==================================================
# 1. Configuration
# ==================================================

BASE_URL = "https://public.api-nonprod.bu.edu/api/v1/dining"

LOCATIONS_URL = f"{BASE_URL}/locations"

# The set of dining locations rarely changes day to day, so it is kept as a
# static file in this directory instead of being re-fetched on every poll.
# Regenerate it only when BU adds or removes a hall.
LOCATIONS_FILE = Path(__file__).resolve().parent / "locations.json"

# ``{location}`` is replaced with each id from the locations response.
MENU_URL_TEMPLATE = f"{BASE_URL}/locations/{{location}}/menu"

# The API authenticates requests with this header.
API_KEY_HEADER = "X-API-Key"

# Per-request network timeout, in seconds.
REQUEST_TIMEOUT = 30

# Repository root (one level above this ``server`` directory).
REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_FILE = REPO_ROOT / ".env"

# Snapshots are written here, one subfolder per day (``YYYY-MM-DD``).
DATA_DIR = Path(__file__).resolve().parent / "data"

# Permanent archive of every day ever collected, one subfolder per day
# (``YYYY-MM-DD``). Unlike DATA_DIR -- a rolling window that is pruned -- the
# archive is never pruned, so the web app's calendar can reach back through the
# full history. Each poll copies any complete day into the archive. Can be
# overridden with the ARCHIVE_DIR env var (e.g. to a shared drive).
ARCHIVE_DIR = Path(
    os.environ.get("ARCHIVE_DIR", str(Path(__file__).resolve().parent / "data_archive"))
)

# Folder-name format for a day's snapshot directory.
DAY_FORMAT = "%Y-%m-%d"

# How many days in advance to cover. The window is today plus the next
# DAYS_AHEAD days, i.e. it ends on today + DAYS_AHEAD. A folder is kept
# only when its date falls inside that range. With DAYS_AHEAD = 7 the last
# day lands on the same weekday as today (e.g. Monday through the following
# Monday), so the stats compare matching weekdays a week apart.
DAYS_AHEAD = 7

# Total number of day-folders in the window (today plus DAYS_AHEAD days).
WINDOW_DAYS = DAYS_AHEAD + 1


# ==================================================
# 2. Environment loading
# ==================================================

def load_api_key(env_path=ENV_FILE):
    """Read ``API_KEY`` from the environment, else a simple ``KEY=VALUE`` ``.env`` file."""

    # Hosts like Render set API_KEY as an environment variable instead of a file.
    if os.environ.get("API_KEY"):
        return os.environ["API_KEY"]

    if not env_path.exists():
        raise FileNotFoundError(
            f"Could not find an .env file at {env_path}."
        )

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():

        line = raw_line.strip()

        # Skip blank lines and comments.
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")

        if key.strip() != "API_KEY":
            continue

        # Trim optional surrounding quotes.
        value = value.strip().strip('"').strip("'")

        if value:
            return value

    raise ValueError(
        f"API_KEY was not found (or was empty) in {env_path}."
    )


# ==================================================
# 3. HTTP helper
# ==================================================

def fetch_json(url, api_key):
    """Perform a GET request and return the decoded JSON body."""

    request = urllib.request.Request(
        url,
        headers={
            API_KEY_HEADER: api_key,
            "Accept": "application/json",
        },
        method="GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:

        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset)

    return json.loads(body)


# ==================================================
# 4. Snapshot persistence
# ==================================================

def timestamp_label():
    """Return a filesystem-safe UTC timestamp, e.g. 20261024T153000Z."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def today():
    """Return today's date in Boston (America/New_York).

    BU's menu API validates ``day`` against its own Eastern-time clock, and the
    web app requests menus by Boston date, so the poller's window must be
    anchored to the same zone. Computing this in UTC would, for the hours when
    UTC has already rolled to the next day, skip the genuine current day.
    """

    return datetime.now(CAMPUS_TZ).date()


def window_dates():
    """Return the window's dates: today through today + WINDOW_DAYS - 1."""

    start = today()
    return [start + timedelta(days=offset) for offset in range(WINDOW_DAYS)]


def dir_for_date(day):
    """Return the snapshot folder for a date, e.g. data/2026-10-24."""

    return DATA_DIR / day.strftime(DAY_FORMAT)


def write_snapshot(day_dir, name, payload):
    """Write one JSON payload into the day's folder, overwriting it."""

    day_dir.mkdir(parents=True, exist_ok=True)

    output_path = day_dir / f"{name}.json"

    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return output_path


def remove_dir(path):
    """Recursively delete a directory and everything inside it."""

    for item in sorted(path.rglob("*"), reverse=True):
        if item.is_file() or item.is_symlink():
            item.unlink()
        else:
            item.rmdir()
    path.rmdir()


def archive_completed_days():
    """Copy every complete day in DATA_DIR into the permanent ARCHIVE_DIR.

    The archive preserves days after they roll out of the rolling window, so the
    web app's calendar can reach backwards indefinitely. A day is copied only
    when it is complete (a menu file for every known location), so partially
    fetched days are never archived in a broken state. An already-archived day
    is refreshed in place (the newest complete copy wins), and the archive is
    never pruned. Returns the number of day-folders archived or refreshed.
    """

    if not DATA_DIR.exists():
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = 0

    for child in sorted(DATA_DIR.iterdir()):
        if not child.is_dir():
            continue
        # Only archive real dated folders.
        try:
            day = datetime.strptime(child.name, DAY_FORMAT).date()
        except ValueError:
            continue
        if not day_is_complete(day):
            continue

        dest = ARCHIVE_DIR / child.name
        dest.mkdir(parents=True, exist_ok=True)
        for src_file in child.glob("*.json"):
            dest_file = dest / src_file.name
            dest_file.write_bytes(src_file.read_bytes())
        archived += 1

    if archived:
        print(
            f"  Archived {archived} complete day(s) to {ARCHIVE_DIR}.",
            flush=True,
        )
    return archived


def prune_out_of_window():
    """Delete day folders whose date is outside the forward window.

    The window is today through today + WINDOW_DAYS - 1. A folder is kept
    only when its date falls inside that range; past days and anything
    beyond the end of the window are removed. The decision is made per
    folder from its own date, so gaps in the data never cause an
    out-of-range folder to be retained. Non-date folders are ignored.
    """

    if not DATA_DIR.exists():
        return

    start = today()
    end = start + timedelta(days=WINDOW_DAYS - 1)

    for child in sorted(DATA_DIR.iterdir()):

        if not child.is_dir():
            continue

        try:
            folder_date = datetime.strptime(child.name, DAY_FORMAT).date()
        except ValueError:
            # Not a dated folder; leave it alone.
            continue

        if start <= folder_date <= end:
            continue

        remove_dir(child)
        print(f"  Removed out-of-window folder -> {child}", flush=True)


# ==================================================
# 5. Single polling cycle
# ==================================================

def load_locations():
    """Return the list of dining locations from the static locations file.

    The locations rarely change, so they live in ``locations.json`` next to
    this script. They are still confirmed against the live API once per run
    (see ``refresh_locations``), but day-to-day the file is read, not fetched.
    Returns the ``locations`` list, or an empty list if the file is missing or
    malformed.
    """

    if not LOCATIONS_FILE.exists():
        print(
            f"  Static locations file not found at {LOCATIONS_FILE}.",
            file=sys.stderr,
            flush=True,
        )
        return []

    try:
        payload = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        print(
            f"  Could not read {LOCATIONS_FILE}: {error}.",
            file=sys.stderr,
            flush=True,
        )
        return []

    return payload.get("locations", [])


def _location_ids(payload):
    """Return the sorted set of location ids from a locations payload."""
    return sorted(
        str(loc.get("id"))
        for loc in (payload.get("locations", []) or [])
        if loc.get("id")
    )


def refresh_locations(api_key):
    """Confirm the stored locations against the live API; update if changed.

    Fetches the current locations and compares their ids to the stored
    ``locations.json``. When they differ (a hall was added or removed), the
    file is overwritten with the fresh payload and True is returned so the
    caller can regenerate all menus. When unchanged (or the request fails and
    we fall back to the stored copy), False is returned. A network failure is
    non-fatal: we keep using the stored locations.
    """
    try:
        live = fetch_json(LOCATIONS_URL, api_key)
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        print(
            f"  Could not confirm locations ({error}); using stored copy.",
            file=sys.stderr,
            flush=True,
        )
        return False

    live_ids = _location_ids(live)
    if not live_ids:
        print("  Live locations response was empty; using stored copy.", flush=True)
        return False

    stored_ids = _location_ids(
        {"locations": load_locations()}
    )

    if live_ids == stored_ids:
        print(f"  Locations unchanged ({', '.join(live_ids)}).", flush=True)
        return False

    # Locations changed: persist the fresh payload.
    LOCATIONS_FILE.write_text(
        json.dumps(live, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"  Locations changed: {stored_ids} -> {live_ids}. "
        f"Updated {LOCATIONS_FILE}.",
        flush=True,
    )
    return True


def regenerate_all_menus():
    """Delete every day folder so the window is re-polled from scratch."""
    if not DATA_DIR.exists():
        return
    for child in sorted(DATA_DIR.iterdir()):
        if child.is_dir():
            remove_dir(child)
    print("  Cleared all day folders for full regeneration.", flush=True)


def day_is_complete(day):
    """Return True when a day's folder has a menu file for every location.

    A day counts as complete only if it has a ``menu_<id>.json`` for each
    location in the static locations file. Partial days left behind by a
    failed fetch are reported incomplete so they get retried.
    """

    day_dir = dir_for_date(day)

    locations = load_locations()
    if not locations:
        # With no known locations we can't judge completeness; treat as not
        # complete so the day is attempted (and the missing file is surfaced).
        return False

    for location in locations:
        location_id = location.get("id")
        if not location_id:
            continue
        if not (day_dir / f"menu_{location_id}.json").exists():
            return False

    return True


def poll_day(api_key, day):
    """Fetch the locations and each location's menu for one date.

    Returns True when every menu was saved, False when at least one menu
    request failed (so the caller knows the day is only partially filled).
    """

    day_label = day.strftime(DAY_FORMAT)
    day_dir = dir_for_date(day)

    # Locations come from the static locations.json, not a daily request.
    locations = load_locations()

    print(
        f"[{timestamp_label()}] Polling {day_label}: "
        f"{len(locations)} location(s) from static locations.json...",
        flush=True,
    )

    if not locations:
        print(
            "  No locations available (missing/empty locations.json); "
            "skipping menu requests.",
            flush=True,
        )
        # Nothing to fetch and nothing to retry for this day.
        return True

    all_succeeded = True

    for location in locations:

        location_id = location.get("id")

        if not location_id:
            print("  Skipping a location with no id.", flush=True)
            continue

        base_menu_url = MENU_URL_TEMPLATE.format(location=location_id)
        menu_url = f"{base_menu_url}?day={day_label}"

        print(f"  Requesting menu for '{location_id}'...", flush=True)

        try:
            menu_payload = fetch_json(menu_url, api_key)
        except urllib.error.HTTPError as error:
            print(
                f"  Menu request for '{location_id}' failed: "
                f"HTTP {error.code} {error.reason}.",
                flush=True,
            )
            all_succeeded = False
            continue
        except urllib.error.URLError as error:
            print(
                f"  Menu request for '{location_id}' failed: {error.reason}.",
                flush=True,
            )
            all_succeeded = False
            continue

        path = write_snapshot(day_dir, f"menu_{location_id}", menu_payload)
        print(f"  Saved menu for '{location_id}' -> {path}", flush=True)

    return all_succeeded


# ==================================================
# 6. Entry point
# ==================================================

def main():
    """Confirm locations, then maintain the 8-day menu window.

    1. Confirm the stored locations against the live API. If they changed,
       regenerate every day's menus from scratch (so the new hall set is
       reflected everywhere).
    2. Ensure the 8-day window (today through today + 7) is present: poll
       any missing/incomplete day, leave complete days untouched.
    3. Prune day folders outside the window and add new in-range days.
    """

    try:
        api_key = load_api_key()
    except (FileNotFoundError, ValueError) as error:
        print(f"Startup error: {error}", file=sys.stderr)
        return 1

    print(f"[{timestamp_label()}] Confirming dining locations...", flush=True)
    locations_changed = refresh_locations(api_key)
    if locations_changed:
        regenerate_all_menus()

    dates = window_dates()

    print(
        f"[{timestamp_label()}] Window: "
        f"{dates[0].strftime(DAY_FORMAT)} .. {dates[-1].strftime(DAY_FORMAT)} "
        f"({WINDOW_DAYS} days). Output directory: {DATA_DIR}",
        flush=True,
    )

    for day in dates:

        day_label = day.strftime(DAY_FORMAT)

        # Skip only days that are already complete. A partially filled
        # day (e.g. locations saved but a menu request failed earlier)
        # is re-polled so the missing files can be filled in.
        if day_is_complete(day):
            print(f"  {day_label} already complete; skipping.", flush=True)
            continue

        try:
            complete = poll_day(api_key, day)
        except urllib.error.HTTPError as error:
            print(
                f"  {day_label} request failed: "
                f"HTTP {error.code} {error.reason}.",
                file=sys.stderr,
            )
            continue
        except urllib.error.URLError as error:
            print(
                f"  {day_label} request failed: {error.reason}.",
                file=sys.stderr,
            )
            continue

        if not complete:
            print(
                f"  {day_label} is incomplete; it will be retried "
                "on a future run.",
                flush=True,
            )

    # Preserve every complete day in the permanent archive BEFORE pruning, so
    # days that are about to roll out of the window are kept for the calendar.
    archive_completed_days()

    # Tidy up folders outside the forward window (past days and beyond).
    prune_out_of_window()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
