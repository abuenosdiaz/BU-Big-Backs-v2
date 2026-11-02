"""
Turn a Boston University dining-menu JSON payload into a tidy Pandas DataFrame.

The BU dining site returns a JSON document shaped like:

    {
        "location": {...},
        "date": "2026-10-24",
        "schedules": [{"meal": ..., "opens_at": ..., "closes_at": ...}, ...],
        "meals": [
            {
                "name": "Breakfast",
                "stations": [
                    {"name": "Bakery", "items": [ {item}, {item}, ... ]},
                    ...
                ],
                "item_count": 35
            },
            ...
        ],
        "open_now": true,
        ...
    }

Each food item lives under meals[].stations[].items[]. This module flattens
that nesting into one row per (meal, station, item) and carries the meal and
station names onto every row, along with the location/date context and the
meal service hours from "schedules".

How to use
----------
1. Set JSON_PATH below to point at your saved menu JSON file.
2. Run the script:  python marciano_json_to_df.py

You can also import the helpers in other code:

    from marciano_json_to_df import menu_json_to_df, load_menu_df

    df = menu_json_to_df(data)              # from an already-parsed dict
    df = load_menu_df("some_menu.json")     # from a file path
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


# ======================================================================
# USER SETTING: specify the path to your menu JSON file here.
#
# By default this resolves to the most recent dated snapshot folder under
# ``data/`` that contains a ``menu_marciano.json`` (the poller keeps a
# rolling window of day-folders and prunes old ones, so a hardcoded date
# would go stale). To parse a specific file instead, set JSON_PATH to a
# plain file name (if it sits next to this script) or a full path such as
# r"C:\Users\you\Downloads\marciano.json".
# ======================================================================
DATA_DIR = Path(__file__).resolve().parent / "data"
MENU_FILENAME = "menu_marciano.json"


def _latest_menu_path() -> Path:
    """Return the newest dated ``data/`` folder that has the marciano menu.

    Day folders are named ``YYYY-MM-DD``, so a reverse lexicographic sort
    puts the most recent date first. Falls back to a plain path under the
    newest-looking folder when nothing matches, so the error message in
    ``main`` still points somewhere sensible.
    """
    if DATA_DIR.exists():
        candidates = sorted(
            (child for child in DATA_DIR.iterdir() if child.is_dir()),
            key=lambda child: child.name,
            reverse=True,
        )
        for child in candidates:
            menu_file = child / MENU_FILENAME
            if menu_file.exists():
                return menu_file

    # Nothing found; return a best-effort path for a helpful error message.
    return DATA_DIR / MENU_FILENAME


JSON_PATH = str(_latest_menu_path())

# Optional: set to a file name to also write the flattened rows as CSV,
# or leave as None to skip writing a CSV.
OUTPUT_CSV_PATH = None


# ----------------------------------------------------------------------
# Column schema
# ----------------------------------------------------------------------
# Per-item fields come straight from each item dict. Context fields are
# copied down from the meal, station, location, date and schedule.

ITEM_FIELDS = [
    "id",
    "name",
    "description",
    "calories",
    "carbohydrates_grams",
    "protein_grams",
    "saturated_fat_grams",
    "vegetarian",
    "vegan",
    "gluten_free",
    "halal",
    "cool_food",
]

NUMERIC_COLUMNS = [
    "calories",
    "carbohydrates_grams",
    "protein_grams",
    "saturated_fat_grams",
]

BOOLEAN_COLUMNS = [
    "vegetarian",
    "vegan",
    "gluten_free",
    "halal",
    "cool_food",
]

# Text columns where an absent value should read as the literal string
# "null" rather than NaN. A float NaN is meaningless for a text field and
# shows up confusingly in the table and in exported CSVs, so missing text
# (most commonly a missing item description) is filled with "null".
STRING_MISSING_FILL = "null"
STRING_COLUMNS = [
    "description",
    "item_id",
    "item_name",
]

# Final column order for the flattened DataFrame.
COLUMNS = [
    # context: where/when
    "location_id",
    "location_name",
    "campus",
    "date",
    "meal",
    "meal_opens_at",
    "meal_closes_at",
    "station",
    # the food item itself
    "item_id",
    "item_name",
    "description",
    "calories",
    "carbohydrates_grams",
    "protein_grams",
    "saturated_fat_grams",
    "vegetarian",
    "vegan",
    "gluten_free",
    "halal",
    "cool_food",
]


def _schedule_lookup(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Map each meal name to its opening and closing time.

    The "schedules" block lists service hours per meal. Matching is done
    case-insensitively so "Breakfast" in schedules lines up with "Breakfast"
    in meals regardless of casing differences.
    """
    lookup: dict[str, dict[str, Any]] = {}
    for schedule in data.get("schedules") or []:
        meal_name = schedule.get("meal")
        if meal_name is None:
            continue
        lookup[str(meal_name).casefold()] = {
            "opens_at": schedule.get("opens_at"),
            "closes_at": schedule.get("closes_at"),
        }
    return lookup


def menu_json_to_df(data: Mapping[str, Any]) -> pd.DataFrame:
    """Flatten a BU dining-menu JSON payload into a per-item DataFrame.

    Parameters
    ----------
    data:
        The parsed JSON document (a dict), or a dict already containing the
        "meals" list. If given the top-level document, location/date/schedule
        context is included; if the structure is missing those keys, the
        corresponding columns are filled with NaN.

    Returns
    -------
    pandas.DataFrame
        One row per food item. Columns follow ``COLUMNS``.
    """
    if not isinstance(data, Mapping):
        raise TypeError(
            "menu_json_to_df expects a mapping (parsed JSON object), "
            f"got {type(data).__name__}."
        )

    location = data.get("location") or {}
    date = data.get("date")
    schedules = _schedule_lookup(data)

    location_id = location.get("id")
    location_name = location.get("name")
    campus = location.get("campus")

    meals: Sequence[Mapping[str, Any]] = data.get("meals") or []

    rows: list[dict[str, Any]] = []

    for meal in meals:
        meal_name = meal.get("name")
        schedule = schedules.get(str(meal_name).casefold(), {})
        meal_opens_at = schedule.get("opens_at")
        meal_closes_at = schedule.get("closes_at")

        for station in meal.get("stations") or []:
            station_name = station.get("name")

            for item in station.get("items") or []:
                row = {
                    "location_id": location_id,
                    "location_name": location_name,
                    "campus": campus,
                    "date": date,
                    "meal": meal_name,
                    "meal_opens_at": meal_opens_at,
                    "meal_closes_at": meal_closes_at,
                    "station": station_name,
                    "item_id": item.get("id"),
                    "item_name": item.get("name"),
                    "description": item.get("description"),
                    "calories": item.get("calories"),
                    "carbohydrates_grams": item.get("carbohydrates_grams"),
                    "protein_grams": item.get("protein_grams"),
                    "saturated_fat_grams": item.get("saturated_fat_grams"),
                    "vegetarian": item.get("vegetarian"),
                    "vegan": item.get("vegan"),
                    "gluten_free": item.get("gluten_free"),
                    "halal": item.get("halal"),
                    "cool_food": item.get("cool_food"),
                }
                rows.append(row)

    df = pd.DataFrame(rows, columns=COLUMNS)

    # Enforce dtypes so downstream analysis is reliable even when the JSON
    # has missing or inconsistent values.
    for column in NUMERIC_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in BOOLEAN_COLUMNS:
        # Preserve genuine missing values as <NA> rather than coercing to
        # False, using pandas' nullable boolean dtype.
        df[column] = df[column].astype("boolean")

    # Fill missing text with the literal "null" instead of NaN. JSON nulls and
    # empty strings both count as missing here. Numeric and boolean columns are
    # left untouched so their math/aggregations still treat gaps as missing.
    for column in STRING_COLUMNS:
        df[column] = (
            df[column]
            .where(df[column].notna(), other=STRING_MISSING_FILL)
            .replace("", STRING_MISSING_FILL)
            .astype(str)
        )

    # Order rows by meal (Breakfast, Lunch, Dinner), then station, then name.
    meal_order = {"Breakfast": 0, "Brunch": 1, "Lunch": 2, "Dinner": 3}
    df["_meal_order"] = df["meal"].map(meal_order)
    df = (
        df.sort_values(
            ["_meal_order", "station", "item_name"],
            na_position="last",
            kind="stable",
        )
        .drop(columns="_meal_order")
        .reset_index(drop=True)
    )

    return df


def load_menu_df(path: str | Path) -> pd.DataFrame:
    """Read a JSON file from ``path`` and return the flattened DataFrame."""
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    return menu_json_to_df(data)


def main() -> pd.DataFrame:
    """Load the file at JSON_PATH, print a summary, and optionally write CSV."""
    json_file = Path(JSON_PATH)
    if not json_file.exists():
        raise FileNotFoundError(
            f"Could not find the JSON file at {json_file.resolve()}.\n"
            "Edit the JSON_PATH setting near the top of this script so it "
            "points to your saved menu JSON file."
        )

    df = load_menu_df(json_file)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", None)

    print(f"Parsed {len(df)} food-item rows from {json_file.resolve()}.\n")
    print("Rows per meal:")
    print(df["meal"].value_counts(dropna=False).to_string())
    print("\nRows per station:")
    print(df["station"].value_counts(dropna=False).to_string())
    print("\nPreview:")
    print(df.head(15).to_string(index=False))

    if OUTPUT_CSV_PATH:
        df.to_csv(OUTPUT_CSV_PATH, index=False, na_rep="")
        print(f"\nWrote CSV to {Path(OUTPUT_CSV_PATH).resolve()}")

    return df


if __name__ == "__main__":
    main()
