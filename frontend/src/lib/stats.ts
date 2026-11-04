import { DIET_KEYS, type DietKey, type HallStats, type Stats } from "@/lib/api";
import { HALLS } from "@/lib/halls";

export const DIET_LABEL: Record<DietKey, string> = {
  vegetarian: "Vegetarian",
  vegan: "Vegan",
  gluten_free: "Gluten-free",
  halal: "Halal",
  cool_food: "Cool Food",
};

export const pct = (v: number) => `${v.toFixed(1)}%`;
export const short = (h: HallStats) =>
  HALLS.find((x) => x.name === h.dining_hall)?.short ?? h.dining_hall;
/** Plain-language findings, computed from the summary so they stay true as menus change. */
export function findings(s: Stats) {
  const out: string[] = [];
  const halls = s.halls;
  const overall = s.overall;
  if (!overall || halls.length < 2) return out;

  // Cool Food is BU's low-carbon label, not a diet anyone has to follow.
  const rarest = DIET_KEYS.filter((k) => k !== "cool_food").sort(
    (a, b) => overall.percent[a] - overall.percent[b],
  )[0]!;
  const rare = overall.percent[rarest];
  out.push(
    `${DIET_LABEL[rarest]} is the hardest diet to eat on: only ${pct(rare)} of items fit it, about 1 in ${Math.round(100 / rare)}.`,
  );

  const spread = (k: DietKey) => {
    const sorted = [...halls].sort((a, b) => b[`percent_${k}`] - a[`percent_${k}`]);
    return { k, hi: sorted[0]!, lo: sorted.at(-1)! };
  };
  const gaps = DIET_KEYS.map(spread);
  const widest = [...gaps].sort(
    (a, b) =>
      b.hi[`percent_${b.k}`] -
      b.lo[`percent_${b.k}`] -
      (a.hi[`percent_${a.k}`] - a.lo[`percent_${a.k}`]),
  )[0]!;
  const [wHi, wLo] = [widest.hi[`percent_${widest.k}`], widest.lo[`percent_${widest.k}`]];
  out.push(
    `${short(widest.hi)} has the most ${DIET_LABEL[widest.k].toLowerCase()} options (${pct(wHi)}), ${(wHi - wLo).toFixed(1)} points more than ${short(widest.lo)} (${pct(wLo)}).`,
  );

  // For rare diets a few points can still mean several times the choice.
  const ratio = gaps
    .filter((g) => g.k !== widest.k && g.lo[`percent_${g.k}`] > 0)
    .map((g) => ({ ...g, r: g.hi[`percent_${g.k}`] / g.lo[`percent_${g.k}`] }))
    .sort((a, b) => b.r - a.r)[0];
  if (ratio && ratio.r >= 1.5) {
    out.push(
      `If you eat ${DIET_LABEL[ratio.k].toLowerCase()}, ${short(ratio.hi)} gives you ${ratio.r.toFixed(1)}× the share of options that ${short(ratio.lo)} does (${pct(ratio.hi[`percent_${ratio.k}`])} vs ${pct(ratio.lo[`percent_${ratio.k}`])}).`,
    );
  }

  const cal = [...halls].sort((a, b) => b.avg_calories - a.avg_calories);
  const [cHi, cLo] = [cal[0]!, cal.at(-1)!];
  out.push(
    `${short(cHi)} serves the heaviest food: ${Math.round(cHi.avg_calories)} calories per item on average, ${Math.round(((cHi.avg_calories - cLo.avg_calories) / cLo.avg_calories) * 100)}% more than ${short(cLo)} (${Math.round(cLo.avg_calories)}).`,
  );

  const moves = halls
    .flatMap((h) => DIET_KEYS.map((k) => ({ h, k, d: h[`change_${k}`] })))
    .filter((m): m is { h: HallStats; k: DietKey; d: number } => m.d != null);
  const big = [...moves].sort((a, b) => Math.abs(b.d) - Math.abs(a.d))[0];
  if (big && big.d !== 0) {
    out.push(
      `Biggest change this week: ${short(big.h)} has ${Math.abs(big.d)} ${big.d > 0 ? "more" : "fewer"} ${DIET_LABEL[big.k].toLowerCase()} items on ${big.h.last_day} than on ${big.h.first_day}.`,
    );
  }
  return out;
}
