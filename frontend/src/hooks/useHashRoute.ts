import { useEffect, useState } from "react";

export type AppRoute = "landing" | "studio";

function routeFromHash(hash: string): AppRoute {
  const normalized = hash.replace(/^#/, "") || "/";
  if (normalized.startsWith("/studio")) return "studio";
  return "landing";
}

export function useHashRoute(): AppRoute {
  const [route, setRoute] = useState<AppRoute>(() =>
    typeof window !== "undefined" ? routeFromHash(window.location.hash) : "landing",
  );

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return route;
}