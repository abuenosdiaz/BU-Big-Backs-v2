export type Hall = {
  /** location_id from the menu API */
  id: string;
  /** location_name from the menu API */
  name: string;
  short: string;
  campus: string;
  /** Real-world coordinates of the dining hall's building (WGS84). */
  lat: number;
  lng: number;
  /** Street address, shown in the map popup. */
  address: string;
  hours: string;
};

// The halls served by GET /api/halls; names and campuses must match the API.
// Coordinates are the real building locations (geocoded from each hall's BU
// street address via OpenStreetMap/Nominatim), so they plot correctly on the
// interactive map.
export const HALLS: Hall[] = [
  {
    id: "west",
    name: "The Fresh Food Co. at West Campus",
    short: "West Campus",
    campus: "West Campus",
    lat: 42.3529,
    lng: -71.1205,
    address: "275 Babcock St, Boston, MA 02215",
    hours: "7am – 11pm",
  },
  {
    id: "marciano",
    name: "The Fresh Food Co. at Marciano Commons",
    short: "Marciano Commons",
    campus: "East Campus",
    lat: 42.3498,
    lng: -71.0979,
    address: "100 Bay State Rd, Boston, MA 02215",
    hours: "7am – 10pm",
  },
  {
    id: "warren",
    name: "The Fresh Food Co. at Warren Towers",
    short: "Warren Towers",
    campus: "Central Campus",
    lat: 42.3492,
    lng: -71.1031,
    address: "700 Commonwealth Ave, Boston, MA 02215",
    hours: "7am – 11pm",
  },
];

export const FORUM_HALLS = HALLS.map((h) => h.short);

export function distanceKm(aLat: number, aLng: number, bLat: number, bLng: number) {
  const R = 6371;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLng = ((bLng - aLng) * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((aLat * Math.PI) / 180) * Math.cos((bLat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** ~5 km/h walking pace */
export function walkMinutes(km: number) {
  return Math.max(1, Math.round((km / 5) * 60));
}

/** Current hour and weekday in Boston, regardless of the viewer's timezone. */
function bostonNow(d = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    hour12: false,
  }).formatToParts(d);
  const hourStr = parts.find((p) => p.type === "hour")?.value ?? "0";
  const weekdayStr = parts.find((p) => p.type === "weekday")?.value ?? "Sun";
  // Intl can emit "24" at midnight; normalise to 0.
  const hour = Number(hourStr) % 24;
  const weekdayIndex = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(weekdayStr);
  return { hour, weekday: weekdayIndex };
}

/** Parse a hall's closing hour from its "7am – 11pm" hours string (24h). */
function closingHour(hours: string): number {
  // Take the part after the dash, e.g. "11pm".
  const end = hours.split("–")[1]?.trim() ?? hours.split("-")[1]?.trim() ?? "";
  const m = end.match(/(\d{1,2})\s*(am|pm)/i);
  if (!m || m[1] == null || m[2] == null) return 23; // default if format is unexpected
  let h = Number(m[1]) % 12;
  if (m[2].toLowerCase() === "pm") h += 12;
  return h;
}

/** Dining halls open at 7am (10am for Sunday brunch). */
function openingHour(weekday: number): number {
  return weekday === 0 ? 10 : 7;
}

/**
 * Which meal a hall is serving right now, or `null` when it is closed.
 *
 * Respects Boston time and the hall's own opening/closing hours (from its
 * `hours` string), so late at night — when every hall is closed — this returns
 * `null` instead of defaulting to "Dinner". Meal boundaries follow the menu
 * API: Sunday is Brunch until 4pm then Dinner; other days Breakfast until 11am,
 * Lunch until 5pm, then Dinner.
 */
export function mealForHall(hall: Hall, d = new Date()): string | null {
  const { hour, weekday } = bostonNow(d);
  if (hour < openingHour(weekday) || hour >= closingHour(hall.hours)) return null;
  if (weekday === 0) return hour < 16 ? "Brunch" : "Dinner";
  if (hour < 11) return "Breakfast";
  if (hour < 17) return "Lunch";
  return "Dinner";
}

/**
 * The current meal period in Boston without a hall-specific close time, used
 * for ranking "what's being served now". Returns `null` before the earliest
 * opening; a hall's own closed state is decided by `mealForHall`.
 */
export function currentMeal(d = new Date()): string | null {
  const { hour, weekday } = bostonNow(d);
  if (hour < openingHour(weekday)) return null;
  if (weekday === 0) return hour < 16 ? "Brunch" : "Dinner";
  if (hour < 11) return "Breakfast";
  if (hour < 17) return "Lunch";
  return "Dinner";
}
