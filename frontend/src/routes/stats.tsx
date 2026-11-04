import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageShell } from "@/components/SiteHeader";
import { DIET_KEYS, getStats, type HallStats } from "@/lib/api";
import { useMenuDate } from "@/lib/dishes";
import { HALLS } from "@/lib/halls";
import { DIET_LABEL, findings, pct, short } from "@/lib/stats";

export const Route = createFileRoute("/stats")({
  head: () => ({
    meta: [
      { title: "Dining hall stats — BU Big Backs" },
      {
        name: "description",
        content:
          "How BU's dining halls compare on vegetarian, vegan, halal and gluten-free options and nutrition.",
      },
      { property: "og:title", content: "Dining hall stats — BU Big Backs" },
      { property: "og:type", content: "website" },
    ],
  }),
  component: StatsPage,
});

const NUTRITION = [
  ["avg_calories", "Calories", ""],
  ["avg_carbohydrates_grams", "Carbs", "g"],
  ["avg_protein_grams", "Protein", "g"],
  ["avg_saturated_fat_grams", "Sat. fat", "g"],
] as const;

/** Each hall keeps the same color everywhere: its position in HALLS. */
const hallColor = (h: HallStats) =>
  `var(--hall-${HALLS.findIndex((x) => x.name === h.dining_hall) + 1})`;
const byHallOrder = (halls: HallStats[]) =>
  [...halls].sort(
    (a, b) =>
      HALLS.findIndex((x) => x.name === a.dining_hall) -
      HALLS.findIndex((x) => x.name === b.dining_hall),
  );

function StatsPage() {
  // Always scope the stats to the day currently being shown -- whether that's
  // an explicitly picked day or the auto-resolved current day. The change
  // table then uniformly compares that day to the same weekday one week LATER,
  // so the current day behaves like any other: it only ever references a future
  // day, never the window's other end. (Previously, auto/today mode used the
  // whole-window summary, which compared the window's first and last days and
  // produced a caption referring to days other than the one shown.)
  const menuDate = useMenuDate();
  const scopeDate = menuDate.data?.date ?? null;
  // The current day can never have a real week-over-week comparison: the same
  // weekday a week later hasn't happened yet. Even if a forward-polled folder
  // for that future date exists, we don't treat it as observed history, so the
  // change table for today always reports "not enough future data".
  const scopeIsToday = !!menuDate.data?.isToday;
  const { data, isLoading, error } = useQuery({
    queryKey: ["stats", scopeDate],
    queryFn: () => getStats(scopeDate),
  });

  return (
    <PageShell>
      <p className="eyebrow">{scopeDate ? "Menu for one day" : "This week's menus"}</p>
      <h1 className="page-title mt-2">How the dining halls compare</h1>
      {data?.overall && (
        <p className="mt-3 max-w-2xl text-muted-foreground">
          {data.scope_date ? (
            <>Every item on BU's menus for {data.scope_date}, across {data.overall.halls} dining halls.</>
          ) : (
            <>Every item on BU's menus from {data.overall.first_day} to {data.overall.last_day}, across {data.overall.halls} dining halls.</>
          )}
        </p>
      )}

      {error && <p className="mt-10 text-destructive">Couldn't load the stats. Try refreshing.</p>}
      {isLoading && <div className="mt-10 h-64 animate-pulse rounded-2xl bg-muted" />}

      {data && !data.overall && !isLoading && (
        <p className="mt-10 text-muted-foreground">
          No menu data yet. Once the dining menus load, the comparison will appear here.
        </p>
      )}

      {data?.overall && (
        (() => {
          const overall = data.overall;
          return (
        <>
          <section className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Kpi label="Menu items" value={overall.total_items.toLocaleString()} />
            {(["vegetarian", "vegan", "halal", "gluten_free"] as const).map((k) => (
              <Kpi key={k} label={DIET_LABEL[k]} value={pct(overall.percent[k])} />
            ))}
          </section>

          <section className="panel mt-8">
            <h2 className="section-title">What stands out</h2>
            <ul className="mt-3 list-disc space-y-2 pl-5 text-foreground">
              {findings(data).map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </section>

          <DietChart halls={byHallOrder(data.halls)} />
          <NutritionTable halls={byHallOrder(data.halls)} />
          <ChangeTable
            halls={byHallOrder(data.halls)}
            scopeDate={data.scope_date}
            // Force "no future data" for the current day: a full week hasn't
            // elapsed, so there is nothing real to compare it against.
            nextWeek={scopeIsToday ? { date: null, available: false } : data.next_week}
          />

          <p className="mt-8 max-w-3xl text-xs text-muted-foreground">
            From the menu API's per-hall summary (backend/dining_diet_analysis.py). An item counts
            once each time it's served, so a dish on at lunch and dinner counts twice. Cool Food
            items are BU's lower-carbon picks. Nutrition is per item as listed by BU Dining.
          </p>
        </>
          );
        })()
      )}
    </PageShell>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel">
      <p className="field-label">{label}</p>
      <p className="mt-1 font-display text-3xl font-black tabular-nums text-foreground">{value}</p>
    </div>
  );
}

function Legend({ halls }: { halls: HallStats[] }) {
  return (
    <div className="mt-3 flex flex-wrap gap-4 text-sm text-foreground">
      {halls.map((h) => (
        <span key={h.dining_hall} className="flex items-center gap-2">
          <span className="h-3 w-3 rounded-sm" style={{ background: hallColor(h) }} />
          {short(h)}
        </span>
      ))}
    </div>
  );
}

function DietChart({ halls }: { halls: HallStats[] }) {
  const rows = DIET_KEYS.map((k) => ({
    diet: DIET_LABEL[k],
    ...Object.fromEntries(halls.map((h) => [short(h), h[`percent_${k}`]])),
  }));
  return (
    <section className="panel mt-8">
      <h2 className="section-title">Share of items that fit each diet</h2>
      <Legend halls={halls} />
      <div className="mt-4 h-[420px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ left: 8, right: 48 }}
            barGap={2}
            barCategoryGap="22%"
          >
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis
              type="number"
              domain={[0, 100]}
              tickFormatter={(v: number) => `${v}%`}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="diet"
              width={92}
              tick={{ fill: "var(--foreground)", fontSize: 13 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              formatter={(v: number, name: string) => [pct(v), name]}
              contentStyle={{
                background: "var(--card)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                color: "var(--foreground)",
              }}
            />
            {halls.map((h) => (
              <Bar
                key={h.dining_hall}
                dataKey={short(h)}
                fill={hallColor(h)}
                radius={[0, 4, 4, 0]}
                maxBarSize={16}
              >
                <LabelList
                  dataKey={short(h)}
                  position="right"
                  formatter={(v: number) => `${Math.round(v)}%`}
                  style={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                />
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-primary">See the numbers</summary>
        <table className="mt-3 w-full text-left tabular-nums">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 font-medium">Diet</th>
              {halls.map((h) => (
                <th key={h.dining_hall} className="py-1 font-medium">
                  {short(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {DIET_KEYS.map((k) => (
              <tr key={k} className="border-t border-border">
                <td className="py-1">{DIET_LABEL[k]}</td>
                {halls.map((h) => (
                  <td key={h.dining_hall} className="py-1">
                    {pct(h[`percent_${k}`])} ({h[`total_${k}`].toLocaleString()} items)
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

function NutritionTable({ halls }: { halls: HallStats[] }) {
  return (
    <section className="panel mt-8 overflow-x-auto">
      <h2 className="section-title">Average nutrition per item</h2>
      <p className="mt-1 text-sm text-muted-foreground">The highest in each column is in bold.</p>
      <table className="mt-4 w-full min-w-[480px] text-left tabular-nums">
        <thead className="text-sm text-muted-foreground">
          <tr>
            <th className="py-2 font-medium">Hall</th>
            {NUTRITION.map(([, label]) => (
              <th key={label} className="py-2 font-medium">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {halls.map((h) => (
            <tr key={h.dining_hall} className="border-t border-border">
              <td className="py-2">{short(h)}</td>
              {NUTRITION.map(([key, , unit]) => {
                const top = Math.max(...halls.map((x) => x[key]));
                return (
                  <td key={key} className={h[key] === top ? "py-2 font-bold" : "py-2"}>
                    {key === "avg_calories" ? Math.round(h[key]) : h[key].toFixed(1)}
                    {unit}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ChangeTable({
  halls,
  scopeDate,
  nextWeek,
}: {
  halls: HallStats[];
  scopeDate: string | null;
  nextWeek: { date: string | null; available: boolean } | null;
}) {
  const first = halls[0];

  // The comparison for a day is that day vs. the same weekday one week later.
  // If that following-week day has no data (which is always the case for the
  // current day -- next week hasn't happened yet), we cannot compute a change,
  // so say so clearly instead of showing zeros or referencing another day.
  if (scopeDate && (!nextWeek || !nextWeek.available)) {
    return (
      <section className="panel mt-8">
        <h2 className="section-title">Change over the week</h2>
        <p className="mt-2 text-muted-foreground">
          There isn't enough data in the future to compare this day with the following week yet.
        </p>
        {nextWeek?.date && (
          <p className="mt-1 text-sm text-muted-foreground">
            A comparison will appear once {nextWeek.date} has been collected.
          </p>
        )}
      </section>
    );
  }

  // Are two YYYY-MM-DD dates exactly seven days apart?
  const isOneWeekApart = (a?: string | null, b?: string | null) => {
    if (!a || !b) return false;
    const ms = Math.abs(Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`));
    return ms === 7 * 24 * 60 * 60 * 1000;
  };

  // Caption only when the two days being compared are genuinely a week apart,
  // so we never label a mismatched pair (e.g. a partial window) as a
  // week-over-week comparison. Otherwise show no date pairing at all.
  const fullWeekApart = !scopeDate && first && isOneWeekApart(first.first_day, first.last_day);
  const caption = scopeDate
    ? `Items that fit each diet on ${nextWeek?.date}, compared with ${scopeDate} (one week earlier).`
    : fullWeekApart
      ? `Items that fit each diet on ${first.last_day}, compared with ${first.first_day} (one week earlier).`
      : first
        ? `Change in items that fit each diet from ${first.first_day} to ${first.last_day}.`
        : "";

  return (
    <section className="panel mt-8 overflow-x-auto">
      <h2 className="section-title">Change over the week</h2>
      {caption && <p className="mt-1 text-sm text-muted-foreground">{caption}</p>}
      <table className="mt-4 w-full min-w-[520px] text-left tabular-nums">
        <thead className="text-sm text-muted-foreground">
          <tr>
            <th className="py-2 font-medium">Hall</th>
            {DIET_KEYS.map((k) => (
              <th key={k} className="py-2 font-medium">
                {DIET_LABEL[k]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {halls.map((h) => (
            <tr key={h.dining_hall} className="border-t border-border">
              <td className="py-2">{short(h)}</td>
              {DIET_KEYS.map((k) => {
                const d = h[`change_${k}`];
                return (
                  <td key={k} className="py-2">
                    {d == null ? "—" : d > 0 ? `▲ +${d}` : d < 0 ? `▼ ${d}` : "— 0"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
