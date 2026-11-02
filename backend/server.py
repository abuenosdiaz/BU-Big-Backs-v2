"""BU Big Backs backend API server.

A standalone Python backend for the dining service. It does two things:

    1. Runs a daily job that executes the dining poller (``dining_poller``):
       it confirms the dining locations against the live API (regenerating all
       menus if they changed), maintains the 7-day menu window in
       ``backend/data``, and then continues compiling ``backend/items.json`` --
       a continuously growing database of per-item detail. The first run
       happens on startup; subsequent runs fire once a day.

    2. Serves a holistic JSON API built on the analysis modules in this
       directory (``collect_menu_dfs`` -> ``marciano_json_to_df`` and
       ``dining_diet_analysis``). The frontend is hosted separately and calls
       this API cross-origin, so every response carries permissive CORS
       headers and OPTIONS preflight is handled.

The server itself depends only on the Python standard library, so it always
boots. The analysis layer pulls in pandas on demand; if it is missing the data
endpoints return a clear 503 while /api/health still answers.

API overview
------------
    GET /api/health                 service + poller + data status
    GET /api/locations              raw dining locations (static file)
    GET /api/metadata               all filter options in one call: halls,
                                    campuses, dates, meals, stations, diets
    GET /api/menu                   filtered menu rows; ?group=true nests them
                                    by hall -> meal -> station
    GET /api/items/{item_id}        every occurrence of one item (menu data)
    GET /api/items/{location}/{item_id}
                                    full upstream detail (ingredients,
                                    nutrition); ?allergens=true folds in
                                    allergen categories
    GET /api/items/{location}/{item_id}/allergens
                                    allergen categories for one item, derived
                                    from its ingredients (allergen.json)
    GET /api/stats                  overall rollup + per-hall dietary &
                                    nutrition summary

Common query parameters for /api/menu:
    hall / location_name, campus, date, meal, station   exact(ish) filters
    diet = vegetarian|vegan|gluten_free|halal|cool_food dietary flags; comma-
                                                        separated requires all
    allergens = dairy,fish,...                          keep items containing ANY
    exclude_allergens = dairy,peanut,...                keep items containing NONE
    include_unknown = true                              keep items with no
                                                        ingredient data (excl. mode)
    q                                                   item-name search
    group = true                                        nest by hall/meal/station
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ==================================================
# 1. Paths and configuration
# ==================================================

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"

# Permanent, never-pruned archive of past days (see dining_poller.ARCHIVE_DIR).
# The API serves days from here as well as the live window, so the calendar can
# reach back through the full history. Overridable with ARCHIVE_DIR.
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", str(BACKEND_DIR / "data_archive")))

# Static list of dining locations. The set of halls rarely changes, so it is
# kept as a constant file here instead of being polled daily.
LOCATIONS_FILE = BACKEND_DIR / "locations.json"

# Dietary flags exposed across the menu and stats endpoints.
DIET_FLAGS = ["vegetarian", "vegan", "gluten_free", "halal", "cool_food"]

# Global, continuously growing database of individual item details. Unlike the
# per-day menu snapshots in DATA_DIR (which are windowed and pruned), this file
# only ever grows: an item is added the first time it is requested or compiled
# and is never removed. Each entry records when it was first fetched.
ITEMS_CACHE_FILE = BACKEND_DIR / "items.json"

# The analysis modules live right here in the backend directory; make them
# importable no matter where the server is launched from.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Where this API server binds.
HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("SERVER_PORT", "8000"))

# Daily poller schedule. By default the poller runs once on startup and then
# every day at POLL_HOUR_UTC:POLL_MINUTE_UTC. Set POLL_INTERVAL_SECONDS to a
# positive value to use a plain fixed interval instead of a wall-clock time.
POLL_HOUR_UTC = int(os.environ.get("POLL_HOUR_UTC", "3"))
POLL_MINUTE_UTC = int(os.environ.get("POLL_MINUTE_UTC", "0"))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "0"))

# How long a built DataFrame is reused before being rebuilt from disk, in
# seconds. Keeps repeated API calls fast without going stale for long.
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

# Minimum gap between consecutive *upstream* item-detail requests, in seconds.
# Each uncached item requires its own call to BU's API, so when many new items
# are requested at once the calls are serialised and spaced out by at least
# this interval to avoid being rate-limited. Cache hits are never throttled.
UPSTREAM_MIN_INTERVAL = float(os.environ.get("UPSTREAM_MIN_INTERVAL", "0.5"))

# Hard ceiling on how long a single request will wait for its turn in the
# upstream throttle queue before giving up with 503, in seconds. Protects
# against unbounded queue growth during a large burst.
UPSTREAM_MAX_WAIT = float(os.environ.get("UPSTREAM_MAX_WAIT", "20"))

# Permissive CORS by default since the frontend is a separate origin. Narrow
# this via ALLOWED_ORIGIN (e.g. "https://app.example.com") in production.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")


# ==================================================
# 2. Poller scheduler (daily job)
# ==================================================

class PollerState:
    """Tracks the most recent dining-poller run for the health endpoint."""

    def __init__(self):
        self._lock = threading.Lock()
        self.last_run_at = None
        self.last_status = None  # "ok", "error", or None before the first run
        self.last_detail = None
        self.next_run_at = None

    def record(self, status, detail):
        with self._lock:
            self.last_run_at = datetime.now(timezone.utc).isoformat()
            self.last_status = status
            self.last_detail = detail

    def set_next(self, when):
        with self._lock:
            self.next_run_at = when.isoformat() if when else None

    def snapshot(self):
        with self._lock:
            return {
                "last_run_at": self.last_run_at,
                "last_status": self.last_status,
                "last_detail": self.last_detail,
                "next_run_at": self.next_run_at,
                "schedule": (
                    f"every {POLL_INTERVAL_SECONDS}s"
                    if POLL_INTERVAL_SECONDS > 0
                    else f"daily at {POLL_HOUR_UTC:02d}:{POLL_MINUTE_UTC:02d} UTC"
                ),
            }


POLLER_STATE = PollerState()


def run_poller_once(stop_event=None):
    """Run the dining poller once, then continue compiling the items database.

    The poller maintains the 7-day menu window (and regenerates it when the
    dining locations change). Afterwards we invalidate the analysis cache and
    compile any menu items still missing from items.json, so the database keeps
    growing toward full coverage across runs.
    """
    try:
        import dining_poller
    except Exception as exc:
        POLLER_STATE.record("error", f"Could not import dining_poller: {exc}")
        print(f"[poller] import failed: {exc}", file=sys.stderr, flush=True)
        return

    print(f"[poller] Starting run at {datetime.now(timezone.utc).isoformat()}", flush=True)
    poll_ok = False
    try:
        exit_code = dining_poller.main()
        if exit_code == 0:
            poll_ok = True
            POLLER_STATE.record("ok", "Poller completed successfully.")
            print("[poller] Run finished (ok).", flush=True)
        else:
            detail = f"Poller returned non-zero exit code {exit_code}."
            POLLER_STATE.record("error", detail)
            print(f"[poller] {detail}", file=sys.stderr, flush=True)
    except Exception as exc:
        POLLER_STATE.record("error", f"Poller raised: {exc}")
        print(f"[poller] Run failed: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
    finally:
        CACHE.invalidate()

    # Keep compiling the items database with any newly available menu items.
    # Only attempt it when the poll succeeded, so we work against fresh menus.
    if poll_ok:
        try:
            compile_items(stop_event=stop_event)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[compile] failed: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc()


def _seconds_until_next_daily_run(now=None):
    """Return (seconds, when) until the next POLL_HOUR_UTC:POLL_MINUTE_UTC."""
    now = now or datetime.now(timezone.utc)
    target = now.replace(
        hour=POLL_HOUR_UTC, minute=POLL_MINUTE_UTC, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds(), target


def poller_loop(stop_event):
    """Run the poller on startup, then on the configured daily schedule."""
    # Always refresh immediately on boot so the API has data to serve.
    run_poller_once(stop_event)

    while not stop_event.is_set():
        if POLL_INTERVAL_SECONDS > 0:
            delay = POLL_INTERVAL_SECONDS
            when = datetime.now(timezone.utc) + timedelta(seconds=delay)
        else:
            delay, when = _seconds_until_next_daily_run()

        POLLER_STATE.set_next(when)
        print(
            f"[poller] Next run at {when.isoformat()} "
            f"(in {int(delay)}s).",
            flush=True,
        )

        # Sleep until the next run, waking early if asked to stop.
        if stop_event.wait(delay):
            break
        run_poller_once(stop_event)


def start_poller_thread():
    """Launch the poller loop on a daemon thread and return a stop handle."""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=poller_loop,
        args=(stop_event,),
        name="dining-poller",
        daemon=True,
    )
    thread.start()
    return stop_event


# ==================================================
# 3. Analysis data layer (cached)
# ==================================================

class BackendUnavailable(RuntimeError):
    """Raised when the analysis backend cannot be imported or run."""


class DataCache:
    """Caches the combined menu DataFrame to avoid rebuilding it per request."""

    def __init__(self, ttl_seconds):
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._df = None
        self._built_at = 0.0

    def invalidate(self):
        with self._lock:
            self._df = None
            self._built_at = 0.0

    def get(self):
        """Return the cached DataFrame, rebuilding it when stale or empty."""
        with self._lock:
            fresh = (
                self._df is not None
                and (time.monotonic() - self._built_at) < self._ttl
            )
            if fresh:
                return self._df

            df = _build_items()
            self._df = df
            self._built_at = time.monotonic()
            return df


def _build_items():
    """Build the combined menu DataFrame from ``backend/data``.

    Imports are lazy so the server still boots (and /api/health answers) even
    when pandas or the analysis modules are unavailable. The data root is
    pinned to ``backend/data`` rather than the modules' hard-coded script
    defaults.
    """
    try:
        import pandas as pd
        from collect_menu_dfs import collect_menu_dataframes
    except Exception as exc:  # ImportError, or pandas import failure
        raise BackendUnavailable(
            "Could not import the analysis backend. Ensure its dependencies "
            f"(e.g. pandas) are installed. Original error: {exc}"
        ) from exc

    # Read both the permanent archive (full history) and the live rolling
    # window. Either may be absent; we fail only when neither has data.
    frames = []
    for root in (ARCHIVE_DIR, DATA_DIR):
        if root.exists():
            part = collect_menu_dataframes(root)
            if not part.empty:
                frames.append(part)

    if not frames:
        raise BackendUnavailable(
            f"No menu data found at {DATA_DIR} (or archive {ARCHIVE_DIR}) yet. "
            "The poller runs on startup; wait for its first run to finish, "
            "then retry."
        )

    combined = pd.concat(frames, ignore_index=True)

    # The same day can appear in both the archive and the live window; keep one
    # row per (date, hall, item, meal, station) so days aren't double-counted.
    key_cols = [c for c in ("date", "location_id", "item_id", "meal", "station")
                if c in combined.columns]
    if key_cols:
        combined = combined.drop_duplicates(subset=key_cols).reset_index(drop=True)

    return combined


CACHE = DataCache(CACHE_TTL_SECONDS)


def _records(df):
    """Convert a DataFrame to JSON-ready records (NaN/NaT -> null)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _csv_params(params, name):
    """Return a lowercased list from a comma-separated query param."""
    raw = params.get(name)
    if not raw:
        return []
    return [p.strip().lower() for p in str(raw).split(",") if p.strip()]


def _apply_filters(items, params):
    """Apply the shared query filters to the menu DataFrame.

    Supports exact-match (case-insensitive) on hall/campus/date/meal/station,
    one or more dietary flags (``diet=vegan,halal`` requires all), an item-name
    search ``q``, and allergen filters that join against the items database:

        allergens=fish,shellfish          keep items containing ANY of these
        exclude_allergens=dairy,peanut    keep items containing NONE of these
        include_unknown=true              keep items with no ingredient data
                                          (default: excluded when excluding
                                          allergens, included otherwise)
    """
    df = items

    # Column filters. ``hall`` is an alias for ``location_name``.
    exact = {
        "location_name": params.get("hall") or params.get("location_name"),
        "campus": params.get("campus"),
        "date": params.get("date"),
        "meal": params.get("meal"),
        "station": params.get("station"),
    }
    for column, value in exact.items():
        if value and column in df.columns:
            df = df[df[column].astype(str).str.casefold() == str(value).casefold()]

    # Dietary flags: accept a comma-separated list; all listed flags must hold.
    for flag in _csv_params(params, "diet"):
        if flag in set(DIET_FLAGS) and flag in df.columns:
            df = df[df[flag].fillna(False).astype(bool)]

    query = params.get("q")
    if query and "item_name" in df.columns:
        df = df[df["item_name"].astype(str).str.casefold().str.contains(
            query.casefold(), na=False
        )]

    # Allergen filters, joined against the items database.
    want = set(_csv_params(params, "allergens"))
    avoid = set(_csv_params(params, "exclude_allergens"))
    if want or avoid:
        df = _filter_by_allergens(df, want, avoid, params)

    return df


def _filter_by_allergens(df, want, avoid, params):
    """Keep rows by their derived allergen categories (from the items DB).

    ``want``: keep rows whose allergens intersect this set (ANY match).
    ``avoid``: keep rows whose allergens are disjoint from this set (NONE).
    Rows whose item is not yet in the items database have unknown allergens:
    for ``avoid`` they are dropped unless ``include_unknown=true`` (safer for
    an allergy filter); for ``want`` they are always dropped (can't confirm).
    """
    if "item_id" not in df.columns or "location_id" not in df.columns:
        return df

    index = allergen_index()
    include_unknown = str(params.get("include_unknown", "")).lower() in (
        "1", "true", "yes"
    )

    def keep(row):
        key = f"{row['location_id']}/{row['item_id']}"
        known = key in index
        cats = index.get(key, set())
        if want:
            if not known or not (cats & want):
                return False
        if avoid:
            if not known:
                return include_unknown
            if cats & avoid:
                return False
        return True

    mask = df.apply(keep, axis=1)
    return df[mask]


# ---- endpoint data builders ----

def data_menu(params):
    """Filtered menu. Flat rows by default; nested when ``group=true``.

    The grouped shape nests hall -> meal -> station -> items, which is handy
    for rendering a menu board. Both shapes honour the same filters.
    """
    items = CACHE.get()
    grouped = str(params.get("group", "")).lower() in ("1", "true", "yes")

    if items.empty:
        return {"halls": []} if grouped else {"count": 0, "rows": []}

    df = _apply_filters(items, params)

    if not grouped:
        rows = _records(df)
        return {"count": len(rows), "rows": rows}

    halls = []
    for hall_name, hall_df in df.groupby("location_name", sort=True):
        meals = []
        for meal_name, meal_df in hall_df.groupby("meal", sort=False):
            stations = []
            for station_name, station_df in meal_df.groupby("station", sort=True):
                stations.append({
                    "station": station_name,
                    "items": _records(station_df),
                })
            meals.append({
                "meal": meal_name,
                "opens_at": _first_or_none(meal_df, "meal_opens_at"),
                "closes_at": _first_or_none(meal_df, "meal_closes_at"),
                "stations": stations,
            })
        halls.append({
            "hall": hall_name,
            "campus": _first_or_none(hall_df, "campus"),
            "meals": meals,
        })
    return {"halls": halls}


def data_item(item_id):
    items = CACHE.get()
    if items.empty or "item_id" not in items.columns:
        return None
    matches = items[items["item_id"].astype(str) == str(item_id)]
    if matches.empty:
        return None
    rows = _records(matches)
    first = rows[0]
    return {
        "item_id": item_id,
        "item_name": first.get("item_name"),
        "occurrences": rows,
    }


def data_locations():
    """Serve the static locations file (the full upstream locations payload)."""
    if not LOCATIONS_FILE.exists():
        raise BackendUnavailable(
            f"Static locations file not found at {LOCATIONS_FILE}."
        )
    try:
        return json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise BackendUnavailable(f"Could not read locations file: {exc}") from exc


def data_metadata():
    """All filter dimensions in one call: halls, campuses, dates, meals,

    stations, and the supported dietary flags. This is everything a client
    needs to build menu filters without issuing several tiny requests.
    """
    items = CACHE.get()
    if items.empty:
        return {
            "halls": [], "campuses": [], "dates": [],
            "meals": [], "stations": [], "diets": DIET_FLAGS,
        }

    def uniq(col):
        if col not in items.columns:
            return []
        return sorted({v for v in items[col].dropna().astype(str) if v})

    meal_order = {"Breakfast": 0, "Brunch": 1, "Lunch": 2, "Dinner": 3}
    hall_cols = [c for c in ("location_id", "location_name", "campus")
                 if c in items.columns]
    halls = _records(
        items[hall_cols].drop_duplicates().sort_values("location_name")
    ) if hall_cols else []

    return {
        "halls": halls,
        "campuses": uniq("campus"),
        "dates": uniq("date"),
        "meals": sorted(uniq("meal"), key=lambda m: meal_order.get(m, 99)),
        "stations": uniq("station"),
        "diets": DIET_FLAGS,
    }


def data_stats(params=None):
    """Unified statistics: an overall rollup plus the per-hall summary.

    ``overall`` aggregates across every hall (totals, dietary percentages, mean
    nutrition, date span). ``halls`` is the authoritative per-hall breakdown
    from ``dining_diet_analysis.build_hall_summary`` (average nutrition,
    dietary totals/percentages, and first-day vs last-day change). One endpoint
    covers both the headline numbers and the per-hall detail.

    When a ``date`` query parameter is supplied, the stats are scoped to just
    that service day (case-insensitive exact match on the ``date`` column), so
    the UI's day picker can show single-day stats. Without it, the stats span
    every day in the data, as before. ``scope_date`` in the response echoes the
    day actually applied (or ``None`` for the full window).
    """
    from dining_diet_analysis import build_hall_summary
    from datetime import date as _date, timedelta as _timedelta

    params = params or {}
    scope_date = params.get("date")

    all_items = CACHE.get()
    if all_items.empty:
        return {"overall": None, "halls": [], "scope_date": None, "next_week": None}

    # ``next_week`` describes the week-over-week comparison when a single day is
    # selected: the same weekday seven days later. It is None for the full-window
    # view. ``available`` is False when that following-week day has no data, so
    # the UI can say "No data is present for the following week".
    next_week = None

    items = all_items
    # Scope to a single service day when requested.
    if scope_date and "date" in items.columns:
        items = items[
            items["date"].astype(str).str.casefold() == str(scope_date).casefold()
        ]
        if items.empty:
            # The requested day has no data; report an empty-but-valid payload
            # rather than erroring, so the client can show "no data" cleanly.
            return {"overall": None, "halls": [], "scope_date": scope_date, "next_week": None}

        # Determine the same weekday one week later for the change comparison.
        try:
            nxt = (_date.fromisoformat(str(scope_date)) + _timedelta(days=7)).isoformat()
        except ValueError:
            nxt = None
        next_rows = (
            all_items[all_items["date"].astype(str) == nxt]
            if nxt and "date" in all_items.columns
            else all_items.iloc[0:0]
        )
        next_week = {"date": nxt, "available": not next_rows.empty}

    summary = build_hall_summary(items)
    halls = _records(summary)

    # For a single selected day, recompute each hall's ``change_*`` as the
    # week-over-week difference: (count on scope_date + 7 days) - (count on
    # scope_date). build_hall_summary's own change is first-day vs last-day,
    # which is always 0 within a single day, so we override it here.
    if scope_date:
        def _counts_for(rows):
            out = {}
            for hall, g in rows.groupby("location_name", sort=False):
                out[str(hall)] = {flag: int(g[flag].fillna(False).astype(bool).sum())
                                  for flag in DIET_FLAGS if flag in g.columns}
            return out

        this_counts = _counts_for(items)
        next_counts = _counts_for(next_rows) if (next_week and next_week["available"]) else {}
        for hall in halls:
            name = hall.get("dining_hall")
            tc = this_counts.get(name, {})
            nc = next_counts.get(name, {})
            for flag in DIET_FLAGS:
                if next_week and next_week["available"]:
                    hall[f"change_{flag}"] = nc.get(flag, 0) - tc.get(flag, 0)
                else:
                    # No following-week data: change is not meaningful.
                    hall[f"change_{flag}"] = None

    # Overall rollup computed directly from the item frame.
    import pandas as pd

    df = items.copy()
    for flag in DIET_FLAGS:
        if flag in df.columns:
            df[flag] = df[flag].fillna(False).astype(bool)
    for col in ("calories", "carbohydrates_grams", "protein_grams",
                "saturated_fat_grams"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    total = len(df)
    dates = df["date"].dropna().astype(str) if "date" in df.columns else []
    overall = {
        "total_items": int(total),
        "halls": int(df["location_name"].nunique()) if "location_name" in df.columns else 0,
        "first_day": min(dates) if len(dates) else None,
        "last_day": max(dates) if len(dates) else None,
        "percent": {
            flag: (round(100 * float(df[flag].mean()), 2) if flag in df.columns and total else None)
            for flag in DIET_FLAGS
        },
        "avg_nutrition": {
            "calories": _safe_mean(df, "calories"),
            "carbohydrates_grams": _safe_mean(df, "carbohydrates_grams"),
            "protein_grams": _safe_mean(df, "protein_grams"),
            "saturated_fat_grams": _safe_mean(df, "saturated_fat_grams"),
        },
    }

    return {"overall": overall, "halls": halls, "scope_date": scope_date, "next_week": next_week}


def _first_or_none(df, column):
    if column not in df.columns:
        return None
    vals = df[column].dropna()
    return None if vals.empty else vals.iloc[0]


def _safe_mean(df, column):
    if column not in df.columns:
        return None
    val = df[column].mean()
    return None if val != val else round(float(val), 2)  # NaN check


# ==================================================
# 3b. Individual item detail (live upstream lookup + database)
# ==================================================
#
# The menu list only carries a few fields per item, but BU's per-item endpoint
# returns richer detail (full ingredient statement, location block). This
# fetches that detail on demand and stores it in a single, continuously
# growing ``backend/items.json`` database: an item is added the first time it
# is requested (lazy) or compiled, and is never removed. The database maps a
# composite key -> timestamped entry:
#
#     { "<location>/<item_id>": {"fetched_at": "<iso>", "item": {...}}, ... }

class ItemLookupError(RuntimeError):
    """Base error for upstream item-detail lookups."""


class ItemNotFound(ItemLookupError):
    """The upstream API has no such item for this location/day (404)."""


class UpstreamUnavailable(ItemLookupError):
    """The upstream API could not be reached or returned an error."""


class UpstreamBusy(ItemLookupError):
    """Too many queued upstream requests; this one waited too long."""


class UpstreamThrottle:
    """Serialises upstream calls and spaces them out to avoid rate limits.

    Only one thread holds the throttle at a time. On release the next thread
    is admitted no sooner than ``min_interval`` seconds after the previous
    upstream call started, so a burst of concurrent requests for different
    items is drip-fed to the upstream API instead of hitting it all at once.

    A thread gives up with ``UpstreamBusy`` if it cannot get its turn within
    ``max_wait`` seconds, bounding worst-case latency during large bursts.
    """

    def __init__(self, min_interval, max_wait):
        self._min_interval = min_interval
        self._max_wait = max_wait
        self._lock = threading.Lock()
        self._next_allowed = 0.0  # monotonic time the next call may start

    def __enter__(self):
        deadline = time.monotonic() + self._max_wait
        # Serialise: one caller proceeds at a time.
        while True:
            if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                raise UpstreamBusy(
                    "Upstream request queue is busy; please retry shortly."
                )
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait <= 0:
                # Our turn: reserve the next slot and keep the lock while the
                # caller performs the upstream request.
                self._next_allowed = now + self._min_interval
                return self
            # Not yet time for the next call. Release the lock so we don't
            # hold it while sleeping, wait out the gap (or the deadline),
            # then try again.
            self._lock.release()
            if now + wait > deadline:
                raise UpstreamBusy(
                    "Upstream request queue is busy; please retry shortly."
                )
            time.sleep(wait)

    def __exit__(self, *exc):
        self._lock.release()
        return False


_UPSTREAM_THROTTLE = UpstreamThrottle(UPSTREAM_MIN_INTERVAL, UPSTREAM_MAX_WAIT)

# Serialises writes to the items database across request threads.
_ITEMS_CACHE_LOCK = threading.Lock()


def _item_detail_url(location, item_id):
    from dining_poller import BASE_URL
    return f"{BASE_URL}/locations/{location}/menu/items/{item_id}"


def _read_items_cache():
    """Return the items database as a dict, or {} if missing/corrupt.

    Shape: ``{ "<location>/<item_id>": {"fetched_at": <iso>, "item": {...}}, ... }``
    This is a continuously growing store: entries are added as items are
    requested or compiled and are never pruned.
    """
    if not ITEMS_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(ITEMS_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable: treat as empty rather than failing.
        return {}


def _write_items_cache(data):
    """Write the items database atomically-ish (temp file then replace)."""
    ITEMS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ITEMS_CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ITEMS_CACHE_FILE)


def _cache_entry(payload):
    """Wrap an upstream item payload with a fetch timestamp for the database."""
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "item": payload,
    }


def _fetch_item_upstream(location, item_id):
    """Perform the throttled upstream request for one item's detail.

    Raises ``ItemNotFound`` (404), ``UpstreamUnavailable`` (other failures), or
    ``UpstreamBusy`` (throttle queue saturated). Returns the raw item payload.
    """
    try:
        from dining_poller import fetch_json, load_api_key
    except Exception as exc:
        raise UpstreamUnavailable(
            f"Could not import the poller's fetch helpers: {exc}"
        ) from exc

    try:
        api_key = load_api_key()
    except (FileNotFoundError, ValueError) as exc:
        raise UpstreamUnavailable(f"API key unavailable: {exc}") from exc

    url = _item_detail_url(location, item_id)
    try:
        return fetch_json(url, api_key)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ItemNotFound(
                f"No item '{item_id}' at location '{location}' for today."
            ) from exc
        raise UpstreamUnavailable(
            f"Upstream returned HTTP {exc.code} {exc.reason}."
        ) from exc
    except urllib.error.URLError as exc:
        raise UpstreamUnavailable(f"Upstream request failed: {exc.reason}.") from exc


def _store_item(key, payload):
    """Add/refresh one item in the items database (never prunes)."""
    with _ITEMS_CACHE_LOCK:
        data = _read_items_cache()
        data[key] = _cache_entry(payload)
        _write_items_cache(data)


def get_item_detail(location, item_id, refresh=False):
    """Return detailed info for one item, using the global items cache.

    Looks in the long-lived ``items.json`` database first (unless ``refresh``
    is set), then falls back to a throttled upstream fetch and stores the
    result with a timestamp. The database is never pruned -- it only grows.
    Raises ``ItemNotFound`` for a 404 and ``UpstreamUnavailable`` otherwise.
    """
    key = f"{location}/{item_id}"

    # Fast path: serve from cache without touching the throttle or upstream.
    if not refresh:
        entry = _read_items_cache().get(key)
        if isinstance(entry, dict) and "item" in entry:
            return {"item": entry["item"], "cached": True,
                    "cache_file": str(ITEMS_CACHE_FILE)}

    # Slow path: an upstream call is needed. Pass through the throttle so a
    # burst of new-item requests is staggered rather than fired all at once.
    with _UPSTREAM_THROTTLE:
        # Re-check the cache now that it is our turn. During a burst for the
        # *same* item, an earlier request may have already fetched and cached
        # it while we waited -- in which case we skip the redundant call.
        if not refresh:
            entry = _read_items_cache().get(key)
            if isinstance(entry, dict) and "item" in entry:
                return {"item": entry["item"], "cached": True,
                        "cache_file": str(ITEMS_CACHE_FILE)}

        payload = _fetch_item_upstream(location, item_id)
        _store_item(key, payload)

    return {"item": payload, "cached": False, "cache_file": str(ITEMS_CACHE_FILE)}


# ==================================================
# 3c. Allergen detection from ingredients
# ==================================================
#
# ``allergen.json`` maps ingredient keywords (lowercase) to a major allergen
# category. For a given item we take its ``ingredients`` string, match the
# keywords on word boundaries (so "eggplant" does not match "egg"), and report
# the distinct categories. Keywords are intentionally matched as whole words
# to avoid false positives while still catching every known allergen term.

ALLERGEN_MAP_PATH = BACKEND_DIR / "allergen.json"


def _load_allergen_map():
    """Load the ingredient-keyword -> allergen-category map from disk.

    Comment keys (starting with ``_``) are ignored. Returns a dict of
    lowercase keyword -> category. Raises BackendUnavailable if the file is
    missing or malformed so the endpoint reports a clear error.
    """
    try:
        raw = json.loads(ALLERGEN_MAP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackendUnavailable(
            f"Allergen map not found at {ALLERGEN_MAP_PATH}."
        ) from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise BackendUnavailable(f"Could not read allergen map: {exc}") from exc

    return {
        str(k).lower(): str(v)
        for k, v in raw.items()
        if not str(k).startswith("_")
    }


# Compiled once: keyword -> (category, boundary-matching regex).
_ALLERGEN_RULES = None
_ALLERGEN_RULES_LOCK = threading.Lock()

# Cached "<location>/<item_id>" -> {categories}, rebuilt when items.json changes.
_ALLERGEN_INDEX = None
_ALLERGEN_INDEX_MTIME = None
_ALLERGEN_INDEX_LOCK = threading.Lock()


def _allergen_rules():
    import re

    global _ALLERGEN_RULES
    with _ALLERGEN_RULES_LOCK:
        if _ALLERGEN_RULES is None:
            rules = []
            for keyword, category in _load_allergen_map().items():
                pattern = re.compile(
                    r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])"
                )
                rules.append((keyword, category, pattern))
            _ALLERGEN_RULES = rules
        return _ALLERGEN_RULES


def detect_allergens(ingredients):
    """Return the distinct allergen categories implied by an ingredient string.

    Returns a sorted list of unique categories and the matched keywords (for
    transparency). ``milk`` and ``cheese`` both map to ``dairy``, so ``dairy``
    appears only once.
    """
    text = (ingredients or "").lower()
    categories = {}
    for keyword, category, pattern in _allergen_rules():
        if pattern.search(text):
            categories.setdefault(category, set()).add(keyword)

    return {
        "allergens": sorted(categories),
        "matched_keywords": {c: sorted(kws) for c, kws in categories.items()},
    }


def allergen_index():
    """Map ``"<location>/<item_id>"`` -> set of allergen categories.

    Built from the items database by classifying each item's ingredients.
    Cached and rebuilt only when items.json changes (keyed on its mtime), so
    menu filtering by allergen stays cheap across requests.
    """
    global _ALLERGEN_INDEX, _ALLERGEN_INDEX_MTIME
    with _ALLERGEN_INDEX_LOCK:
        try:
            mtime = ITEMS_CACHE_FILE.stat().st_mtime
        except OSError:
            mtime = 0.0
        if _ALLERGEN_INDEX is not None and mtime == _ALLERGEN_INDEX_MTIME:
            return _ALLERGEN_INDEX

        index = {}
        for key, entry in _read_items_cache().items():
            item = entry.get("item", {}) if isinstance(entry, dict) else {}
            cats = detect_allergens(item.get("ingredients"))["allergens"]
            index[key] = set(cats)

        _ALLERGEN_INDEX = index
        _ALLERGEN_INDEX_MTIME = mtime
        return index


def get_item_allergens(location, item_id, refresh=False):
    """Fetch an item's detail and return its allergen categories.

    Reuses the cached item-detail lookup, then classifies the item's
    ``ingredients`` against the allergen map. ``allergy_notice`` is
    deliberately not used -- it is a generic disclaimer, not a list of
    allergens.
    """
    detail = get_item_detail(location, item_id, refresh=refresh)
    item = detail["item"]
    ingredients = item.get("ingredients")
    result = detect_allergens(ingredients)

    return {
        "item_id": item.get("id", item_id),
        "name": item.get("name"),
        "location": location,
        "ingredients": ingredients,
        "allergens": result["allergens"],
        "matched_keywords": result["matched_keywords"],
    }


# ==================================================
# 4. Request handler
# ==================================================

class ApiHandler(BaseHTTPRequestHandler):
    """Serve the JSON API under /api with CORS."""

    server_version = "BUBigBacksAPI/1.0"

    # ---- response helpers ----

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_busy(self, message):
        """503 with a Retry-After hint when the upstream throttle is saturated."""
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Suggest the client back off roughly one throttle interval.
        self.send_header("Retry-After", str(max(1, round(UPSTREAM_MIN_INTERVAL))))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    # ---- routing ----

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/api":
            self._send_json(self._index())
            return
        if parsed.path.startswith("/api/"):
            self._handle_api(parsed)
            return
        self._send_error_json(404, "Not found.")

    def do_POST(self):
        self._send_error_json(405, "Method not allowed. This API is read-only.")

    # ---- API ----

    def _handle_api(self, parsed):
        route = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

        try:
            if route == "/api/health":
                self._send_json(self._health())
                return
            if route == "/api/locations":
                self._send_json(data_locations())
                return
            if route == "/api/metadata":
                self._send_json(data_metadata())
                return
            if route == "/api/menu":
                self._send_json(data_menu(params))
                return
            if route.startswith("/api/items/"):
                self._handle_items(route, params)
                return
            if route == "/api/stats":
                self._send_json(data_stats(params))
                return

            self._send_error_json(404, f"Unknown API route: {route}")

        except BackendUnavailable as exc:
            self._send_error_json(503, str(exc))
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive catch-all
            self._send_error_json(500, f"Unexpected error: {exc}")

    def _handle_items(self, route, params):
        """Route the two item endpoints that share the /api/items/ prefix.

            /api/items/{item_id}              search already-polled menu data
            /api/items/{location}/{item_id}   live upstream detail + per-day cache
        """
        from urllib.parse import unquote

        tail = route[len("/api/items/"):]
        segments = [unquote(s) for s in tail.split("/") if s]

        if len(segments) == 1:
            # Search the combined menu data for every occurrence of the item.
            item_id = segments[0]
            result = data_item(item_id)
            if result is None:
                self._send_error_json(404, f"No item with id '{item_id}'.")
            else:
                self._send_json(result)
            return

        refresh = params.get("refresh", "").lower() in ("1", "true", "yes")

        if len(segments) == 2:
            # Live per-item lookup against the upstream dining API. With
            # ?allergens=true the allergen categories are folded into the same
            # response so clients can get detail + allergens in one call.
            location, item_id = segments
            want_allergens = str(params.get("allergens", "")).lower() in (
                "1", "true", "yes"
            )
            try:
                detail = get_item_detail(location, item_id, refresh=refresh)
                if want_allergens:
                    ingredients = detail["item"].get("ingredients")
                    detail["allergens"] = detect_allergens(ingredients)["allergens"]
                self._send_json(detail)
            except ItemNotFound as exc:
                self._send_error_json(404, str(exc))
            except UpstreamBusy as exc:
                self._send_busy(str(exc))
            except UpstreamUnavailable as exc:
                self._send_error_json(502, str(exc))
            return

        if len(segments) == 3 and segments[2] == "allergens":
            # Allergen categories for one item, derived from its ingredients.
            location, item_id, _ = segments
            try:
                self._send_json(get_item_allergens(location, item_id, refresh=refresh))
            except ItemNotFound as exc:
                self._send_error_json(404, str(exc))
            except UpstreamBusy as exc:
                self._send_busy(str(exc))
            except UpstreamUnavailable as exc:
                self._send_error_json(502, str(exc))
            except BackendUnavailable as exc:
                self._send_error_json(503, str(exc))
            return

        self._send_error_json(
            404,
            "Use /api/items/{item_id}, /api/items/{location}/{item_id}, or "
            "/api/items/{location}/{item_id}/allergens.",
        )

    def _index(self):
        return {
            "service": "BU Big Backs API",
            "version": self.server_version,
            "endpoints": {
                "/api/health": "service, poller and data status",
                "/api/locations": "raw dining locations (static file)",
                "/api/metadata": "all filter options: halls, campuses, dates, meals, stations, diets",
                "/api/menu": "filtered menu rows; add ?group=true to nest by hall/meal/station",
                "/api/items/{item_id}": "occurrences of one item across the menus",
                "/api/items/{location}/{item_id}": "full item detail (ingredients, nutrition); ?allergens=true to include allergens",
                "/api/items/{location}/{item_id}/allergens": "allergen categories for one item",
                "/api/stats": "overall rollup + per-hall dietary & nutrition summary",
            },
        }

    def _health(self):
        backend_ok = True
        backend_detail = "ready"
        item_count = None
        try:
            df = CACHE.get()
            item_count = int(len(df))
        except BackendUnavailable as exc:
            backend_ok = False
            backend_detail = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            backend_ok = False
            backend_detail = f"error: {exc}"

        return {
            "status": "ok",
            "backend": {
                "available": backend_ok,
                "detail": backend_detail,
                "data_dir": str(DATA_DIR),
                "item_count": item_count,
            },
            "poller": POLLER_STATE.snapshot(),
        }

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[server] {self.address_string()} - {fmt % args}\n")


# ==================================================
# 5. Precompile and entry point
# ==================================================

def _iter_menu_items():
    """Yield unique (location, item_id) pairs from TODAY's menu snapshots.

    The upstream item-detail endpoint only serves items on the current day, so
    precompiling other days' items would just 404. We read today's menu files
    and de-duplicate by (location, item_id) so each distinct item is visited
    once.
    """
    from dining_poller import DAY_FORMAT, today

    seen = set()
    if not DATA_DIR.exists():
        return
    day_label = today().strftime(DAY_FORMAT)
    for menu_file in sorted((DATA_DIR / day_label).glob("menu_*.json")):
        try:
            data = json.loads(menu_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        location = (data.get("location") or {}).get("id")
        if not location:
            continue
        for meal in data.get("meals", []) or []:
            for station in meal.get("stations", []) or []:
                for item in station.get("items", []) or []:
                    item_id = item.get("id")
                    if not item_id:
                        continue
                    pair = (location, item_id)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    yield pair


def _missing_menu_items():
    """Return the unique (location, item_id) pairs not yet in the database.

    These are today's menu items that have never been fetched -- i.e. the work
    remaining to keep items.json in sync with the current menus.
    """
    have = set(_read_items_cache().keys())
    missing = []
    for location, item_id in _iter_menu_items():
        if f"{location}/{item_id}" not in have:
            missing.append((location, item_id))
    return missing


def compile_items(stop_event=None):
    """Fill the items database with any of today's menu items not yet present.

    The database grows continuously: already-present items are left untouched
    and only the missing ones are fetched, through the throttled path so the
    upstream API is not hammered. Safe to call repeatedly -- it simply resumes
    from wherever the database currently is. Returns a summary dict.

    ``stop_event`` (optional) lets a caller interrupt a long run at shutdown.
    """
    pairs = _missing_menu_items()
    total = len(pairs)
    print(
        f"[compile] {total} menu item(s) missing from the database "
        f"({len(_read_items_cache())} already present).",
        flush=True,
    )

    fetched = not_found = errors = 0
    for i, (location, item_id) in enumerate(pairs, start=1):
        if stop_event is not None and stop_event.is_set():
            print("[compile] Interrupted; will resume on the next run.", flush=True)
            break
        key = f"{location}/{item_id}"
        try:
            result = get_item_detail(location, item_id)
            if not result["cached"]:
                fetched += 1
        except ItemNotFound:
            not_found += 1
        except (UpstreamUnavailable, UpstreamBusy) as exc:
            errors += 1
            print(f"[compile] {key}: {exc}", file=sys.stderr, flush=True)
        if i % 50 == 0:
            print(
                f"[compile] {i}/{total} "
                f"(fetched={fetched}, not_found={not_found}, errors={errors})",
                flush=True,
            )

    summary = {
        "missing_before": total,
        "fetched": fetched,
        "not_found": not_found,
        "errors": errors,
        "total_in_db": len(_read_items_cache()),
    }
    print(
        f"[compile] Done. fetched={fetched}, not_found={not_found}, "
        f"errors={errors}. Database now holds {summary['total_in_db']} items "
        f"({ITEMS_CACHE_FILE}).",
        flush=True,
    )
    return summary


def precompile_items():
    """CLI entry point: compile the items database once, then exit."""
    compile_items()
    return 0


def main():
    # Kick off the daily poller (runs now, then on schedule).
    stop_event = start_poller_thread()

    httpd = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    schedule = (
        f"every {POLL_INTERVAL_SECONDS}s"
        if POLL_INTERVAL_SECONDS > 0
        else f"daily at {POLL_HOUR_UTC:02d}:{POLL_MINUTE_UTC:02d} UTC"
    )
    print(
        f"[server] BU Big Backs API listening on http://{HOST}:{PORT}\n"
        f"[server]   Endpoints -> GET /api (index of routes)\n"
        f"[server]   Poller    -> {schedule}, data in {DATA_DIR}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] Shutting down.", flush=True)
    finally:
        stop_event.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    # ``python server.py precompile`` warms backend/items.json from the menus;
    # with no argument it runs the API server.
    if len(sys.argv) > 1 and sys.argv[1] == "precompile":
        raise SystemExit(precompile_items())
    raise SystemExit(main())
