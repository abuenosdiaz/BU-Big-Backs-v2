// Client for the menu API served by backend/server.py (see backend/API.md).
// In dev, Vite proxies /api to the backend, so VITE_API_URL can stay empty.
const BASE = (import.meta.env["VITE_API_URL"] ?? "").replace(/\/+$/, "");

export type ApiHall = {
  location_id: string;
  location_name: string;
  campus: string;
};

export type MenuRow = {
  location_id: string;
  location_name: string;
  campus: string;
  date: string;
  meal: string;
  meal_opens_at: string | null;
  meal_closes_at: string | null;
  station: string;
  item_id: string;
  item_name: string;
  description: string | null;
  calories: number | null;
  carbohydrates_grams: number | null;
  protein_grams: number | null;
  saturated_fat_grams: number | null;
  vegetarian: boolean | null;
  vegan: boolean | null;
  gluten_free: boolean | null;
  halal: boolean | null;
  cool_food: boolean | null;
};

/** Allergen categories the API derives from each item's ingredient list. */
export type Allergen =
  | "dairy"
  | "egg"
  | "fish"
  | "shellfish"
  | "tree_nuts"
  | "peanut"
  | "wheat"
  | "gluten"
  | "soy"
  | "sesame"
  | "mustard"
  | "sulfites";

export type MenuFilters = {
  hall?: string;
  campus?: string;
  date?: string;
  meal?: string;
  station?: string;
  /** Comma-separated; items must match all of them */
  diet?: string;
  q?: string;
  /** Comma-separated; keeps items containing none of them (unknown items are dropped) */
  exclude_allergens?: string;
};

async function apiGet<T>(path: string, params: Record<string, string | undefined> = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter((e): e is [string, string] => !!e[1]),
  ).toString();
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error ?? `API request failed (${res.status})`);
  return body as T;
}

export const DIET_KEYS = ["vegetarian", "vegan", "gluten_free", "halal", "cool_food"] as const;
export type DietKey = (typeof DIET_KEYS)[number];

/** One row of dining_diet_analysis.build_hall_summary. */
export type HallStats = {
  dining_hall: string;
  campus: string;
  first_day: string;
  last_day: string;
  total_items: number;
  avg_calories: number;
  avg_carbohydrates_grams: number;
  avg_protein_grams: number;
  avg_saturated_fat_grams: number;
} & Record<`total_${DietKey}` | `percent_${DietKey}`, number>
  // change_* is null when there's no following-week data to compare against.
  & Record<`change_${DietKey}`, number | null>;

export type Stats = {
  /** Null when there is no data for the requested scope (e.g. an empty day). */
  overall: {
    total_items: number;
    halls: number;
    first_day: string;
    last_day: string;
    percent: Record<DietKey, number>;
    avg_nutrition: Record<"calories" | "carbohydrates_grams" | "protein_grams" | "saturated_fat_grams", number>;
  } | null;
  halls: HallStats[];
  /** The single day the stats were scoped to, or null for the full window. */
  scope_date: string | null;
  /**
   * The week-over-week comparison target when a single day is scoped: the same
   * weekday seven days later. `available` is false when that day has no data,
   * so per-hall `change_*` values come back null. Null for the full window.
   */
  next_week: { date: string | null; available: boolean } | null;
};

/** Stats for the whole window, or for one service day when `date` is given. */
export const getStats = (date?: string | null) =>
  apiGet<Stats>("/api/stats", date ? { date } : {});

export const getMetadata = () =>
  apiGet<{ halls: ApiHall[]; campuses: string[]; dates: string[]; meals: string[] }>("/api/metadata");

export const getMenu = (filters: MenuFilters = {}) =>
  apiGet<{ count: number; rows: MenuRow[] }>("/api/menu", filters);

/** An item's ingredient statement and the allergen categories found in it. 404 if unknown. */
export const getItemAllergens = (location: string, itemId: string) =>
  apiGet<{ ingredients: string | null; allergens: Allergen[] }>(
    `/api/items/${encodeURIComponent(location)}/${encodeURIComponent(itemId)}/allergens`,
  );

export const getItem = (itemId: string) =>
  apiGet<{ item_id: string; item_name: string; occurrences: MenuRow[] }>(
    `/api/items/${encodeURIComponent(itemId)}`,
  );
