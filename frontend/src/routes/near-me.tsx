import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { MapPin } from "lucide-react";
import { PageShell } from "@/components/SiteHeader";
import { CampusMap } from "@/components/CampusMap";
import { dishImage, useDishes } from "@/lib/dishes";
import { HALLS, currentMeal, distanceKm, mealForHall, walkMinutes } from "@/lib/halls";
import { fitsProfile, useProfile } from "@/lib/profile";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/near-me")({
  head: () => ({
    meta: [
      { title: "Near me — BU Big Backs" },
      { name: "description", content: "See what's being served at the BU dining halls closest to you, matched to your restrictions." },
      { property: "og:title", content: "Near me — BU Big Backs" },
      { property: "og:description", content: "What's being served closest to you on campus right now." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: NearMe,
});

// Campus-center fallback (Marsh Plaza / Marsh Chapel) when the user's real
// location is unavailable. Verified against OpenStreetMap.
const FALLBACK = { lat: 42.3507, lng: -71.1064 };

function NearMe() {
  const { profile } = useProfile();
  const [pos, setPos] = useState<{ lat: number; lng: number } | null>(null);
  const [status, setStatus] = useState<"off" | "asking" | "on" | "denied">("off");
  const [limit, setLimit] = useState(10);
  const [onlyFits, setOnlyFits] = useState(true);
  const [selectedHall, setSelectedHall] = useState<string | null>(null);
  const hallRefs = useRef<Record<string, HTMLLIElement | null>>({});
  const { data } = useDishes(onlyFits ? profile.avoid : []);

  useEffect(() => setLimit(profile.walkLimit), [profile.walkLimit]);

  const locate = () => {
    if (!navigator.geolocation) return setStatus("denied");
    setStatus("asking");
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setPos({ lat: p.coords.latitude, lng: p.coords.longitude });
        setStatus("on");
      },
      () => setStatus("denied"),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 },
    );
  };

  useEffect(() => {
    if (profile.useLocation) locate();
  }, [profile.useLocation]);

  // Clicking a hall on the map scrolls its list card into view and highlights it.
  const handleSelectHall = (hallId: string) => {
    setSelectedHall(hallId);
    hallRefs.current[hallId]?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const here = pos ?? FALLBACK;
  const located = status === "on" && pos != null;
  const meal = currentMeal();
  const rows = useMemo(
    () =>
      HALLS.map((h) => {
        const km = distanceKm(here.lat, here.lng, h.lat, h.lng);
        // Per-hall status: the meal it's serving now, or null when closed
        // (outside its own opening/closing hours in Boston time).
        const hallMeal = mealForHall(h);
        const top = (data ?? [])
          .filter((d) => d.hall === h.name && (!onlyFits || fitsProfile(d, profile)))
          .sort((a, b) => Number(hallMeal != null && b.meals.includes(hallMeal)) - Number(hallMeal != null && a.meals.includes(hallMeal)) || b.loves - a.loves)[0];
        return { h, km, min: walkMinutes(km), top, hallMeal };
      }).sort((a, b) => a.km - b.km),
    [data, here.lat, here.lng, onlyFits, profile, meal],
  );
  const shown = rows.filter((r) => r.min <= limit || limit >= 60);

  return (
    <PageShell>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="page-title">Near me</h1>
          <p className="mt-2 text-muted-foreground">See what's being served closest to you right now, matched to your restrictions.</p>
        </div>
        <button onClick={locate} className={cn("btn-primary", status === "on" && "btn-primary")}>
          <MapPin className="h-4 w-4" />
          {status === "on" ? "Location on" : status === "asking" ? "Finding you…" : "Use my location"}
        </button>
      </div>
      {status === "denied" && (
        <p className="mt-3 text-sm text-muted-foreground">We couldn't get your location, so the map is centered on campus and distances are measured from Marsh Plaza.</p>
      )}
      {located && (
        <p className="mt-3 text-sm text-muted-foreground">Showing your real location. Distances are straight-line to each hall.</p>
      )}

      <div className="mt-8 grid gap-8 lg:grid-cols-[1.2fr_1fr]">
        <div className="min-h-[360px] overflow-hidden rounded-2xl border border-border">
          <CampusMap
            halls={HALLS}
            here={here}
            located={located}
            onSelectHall={handleSelectHall}
          />
        </div>

        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-display text-xl font-bold">Closest to you</h2>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="field w-auto py-1.5">
              <option value={5}>Within 5 min walk</option>
              <option value={10}>Within 10 min walk</option>
              <option value={20}>Within 20 min walk</option>
              <option value={60}>Any distance</option>
            </select>
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={onlyFits} onChange={(e) => setOnlyFits(e.target.checked)} className="accent-primary" />
            Only show what fits my restrictions
          </label>
          <ul className="mt-4 space-y-3">
            {shown.length === 0 && (
              <p className="text-sm text-muted-foreground">No dining halls within that walk. Try a longer distance.</p>
            )}
            {shown.map(({ h, km, min, top, hallMeal }) => (
              <li
                key={h.name}
                ref={(el) => {
                  hallRefs.current[h.id] = el;
                }}
                className={cn(
                  "panel flex items-center gap-4 transition-shadow",
                  selectedHall === h.id && "ring-2 ring-primary",
                )}
              >
                {top && dishImage(top.name) && (
                  <img src={dishImage(top.name)} alt="" width={72} height={72} loading="lazy" className="h-16 w-16 rounded-lg object-cover" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <strong>{h.name}</strong>
                    {hallMeal ? (
                      <span className="diet-tag">Serving {hallMeal}</span>
                    ) : (
                      <span className="diet-tag border-destructive/40 bg-destructive/10 text-destructive">
                        Closed
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {km.toFixed(1)} km · {min} min walk · {h.campus} · Open {h.hours}
                  </p>
                  {!hallMeal ? (
                    <p className="mt-1 text-sm text-muted-foreground">Closed right now — opens {h.hours.split("–")[0]?.trim() ?? ""}.</p>
                  ) : top ? (
                    <Link to="/dish/$id" params={{ id: top.id }} className="mt-1 block text-sm">
                      <span className="text-muted-foreground">Most loved right now: </span>
                      <span className="font-semibold text-primary">{top.name}</span>
                    </Link>
                  ) : (
                    <p className="mt-1 text-sm text-muted-foreground">Nothing that fits your restrictions here.</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </PageShell>
  );
}
