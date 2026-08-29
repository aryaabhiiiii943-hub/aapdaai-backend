/*
 * Talking to the AapdaAi backend.
 *
 * WHY THIS FILE IS SEPARATE
 *   App.jsx is 4,017 lines. Every line added there is a line your teammate has
 *   to review inside something he already knows. Everything network-shaped
 *   lives here instead, so the diff in App.jsx is a handful of lines.
 *
 * THE SHAPE PROBLEM
 *   The backend and this UI were built independently and describe an incident
 *   differently. Rather than rewriting either, `toUiIncident` translates - so
 *   every existing component keeps working untouched, and new fields
 *   (confidence, dispatch, shortage) ride along for whatever we add next.
 *
 * Drop this in as  src/api.js
 */

const API =
  import.meta.env.VITE_API_URL || "https://aapdaai-backend.onrender.com";

/* ------------------------------------------------------------------ *
 * Render's free tier sleeps after ~15 minutes and takes about 50
 * seconds to wake. The first request after a quiet spell doesn't fail -
 * it hangs - which on screen is indistinguishable from being broken.
 * Callers use `waking` to say "starting the server" instead of showing
 * an error that isn't one.
 * ------------------------------------------------------------------ */
let awake = false;

export async function wake() {
  if (awake) return true;
  try {
    const r = await fetch(`${API}/health`, { cache: "no-store" });
    awake = r.ok;
    return awake;
  } catch {
    return false;
  }
}

async function get(path) {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${path} → ${r.status}`);
  return data;
}

/* ================================================================== *
 * Translating one incident into the shape this UI already expects
 * ================================================================== */

/* Severity is a 0-100 number; this UI shows P1/P2/P3. Mapping through the
 * band rather than the raw number keeps the two in step if the thresholds
 * ever move. */
const PRIORITY = { critical: "P1", high: "P2", medium: "P3", low: "P3" };

/* The backend keeps two lifecycles apart on purpose - "is it real" and "is
 * anyone on it". This UI has one field. We surface the response state here
 * and keep confirmation in `verification`, so nothing is lost. */
const STATUS = {
  pending: "Active",
  assigned: "Responding",
  in_progress: "Responding",
  resolved: "Resolved",
};

/* Rough district split so the city filter keeps working. The backend has no
 * city concept - it has coordinates, which are more useful and less wrong. */
function cityFor(lat, lng, place) {
  const text = (place || "").toLowerCase();
  if (text.includes("cuttack")) return "Cuttack";
  if (text.includes("puri")) return "Puri";
  if (text.includes("bhubaneswar") || text.includes("patia")) return "Bhubaneswar";
  return lat > 20.42 ? "Cuttack" : "Bhubaneswar";
}

/* A reporter's phone number has no business being displayed on a wall screen
 * in a control room. Enough to tell two reporters apart, not enough to call
 * one of them. */
function maskReporter(b) {
  const n = b.independent_reporters || 0;
  if (n > 1) return `${n} independent reports`;
  return "Citizen report";
}

/* What we understood, in a sentence, for the description field. */
function describe(b) {
  const bits = [];
  if (b.people) bits.push(`${b.people} people`);
  if (b.injured) bits.push(`${b.injured} injured`);
  if (b.trapped) bits.push(`${b.trapped} trapped`);
  if (b.needs?.length) bits.push(`needs ${b.needs.join(", ")}`);
  if (b.vulnerable?.length)
    bits.push(`includes ${b.vulnerable.join(", ")} who cannot self-evacuate`);
  return bits.join(" · ") || "Report received, details pending.";
}

export function toUiIncident(b) {
  return {
    /* --- what the existing components read ------------------------- */
    id: b.id,
    city: cityFor(b.lat, b.lng, b.place),
    type: b.hazard
      ? b.hazard.charAt(0).toUpperCase() + b.hazard.slice(1)
      : (b.needs?.[0]
          ? b.needs[0].charAt(0).toUpperCase() + b.needs[0].slice(1)
          : "General"),
    location: b.place || `${b.lat.toFixed(4)}, ${b.lng.toFixed(4)}`,
    people: b.people ?? 0,
    priority: PRIORITY[b.severity_band] || "P3",
    verification: b.confirmation === "confirmed" ? "Verified" : "Pending",
    status: STATUS[b.response] || "Active",
    reporterName: maskReporter(b),
    description: describe(b),
    photo: null,
    video: null,
    createdAt: b.created_at,

    /* --- everything the UI doesn't use yet, kept for what we add ---- *
     * confidence especially: it has no equivalent in this UI, and it's
     * the thing that separates an unverified rumour of a collapse from a
     * confirmed one. */
    severity: b.severity,
    severityBand: b.severity_band,
    confidence: b.confidence,
    confidenceLabel: b.confidence_label,
    reports: b.reports,
    independentReporters: b.independent_reporters,
    confirmation: b.confirmation,
    response: b.response,
    hazard: b.hazard,
    accessBlocked: b.access_blocked,
    injured: b.injured,
    trapped: b.trapped,
    needs: b.needs || [],
    vulnerable: b.vulnerable || [],
    send: b.send || {},
    dispatch: b.dispatch || [],
    shortage: b.shortage || [],
    assigned: b.assigned || [],
    exceedsLocalCapacity: b.exceeds_local_capacity || [],
    lat: b.lat,
    lng: b.lng,
  };
}

/* ================================================================== *
 * The calls
 * ================================================================== */

export async function fetchIncidents() {
  const { incidents } = await get("/incidents");
  return incidents.map(toUiIncident);
}

export async function fetchStats() {
  return get("/stats");
}

export async function fetchResources() {
  return get("/resources");
}

export async function fetchFacilities() {
  return get("/facilities");
}

export async function fetchFollowUps() {
  const { follow_ups } = await get("/follow-ups");
  return follow_ups;
}

export async function fetchMyReports({ reporter = "", reportedBy = "" } = {}) {
  const params = new URLSearchParams();
  if (reporter) params.set("reporter", reporter);
  if (reportedBy) params.set("reported_by", reportedBy);
  const query = params.toString();
  const { reports } = await get(`/reports${query ? `?${query}` : ""}`);
  return reports;
}

/* The citizen form and the 112 operator console both land here. `source`
 * decides how much one report is worth on its own. */
export async function submitReport({
  text,
  lat,
  lng,
  place,
  phone,
  source = "web",
  reportedBy = "",
}) {
  return post("/reports", {
    text, lat, lng, place, phone, source, reported_by: reportedBy,
  });
}

/* A decision without a name attached is not a decision - the backend rejects
 * it with a 422, deliberately. */
export async function verifyIncident(id, decidedBy, decision, note = "") {
  return post(`/incidents/${id}/verify`, {
    decided_by: decidedBy, decision, note,
  });
}

export async function assignResource(incidentId, resourceId) {
  return post(`/incidents/${incidentId}/assign`, { resource_id: resourceId });
}

export async function previewOfficerBrief(id) {
  return get(`/incidents/${id}/preview`);
}

export async function notifyOfficer(id) {
  return post(`/incidents/${id}/notify`, {});
}

export async function seedDemo() {
  return post("/demo/seed", {});
}

export { API };
