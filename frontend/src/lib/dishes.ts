import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";
import { getMenu, getMetadata, type MenuRow } from "@/lib/api";
import { requireBuUser, toastWriteError, useAuth } from "@/lib/auth";
import aiPhotos from "@/lib/dish-images.json";
import { useSelectedDate } from "@/lib/menu-date";
import { toAllergens } from "@/lib/profile";

const images = import.meta.glob("@/assets/dishes/*.jpg", { eager: true, import: "default" }) as Record<
  string,
  string
>;

// The menu API has no photos, so a dish shows one of these photos only when the
// photo is really that dish. Dish names end with what the dish is ("Blueberry
// Pancake", "Chicken Noodle Soup"), so the words must end the name. No match, no photo.
const PHOTO_KEYWORDS: [photo: string, words: string][] = [
  ["west-mac", "mac (?:&|and) cheese|macaroni (?:&|and) cheese"],
  ["central-pizza", "pizza"],
  ["east-pancakes", "pancake"],
  ["central-poke", "poke bowl"],
  ["central-falafel", "falafel|falafel pita"],
  ["fenway-tacos", "taco"],
  ["east-burger", "burger|cheeseburger"],
  ["east-ramen", "ramen"],
  ["west-tofu", "sesame tofu|crispy tofu|tofu stir[- ]fry|tofu vegetable stir[- ]fry"],
  ["fenway-cookie", "cookie"],
  ["fenway-buddha", "buddha bowl|grain bowl"],
  ["west-chicken-bowl", "grilled chicken|chicken breast"],
];
const PHOTO_PATTERNS = PHOTO_KEYWORDS.map(
  ([photo, words]) => [photo, new RegExp(`\\b(?:${words})(?:e?s)?$`, "i")] as const,
);

/** File name for a dish's AI photo; must match slug() in scripts/generate-dish-images.mjs. */
function dishSlug(name: string) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

const AI_PHOTOS = new Set<string>(aiPhotos);

export function dishImage(name: string) {
  // AI photos made by `npm run images` win; otherwise fall back to a matching category photo.
  const s = dishSlug(name);
  if (AI_PHOTOS.has(s)) return `/dishes/ai/${s}.jpg`;
  const photo = PHOTO_PATTERNS.find(([, re]) => re.test(name.trim()))?.[0];
  const key = photo && Object.keys(images).find((k) => k.endsWith(`/${photo}.jpg`));
  return key ? images[key] : undefined;
}

export const CAMPUSES = ["West Campus", "Central Campus", "East Campus"] as const;

/** Meal names from the menu API, in serving order. */
export const MEALS = ["Breakfast", "Brunch", "Lunch", "Dinner"] as const;

export type Nutrition = {
  serving?: string;
  fat?: number;
  satFat?: number;
  chol?: number;
  sodium?: number;
  carbs?: number;
  fiber?: number;
  sugars?: number;
  protein?: number;
};

export type Dish = {
  id: string;
  /** location_id and item_id from the menu API, for /api/items lookups */
  locationId: string;
  itemId: string;
  name: string;
  campus: string;
  hall: string;
  station: string;
  calories: number;
  tags: string[];
  description: string;
  ingredients: string[];
  sort: number;
  /** First meal it's served at today */
  meal: string;
  /** Every meal it's served at today, in serving order */
  meals: string[];
  nutrition: Nutrition;
  loves: number;
  loved: boolean;
  comments: number;
};

const DIET_TAGS = [
  ["vegetarian", "Vegetarian"],
  ["vegan", "Vegan"],
  ["gluten_free", "Gluten-free"],
  ["halal", "Halal"],
] as const;

/** Today's date in Boston, as the API's YYYY-MM-DD service date. */
export function today() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

/**
 * Pick the service date to show.
 *
 * Priority:
 *   1. An explicit `override` the user picked in the header calendar, when that
 *      day is actually available.
 *   2. Today (Boston time), when the backend has it.
 *   3. Otherwise the most recent available day (the backend keeps a rolling
 *      window and the newest/edge days can be temporarily missing), falling
 *      back to the earliest upcoming day if all data is in the future.
 *
 * Returns `null` only when the backend has no dates at all. An override for a
 * day that is no longer available is ignored, so a stale saved choice can never
 * leave the app stuck on an empty page.
 */
function resolveMenuDate(available: string[], override?: string | null): string | null {
  if (override && available.includes(override)) return override;
  const t = today();
  if (available.includes(t)) return t;
  if (available.length === 0) return null;
  // Dates are YYYY-MM-DD, so lexical max is the latest available day.
  const past = available.filter((d) => d <= t);
  // Prefer the most recent day on or before today; otherwise the earliest
  // upcoming day. Either way we land on a day the backend can actually serve.
  return (past.length ? past : available).reduce((a, b) => (a > b ? a : b));
}

// An item can be served at several halls, so the dish id includes the hall.
function toDish(r: MenuRow, sort: number): Omit<Dish, "loves" | "loved" | "comments"> {
  return {
    id: `${r.location_id}-${r.item_id}`,
    locationId: r.location_id,
    itemId: r.item_id,
    name: r.item_name,
    campus: r.campus,
    hall: r.location_name,
    station: r.station,
    calories: r.calories ?? 0,
    tags: DIET_TAGS.filter(([k]) => r[k]).map(([, label]) => label),
    description: r.description && r.description !== "null" ? r.description : "",
    ingredients: [],
    sort,
    meal: r.meal,
    meals: [r.meal],
    nutrition: Object.fromEntries(
      [
        ["carbs", r.carbohydrates_grams],
        ["protein", r.protein_grams],
        ["satFat", r.saturated_fat_grams],
      ].filter(([, v]) => v != null),
    ) as Nutrition,
  };
}

/**
 * Today's dishes. `avoid` takes profile "avoid" choices (e.g. "Dairy"); the API drops
 * dishes whose ingredients contain them, and dishes with unknown ingredients.
 */
/**
 * The service date currently being shown, plus everything the header's date
 * picker needs: the full list of available days, the resolved day, whether it
 * is today, and whether the user has an explicit override active.
 *
 * `date` resolves to the same day `useDishes` requests: the user's picked day
 * when available, else today, else the most recent available day. Returns
 * `null` for the date only when the backend has no menu data at all.
 */
export function useMenuDate() {
  const override = useSelectedDate();
  return useQuery({
    queryKey: ["menu-date", override],
    queryFn: async () => {
      const meta = await getMetadata();
      const available = (meta.dates ?? []).slice().sort();
      const date = resolveMenuDate(available, override);
      return {
        date,
        available,
        isToday: date === today(),
        isOverride: !!override && available.includes(override),
      };
    },
  });
}

export function useDishes(avoid: string[] = []) {
  // Keyed by user so "loved" refreshes on sign-in and sign-out.
  const user = useAuth();
  const override = useSelectedDate();
  const exclude = [...new Set(toAllergens(avoid))].sort().join(",");
  return useQuery({
    // Keyed by the selected day so switching days refetches the right menu.
    queryKey: ["dishes", user?.id ?? null, exclude, override],
    queryFn: async () => {
      // Resolve which day to show: the user's picked day when available, else
      // today, else the latest day the backend has. This keeps the menu
      // populated even when the current date's folder is missing.
      const meta = await getMetadata();
      const date = resolveMenuDate((meta.dates ?? []).slice().sort(), override);
      const [menu, stats] = await Promise.all([
        getMenu({ ...(date ? { date } : {}), ...(exclude && { exclude_allergens: exclude }) }),
        supabase.rpc("get_dish_stats"),
      ]);
      // Votes are optional: still show the menu if Supabase is unavailable.
      if (stats.error) console.warn("Couldn't load dish votes:", stats.error.message);
      const votes = new Map((stats.data ?? []).map((s) => [s.dish_id, s]));
      const dishes = new Map<string, Dish>();
      menu.rows.forEach((r, i) => {
        const d = toDish(r, i);
        const seen = dishes.get(d.id);
        if (seen) {
          if (!seen.meals.includes(r.meal)) seen.meals.push(r.meal);
          return;
        }
        const v = votes.get(d.id);
        dishes.set(d.id, {
          ...d,
          loves: Number(v?.loves ?? 0),
          loved: !!v?.loved,
          comments: Number(v?.comments ?? 0),
        });
      });
      return [...dishes.values()];
    },
  });
}

export function useToggleLove() {
  const user = useAuth();
  const qc = useQueryClient();
  const key = ["dishes", user?.id ?? null];
  return useMutation({
    mutationFn: async (dishId: string) => {
      await requireBuUser();
      const { error } = await supabase.rpc("toggle_love", { p_dish: dishId });
      if (error) throw error;
    },
    onMutate: async (dishId) => {
      if (!user) return;
      // Update every cached dish list for this user (one per allergen filter) optimistically.
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueriesData<Dish[]>({ queryKey: key });
      qc.setQueriesData<Dish[]>({ queryKey: key }, (old) =>
        old?.map((d) =>
          d.id === dishId ? { ...d, loved: !d.loved, loves: d.loves + (d.loved ? -1 : 1) } : d,
        ),
      );
      return { prev };
    },
    onError: (e, _id, ctx) => {
      ctx?.prev.forEach(([k, data]) => qc.setQueryData(k, data));
      toastWriteError(e, "Couldn't save your vote. Try again.");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["dishes"] }),
  });
}

export function rankByCampus(dishes: Dish[], campus: string) {
  return dishes
    .filter((d) => d.campus === campus)
    .sort((a, b) => b.loves - a.loves || a.sort - b.sort);
}

export function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
