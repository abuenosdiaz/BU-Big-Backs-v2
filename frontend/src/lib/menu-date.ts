import { useSyncExternalStore } from "react";

/**
 * Global "which service day is the app showing" state.
 *
 * A single selected date drives every data view (home dishes, near-me, dish
 * pages, stats). `null` means "auto": follow today when the backend has it,
 * else the most recent available day (resolved in `dishes.ts`). Picking a day
 * in the header's calendar sets an explicit override here; choosing "Today"
 * clears it back to auto.
 *
 * It lives in a tiny external store (not React context) so any component can
 * read it and re-render on change. It is kept in `sessionStorage`, so a picked
 * day persists across reloads and page navigation *within the same visit*, but
 * every fresh open of the website starts back on "auto" (today). Dates are
 * plain `YYYY-MM-DD` strings.
 */

const KEY = "bb-selected-date";
const EVENT = "bb-selected-date-change";

function read(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** Set the explicit day override, or clear it (null) to return to auto/today. */
export function setSelectedDate(date: string | null) {
  try {
    if (date) sessionStorage.setItem(KEY, date);
    else sessionStorage.removeItem(KEY);
  } catch {
    // Ignore storage failures (e.g. private mode); the event still fires so
    // the in-memory UI updates for this session.
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  // sessionStorage is per-tab, so there is no cross-tab sync to listen for;
  // our own custom event covers every in-app change.
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}

/**
 * The user's explicit date override, or `null` for auto. Returns `null` during
 * SSR (no localStorage), which correctly means "auto" until hydration.
 */
export function useSelectedDate(): string | null {
  return useSyncExternalStore(subscribe, read, () => null);
}
