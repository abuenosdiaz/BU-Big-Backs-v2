import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Lock, Settings } from "lucide-react";
import { PageShell } from "@/components/SiteHeader";
import { dishImage, timeAgo, useDishes } from "@/lib/dishes";
import { HALLS, currentMeal } from "@/lib/halls";
import { fitsProfile, useProfile } from "@/lib/profile";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/profile")({
  head: () => ({
    meta: [
      { title: "My profile — BU Big Backs" },
      { name: "description", content: "Your dietary restrictions, starred dishes and picks for today." },
      { property: "og:title", content: "My profile — BU Big Backs" },
      { property: "og:description", content: "Your dietary restrictions, starred dishes and picks for today." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ProfilePage,
});

function ProfilePage() {
  const { profile } = useProfile();
  const { data } = useDishes();
  // "For you" also leaves out dishes with the profile's allergens (filtered by the API).
  const { data: safe } = useDishes(profile.avoid);
  const [tab, setTab] = useState<"starred" | "disliked">("starred");
  const byId = new Map((data ?? []).map((d) => [d.id, d]));
  const forYou = (safe ?? [])
    .filter((d) => fitsProfile(d, profile) && !profile.disliked.includes(d.id))
    .sort((a, b) => Number(profile.starred.includes(b.id)) - Number(profile.starred.includes(a.id)) || b.loves - a.loves)
    .slice(0, 3);
  const list = (tab === "starred" ? profile.starred : profile.disliked).map((id) => byId.get(id)).filter(Boolean);
  const restrictions = [...profile.diets, ...profile.avoid.map((a) => `No ${a.toLowerCase()}`)];
  // Empty string when nothing is being served now (before open), so the
  // "being served now" check simply reads false rather than matching a meal.
  const meal = currentMeal() ?? "";
  const initial = (profile.name || "?").trim()[0]?.toUpperCase();

  return (
    <PageShell>
      <div className="panel flex flex-wrap items-center gap-4">
        <span className="grid h-14 w-14 place-items-center rounded-full bg-primary font-display text-xl font-bold text-primary-foreground">
          {initial}
        </span>
        <div className="flex-1">
          <h1 className="font-display text-3xl font-black tracking-tight">{profile.name || "Set your name"}</h1>
          <p className="text-sm text-muted-foreground">
            {profile.year ? `Class of ${profile.year} · ` : ""}Usually eats at {profile.homeHall}
          </p>
          <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            <Lock className="h-3 w-3" /> Only you see this. Comments and forum posts always show as Anonymous.
          </p>
        </div>
        <Link to="/settings" className="btn-outline">
          <Settings className="h-4 w-4" /> Settings
        </Link>
      </div>

      <section className="mt-10">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-xl font-bold">My dietary restrictions</h2>
          <Link to="/settings" className="text-sm text-primary">
            Edit
          </Link>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {restrictions.length ? (
            restrictions.map((r) => (
              <span key={r} className="diet-tag text-sm">
                {r}
              </span>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">None set yet.</span>
          )}
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          {profile.hideMismatch
            ? "Dishes that don't fit these are hidden across Big Backs. You can turn this off in Settings."
            : "Dishes that don't fit are still shown. You can hide them in Settings."}
        </p>
      </section>

      <section className="mt-10">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-xl font-bold">For you today</h2>
          <Link to="/" className="text-sm text-primary">
            See all matches
          </Link>
        </div>
        <div className="mt-4 grid gap-5 sm:grid-cols-3">
          {forYou.map((d) => (
            <Link key={d.id} to="/dish/$id" params={{ id: d.id }} className="dish-card">
              <img src={dishImage(d.name)} alt={d.name} loading="lazy" width={944} height={704} className="aspect-[4/3] w-full rounded-xl object-cover" />
              <p className="mt-2 font-semibold">{d.name}</p>
              <p className="text-xs text-muted-foreground">
                {HALLS.find((h) => h.name === d.hall)?.short ?? d.hall} ·{" "}
                {d.meals.includes(meal) ? "Being served now" : `Served at ${d.meals.join(", ").toLowerCase()}`}
              </p>
            </Link>
          ))}
        </div>
      </section>

      <div className="mt-10 grid gap-8 md:grid-cols-2">
        <section>
          <div className="flex gap-4 border-b border-border">
            {(["starred", "disliked"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={cn("tab", tab === t && "tab-active")}>
                {t === "starred" ? "Starred" : "Disliked"}
              </button>
            ))}
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            {tab === "starred" ? "We'll let you know when a starred dish is on the menu." : "These are hidden from your feed."}
          </p>
          <ul className="mt-3 space-y-2">
            {list.length === 0 && <li className="text-sm text-muted-foreground">Nothing here yet — use the buttons on a dish page.</li>}
            {list.map((d) => (
              <li key={d!.id}>
                <Link to="/dish/$id" params={{ id: d!.id }} className="flex items-center gap-3 rounded-lg p-2 hover:bg-secondary">
                  <img src={dishImage(d!.name)} alt="" width={48} height={48} className="h-12 w-12 rounded-lg object-cover" />
                  <div>
                    <p className="font-semibold">{d!.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {d!.hall} · {d!.meals.includes(meal) ? "Being served now" : `Served at ${d!.meals.join(", ").toLowerCase()}`}
                    </p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel h-fit">
          <h2 className="font-display text-xl font-bold">Rate what you just ate</h2>
          <p className="text-sm text-muted-foreground">Dishes you've rated or opened recently.</p>
          <ul className="mt-3 space-y-2">
            {profile.history.length === 0 && <li className="text-sm text-muted-foreground">No recent meals yet.</li>}
            {profile.history.slice(0, 4).map((h) => {
              const d = byId.get(h.dishId);
              if (!d) return null;
              return (
                <li key={h.at} className="flex items-center justify-between gap-2 text-sm">
                  <span>
                    <strong>{d.name}</strong> <span className="text-muted-foreground">· {timeAgo(h.at)}</span>
                  </span>
                  <Link to="/feedback" search={{ dish: d.id }} className="text-primary">
                    Rate →
                  </Link>
                </li>
              );
            })}
          </ul>
          <Link to="/feedback" className="btn-primary mt-4 w-full">
            Request a new item
          </Link>
        </section>
      </div>
    </PageShell>
  );
}
