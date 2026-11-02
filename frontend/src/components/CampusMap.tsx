import { useEffect, useRef } from "react";
import type { Map as LeafletMap, Marker, Circle } from "leaflet";
import type { Hall } from "@/lib/halls";

export type MapPoint = { lat: number; lng: number };

type CampusMapProps = {
  halls: Hall[];
  /** The point to treat as "you": real GPS when available, else a fallback. */
  here: MapPoint;
  /** True when ``here`` is the user's actual geolocation (vs. a fallback). */
  located: boolean;
  /** Called when a hall marker is clicked, so the list can scroll/highlight. */
  onSelectHall?: (hallId: string) => void;
  className?: string;
};

// A small inline SVG pin, colored per role, as a Leaflet divIcon. Keeping the
// icon inline avoids shipping Leaflet's default marker image assets (which need
// extra bundler wiring) and lets the pins match the app's theme.
// The icon HTML must NOT carry its own translate/offset. Leaflet anchors the
// icon via `iconAnchor` by setting the element's absolute position to match
// `iconSize`; an extra CSS transform here fights Leaflet's zoom-animation
// transforms and makes the pin drift on zoom. So the root is a plain
// `iconSize`-sized box (30x42) whose bottom-center tip is the anchor, and the
// label is placed with `position:absolute` so it never changes the box size.
function pinHtml(color: string, label: string) {
  return `
    <div style="position:relative;width:30px;height:42px;">
      <svg width="30" height="42" viewBox="0 0 30 42" xmlns="http://www.w3.org/2000/svg" style="display:block;">
        <path d="M15 0C6.7 0 0 6.7 0 15c0 10.5 15 27 15 27s15-16.5 15-27C30 6.7 23.3 0 15 0z" fill="${color}"/>
        <circle cx="15" cy="15" r="6" fill="white"/>
      </svg>
      <span style="position:absolute;left:15px;top:44px;transform:translateX(-50%);white-space:nowrap;font-size:11px;font-weight:700;color:#1f2937;text-shadow:0 1px 2px rgba(255,255,255,0.9);">${label}</span>
    </div>`;
}

/**
 * An interactive OpenStreetMap view of the dining halls and the user.
 *
 * Leaflet is loaded dynamically (client-only) because it touches ``window`` and
 * must not run during server-side rendering. Hall markers use each hall's real
 * geocoded coordinates; the "you" marker uses the live GPS position when the
 * user has granted location, otherwise a labeled campus-center fallback.
 */
export function CampusMap({ halls, here, located, onSelectHall, className }: CampusMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const hallMarkersRef = useRef<Marker[]>([]);
  const youRef = useRef<Marker | null>(null);
  const accuracyRef = useRef<Circle | null>(null);
  const onSelectRef = useRef(onSelectHall);
  onSelectRef.current = onSelectHall;

  // Create the map once, on mount (client only).
  useEffect(() => {
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    (async () => {
      const L = await import("leaflet");
      await import("leaflet/dist/leaflet.css");
      if (cancelled || !containerRef.current || mapRef.current) return;

      const map = L.map(containerRef.current, {
        center: [here.lat, here.lng],
        zoom: 15,
        scrollWheelZoom: false,
      });
      mapRef.current = map;

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
      }).addTo(map);

      // Hall markers.
      halls.forEach((h) => {
        const marker = L.marker([h.lat, h.lng], {
          icon: L.divIcon({
            html: pinHtml("#cc0000", h.short),
            className: "",
            iconSize: [30, 42],
            iconAnchor: [15, 42],
          }),
          title: h.name,
        }).addTo(map);
        marker.bindPopup(
          `<strong>${h.name}</strong><br>${h.address}<br>${h.hours}`,
        );
        marker.on("click", () => onSelectRef.current?.(h.id));
        hallMarkersRef.current.push(marker);
      });

      // Fit the view to show every hall plus the user.
      const bounds = L.latLngBounds([
        ...halls.map((h) => [h.lat, h.lng] as [number, number]),
        [here.lat, here.lng],
      ]);
      map.fitBounds(bounds, { padding: [48, 48], maxZoom: 16 });

      // Leaflet needs a size recalculation if the container was hidden/resized
      // when the map was created (common inside responsive grids).
      resizeObserver = new ResizeObserver(() => map.invalidateSize());
      resizeObserver.observe(containerRef.current);
    })();

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      mapRef.current?.remove();
      mapRef.current = null;
      hallMarkersRef.current = [];
      youRef.current = null;
      accuracyRef.current = null;
    };
    // Halls are static; intentionally run once. Position updates are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update the "you" marker whenever the position or located-state changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const L = await import("leaflet");
      const map = mapRef.current;
      if (cancelled || !map) return;

      const color = located ? "#2563eb" : "#9ca3af";
      const label = located ? "You" : "Campus center";
      // Plain 18x18 box (no wrapper transform) so Leaflet's iconAnchor [9,9]
      // keeps the dot centered on the real position through zoom animations.
      const html = `
        <div style="position:relative;width:18px;height:18px;">
          <span style="display:block;width:18px;height:18px;border-radius:9999px;background:${color};border:3px solid white;box-shadow:0 0 0 2px ${color};"></span>
          <span style="position:absolute;left:9px;top:22px;transform:translateX(-50%);white-space:nowrap;font-size:11px;font-weight:700;color:#1f2937;text-shadow:0 1px 2px rgba(255,255,255,0.9);">${label}</span>
        </div>`;

      if (youRef.current) {
        youRef.current.setLatLng([here.lat, here.lng]);
        youRef.current.setIcon(
          L.divIcon({ html, className: "", iconSize: [18, 18], iconAnchor: [9, 9] }),
        );
      } else {
        youRef.current = L.marker([here.lat, here.lng], {
          icon: L.divIcon({ html, className: "", iconSize: [18, 18], iconAnchor: [9, 9] }),
          title: label,
          zIndexOffset: 1000,
        }).addTo(map);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [here.lat, here.lng, located]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ minHeight: 320, height: "100%", width: "100%", borderRadius: 16, zIndex: 0 }}
      role="application"
      aria-label="Map of BU dining halls and your location"
    />
  );
}
