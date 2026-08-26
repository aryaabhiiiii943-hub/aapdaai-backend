/*
 * Live incidents from the backend, with the demo data as a safety net.
 *
 * WHY A HOOK
 *   So the change inside App.jsx is two lines. A 4,017-line file that someone
 *   else wrote is not the place to add fetching, polling, error handling and
 *   cold-start logic - it's the place to call something that already does it.
 *
 * WHY IT FALLS BACK RATHER THAN FAILING
 *   Venue wifi is bad and the free dyno sleeps. A dashboard that shows an
 *   error on stage is worse than one that shows seeded data and says so.
 *   `source` tells you which you're looking at, so nobody accidentally
 *   presents fixtures as live.
 *
 * Drop this in as  src/useIncidents.js
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchIncidents, wake } from "./api";

/* Ten seconds is real-time for this domain. Nobody dispatches an ambulance in
 * under ten seconds, and saying that out loud turns a limitation into a
 * deliberate choice. It also keeps the free instance from falling asleep
 * mid-demo. */
const POLL_MS = 10_000;

export function useIncidents(fallback = []) {
  const [incidents, setIncidents] = useState(fallback);
  const [source, setSource] = useState("demo");   // "live" | "demo"
  const [waking, setWaking] = useState(false);
  const [error, setError] = useState(null);

  /* Held in a ref so a manual refresh mid-poll doesn't fight the timer. */
  const timer = useRef(null);

  const load = useCallback(async ({ firstRun = false } = {}) => {
    try {
      if (firstRun) {
        setWaking(true);
        await wake();          // may take ~50s on a cold free instance
        setWaking(false);
      }
      const live = await fetchIncidents();
      setIncidents(live);
      setSource("live");
      setError(null);
    } catch (err) {
      setWaking(false);
      setError(err.message);
      /* Keep whatever we last had. Replacing real data with an empty list
       * because one poll failed is how a dashboard goes blank at the worst
       * possible moment. */
      setSource((prev) => (prev === "live" ? "live" : "demo"));
    }
  }, []);

  useEffect(() => {
    load({ firstRun: true });
    timer.current = setInterval(load, POLL_MS);
    return () => clearInterval(timer.current);
  }, [load]);

  return {
    incidents,
    setIncidents,      // so optimistic local updates still work
    refresh: load,
    source,            // "live" or "demo" - show this somewhere honest
    waking,            // true while the server is starting up
    error,
  };
}
