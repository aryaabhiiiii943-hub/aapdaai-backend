# Wiring the dashboard to the backend

Three files, and a **two-line change** inside `App.jsx`. Deliberately small:
your teammate wrote 4,017 lines and has to review whatever you send him three
days before the round.

---

## Step 1 · Copy two files in

```
src/api.js            the network layer + shape translation
src/useIncidents.js   live data, polling, cold-start handling, fallback
```

Nothing else in the repo changes.

---

## Step 2 · Two lines in `App.jsx`

**Add the import** near the top, with the other imports:

```js
import { useIncidents } from "./useIncidents";
```

**Then find this line** (around line 3408, first line inside `App()`):

```js
const [incidents, setIncidents] = useState(initialIncidents);
```

**Replace it with:**

```js
const { incidents, setIncidents, source, waking } = useIncidents(initialIncidents);
```

That's the whole integration. Every existing component keeps working untouched,
because `api.js` translates the backend's shape into the one they already read.

`initialIncidents` stays exactly where it is — it becomes the fallback when the
API is unreachable, so the dashboard never goes blank on venue wifi.

---

## Step 3 · Point it at the backend

Create `.env` in the repo root:

```
VITE_API_URL=https://aapdaai-backend.onrender.com
```

It defaults to that anyway, so this is only needed if you run the backend
locally.

---

## Step 4 · Prove it worked

```
npm install
npm run dev
```

The dashboard should show **real incidents from WhatsApp**, not Rahul Das and
Amit Kumar. Send a message to the WhatsApp number and watch it appear within
ten seconds.

---

## What this deliberately does NOT do

**It doesn't show `confidence` yet.** The mapping carries it through on every
incident (`incident.confidence`, `incident.confidenceLabel`) but no component
displays it. That's the next change, and it's the one worth making — it's the
thing that separates an unverified rumour of a collapse from a confirmed one,
and this UI currently cannot express the difference.

**It doesn't touch the map.** `LiveMapPage` is still two pins at hardcoded CSS
percentages. `leaflet` and `react-leaflet` are already in `package.json` and
never imported — and every incident now arrives with real `lat`/`lng`. That's
the highest-visual-impact change available and it's unblocked.

**It doesn't wire the report form or the verify buttons.** `api.js` has
`submitReport()` and `verifyIncident()` ready; nothing calls them yet.

---

## Two things to show honestly

`source` is `"live"` or `"demo"`. Put it somewhere visible — a small chip in the
header. Presenting seeded data as live is the one mistake you cannot recover
from if a judge notices.

`waking` is true while the free instance starts (~50 seconds after 15 minutes
idle). Show "starting the server…" rather than an error, because it isn't one.
