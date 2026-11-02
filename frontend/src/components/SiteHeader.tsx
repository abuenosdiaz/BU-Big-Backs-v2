import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { CalendarDays, Check, ChevronDown, UserRound } from "lucide-react";
import { toast } from "sonner";
import logo from "@/assets/logo.png";
import { signInWithGoogle, signOut, useAuth } from "@/lib/auth";
import { today, useMenuDate } from "@/lib/dishes";
import { setSelectedDate } from "@/lib/menu-date";
import { cn } from "@/lib/utils";

/** Parse a YYYY-MM-DD string as a fixed calendar date (noon UTC) so the day
 *  shown never shifts by the viewer's timezone. */
function parseYmd(ymd: string) {
  return new Date(`${ymd}T12:00:00Z`);
}

/** Format a menu date as e.g. "Sat, Nov 1". */
function formatMenuDate(ymd: string) {
  return parseYmd(ymd).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  });
}

/** Short weekday + day number for the dropdown cells, e.g. "Sat" / "1". */
function dayParts(ymd: string) {
  const d = parseYmd(ymd);
  return {
    weekday: d.toLocaleDateString("en-US", { weekday: "short", timeZone: "America/New_York" }),
    day: d.toLocaleDateString("en-US", { day: "numeric", timeZone: "America/New_York" }),
    month: d.toLocaleDateString("en-US", { month: "short", timeZone: "America/New_York" }),
  };
}

/**
 * Build the full day grid to show in the dropdown: every date from the earliest
 * available through the latest available, unioned with today. Days the backend
 * has data for are selectable; the rest are rendered with a gray cover and are
 * not clickable. This way the calendar shows a continuous span with the gaps
 * visibly disabled, rather than only the handful of available days.
 */
function buildDayGrid(available: string[]): string[] {
  const t = today();
  const all = available.length ? [...available, t] : [t];
  const sorted = [...new Set(all)].sort();
  const first = sorted[0] ?? t;
  const last = sorted[sorted.length - 1] ?? t;
  const start = parseYmd(first);
  const end = parseYmd(last);
  const days: string[] = [];
  for (let d = new Date(start); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

function MenuDatePicker() {
  const { data } = useMenuDate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!data?.date) return null;

  const available = new Set(data.available);
  const grid = buildDayGrid(data.available);
  const t = today();

  const pick = (ymd: string) => {
    if (!available.has(ymd)) return;
    // Picking today returns to auto; any other day is an explicit override.
    setSelectedDate(ymd === t ? null : ymd);
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="chip flex items-center gap-1.5"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={data.isToday ? "Today's menu — click to change the day" : "Click to change the day"}
      >
        <CalendarDays className="h-4 w-4" />
        <span className="tabular-nums">{formatMenuDate(data.date)}</span>
        {!data.isToday && <span className="text-muted-foreground">({data.isOverride ? "selected" : "latest"})</span>}
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Pick a menu day"
          className="absolute right-0 z-50 mt-2 w-72 rounded-2xl border border-border bg-card p-4 shadow-xl"
        >
          <div className="flex items-center justify-between">
            <p className="font-display text-sm font-bold">Choose a day</p>
            <button type="button" className="text-xs text-primary" onClick={() => pick(t)}>
              Today
            </button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">Grayed-out days have no menu available.</p>
          <div className="mt-3 grid grid-cols-4 gap-2">
            {grid.map((ymd) => {
              const isAvail = available.has(ymd);
              const isSelected = ymd === data.date;
              const isToday = ymd === t;
              const p = dayParts(ymd);
              return (
                <button
                  key={ymd}
                  type="button"
                  disabled={!isAvail}
                  onClick={() => pick(ymd)}
                  aria-current={isSelected ? "date" : undefined}
                  className={cn(
                    "relative flex flex-col items-center rounded-lg border px-1 py-2 text-xs transition-colors",
                    isAvail ? "cursor-pointer border-border hover:border-primary hover:bg-secondary" : "cursor-not-allowed border-transparent",
                    isSelected && "border-primary bg-primary/10 font-bold",
                  )}
                  title={isAvail ? formatMenuDate(ymd) : `${formatMenuDate(ymd)} — no menu`}
                >
                  <span className="text-muted-foreground">{p.weekday}</span>
                  <span className="tabular-nums text-foreground">{p.day}</span>
                  <span className="text-[10px] text-muted-foreground">{p.month}</span>
                  {isToday && <span className="mt-0.5 h-1 w-1 rounded-full bg-primary" aria-hidden />}
                  {isSelected && (
                    <span className="absolute right-0.5 top-0.5 text-primary">
                      <Check className="h-3 w-3" />
                    </span>
                  )}
                  {/* Slight gray cover over unavailable days. */}
                  {!isAvail && (
                    <span className="absolute inset-0 rounded-lg bg-muted-foreground/25" aria-hidden />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

const NAV = [
  { to: "/", label: "Dishes" },
  { to: "/near-me", label: "Near me" },
  { to: "/forums", label: "Forums" },
  { to: "/feedback", label: "Feedback" },
  { to: "/stats", label: "Stats" },
] as const;

export function SiteHeader() {
  const user = useAuth();
  const avatar = user?.user_metadata?.["avatar_url"] as string | undefined;
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-3 px-5 py-3 md:px-8">
        <Link to="/" className="flex shrink-0 items-center gap-3" aria-label="BU Big Backs home">
          <img src={logo} alt="" className="h-14 w-auto" />
          <span className="font-display text-2xl font-black leading-none tracking-tight text-foreground">Big Backs</span>
        </Link>
        <nav aria-label="Main" className="flex flex-wrap gap-1">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className="nav-link"
              activeOptions={{ exact: n.to === "/" }}
              activeProps={{ className: "nav-link-active", "aria-current": "page" }}
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <MenuDatePicker />
          <button
            type="button"
            className="chip"
            onClick={() =>
              (user ? signOut() : signInWithGoogle()).catch((e: Error) =>
                toast.error(user ? "Couldn't sign out. Try again." : `Couldn't sign in with Google: ${e.message}`),
              )
            }
          >
            {user ? "Sign out" : "Sign in with Google"}
          </button>
          <Link
            to="/profile"
            aria-label="My profile"
            className="grid h-11 w-11 place-items-center overflow-hidden rounded-full bg-secondary text-foreground transition-colors hover:bg-border"
            activeProps={{ className: "!bg-foreground !text-background" }}
          >
            {avatar ? (
              <img src={avatar} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
            ) : (
              <UserRound className="h-5 w-5" />
            )}
          </Link>
        </div>
      </div>
    </header>
  );
}

export function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <SiteHeader />
      <main className="mx-auto max-w-7xl px-5 pb-20 pt-10 md:px-8">{children}</main>
    </div>
  );
}
