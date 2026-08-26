# Wiring the dashboard to the backend

What the frontend at `aapat.freebuff.app` shows today, what the API actually
returns, and the gaps in both directions.

**The short version:** the backend knows things the dashboard has no way to show
(confidence, quantities, follow-ups), and the dashboard shows things the backend
has no data for (every resource layer). Neither is a small edit, and the second
is the bigger one.

---

## 1. What the API returns today

`GET /incidents` → `{"incidents": [...]}`, ranked worst first:

```jsonc
{
  "id": 1,
  "place": "Patia Govt High School",
  "lat": 20.355833, "lng": 85.819467,

  "people": 180, "injured": 2, "trapped": null,
  "needs": ["water", "medical", "food"],     // what they lack
  "hazard": "flood",                          // fire|flood|collapse|...  "" if unknown
  "access_blocked": false,

  "send": {                                   // computed, not reported
    "water_litres_now": 540,
    "water_litres_per_day": 2700,
    "food_packs": 180,
    "ambulances": 1
  },
  "exceeds_local_capacity": [],               // names of anything beyond a district

  "severity": 59, "severity_band": "high",    // how bad IF TRUE
  "confidence": 0.657, "confidence_label": "medium",   // how sure
  "reports": 3, "independent_reporters": 3,

  "confirmation": "unconfirmed",  // unconfirmed|verifying|confirmed|rejected
  "response": "pending",          // pending|assigned|in_progress|resolved
  "created_at": "2026-08-25T10:40:17Z"
}
```

Also live:

```
GET   /stats                        the four tiles
GET   /follow-ups                   reports we can't place + the question to ask
GET   /incidents/{id}/preview       the officer's message, without sending
POST  /incidents/{id}/notify        send it
POST  /incidents/{id}/verify        confirmed|rejected|duplicate|ground_check
PATCH /incidents/{id}               response status
GET   /incidents/{id}/history       audit trail
```

---

## 2. Frontend needs it, backend hasn't got it

### THE BIG ONE — there is no resource inventory

The dashboard has **six layers** for things the backend knows nothing about:

- Ambulances · Rescue Teams · Fire Trucks
- Hospitals · Shelters · Road Blocks

It also shows **"31 Available / 12 Deployed"**, a **Resource Density** heatmap,
and *"→ Mumbai Ambulance Service (16.1 km)"* — all of which need a table of
resources with locations that does not exist.

This is the single largest gap and everything else is cosmetic beside it.

**What it needs:** a `resources` table (id, name, kind, lat, lng, status, org),
a `facilities` table for hospitals and shelters with capacity, `road_blocks`,
and `GET /resources`, `/facilities`, `/roadblocks`.

Seeded data is fine and honest — no Indian state publishes live ambulance
positions. Say it's seeded.

### Smaller, quick

| Frontend field | Backend today | Fix |
|---|---|---|
| `title` — "Sector 17 building fire" | `place` only | derive a title from hazard + place |
| `type` — fire/flood/collapse | `hazard`, often `""` | fall back to worst deficit |
| `"2h ago"` | `created_at` | frontend formats it |
| `status` badge | **two** fields | see below |
| `P:35` | `severity` | rename, or read `severity` |

### The status mismatch — worth getting right

The dashboard has one status: `reported → acknowledged → in progress`.

The backend deliberately has **two**, because they answer different questions:

```
confirmation   is it real?          unconfirmed -> verifying -> confirmed / rejected
response       is anyone on it?     pending -> assigned -> in_progress -> resolved
```

"Acknowledged" tells you a human looked at it, not that it's true. Collapsing
them loses the distinction that lets an officer see *"probably catastrophic,
nobody has verified it"* — which is the case that matters most.

**Recommendation:** show both. A small grey chip for confirmation, the existing
coloured badge for response.

---

## 3. Backend has it, frontend ignores it

These are already computed and thrown away by the UI:

**`confidence` — the most important one.** The dashboard shows a single priority
number, so an unconfirmed rumour of a collapse and a triple-confirmed one look
identical. Put it next to severity. It is the thing that distinguishes this from
every other incident board.

**`send` quantities.** The panel says *"Suggested: ambulance"*. The backend says
*540 litres of drinking water today, 2 700 L/day, 180 food packs, 1 ambulance.*
Show the numbers.

**`exceeds_local_capacity`.** When the requirement is beyond a district, say so
loudly. It is the difference between a number and a decision.

**`/follow-ups`.** Reports that can't be placed, with the one question to ask
each person. There is no screen for this and there should be — it is live work
an operator can do.

**`independent_reporters`** — "3 independent reports" is *why* confidence is what
it is. Show it as the justification.

**Verification.** `POST /verify` and the audit trail exist and nothing calls
them. The confirm/reject buttons are the demo's turning point.

---

## 4. Delete from the frontend

Three claims on the landing page are pure liability:

- **"99.9% Uptime SLA"** — nobody promised anyone an SLA. Remove.
- **"10K+ Reports/Minute"** — never measured. Remove or relabel as a target.
- **"<30s Incident Processing"** — same.

And one wording fix that removes the biggest attack surface for free:

- **"Resource Optimization — algorithmic allocation engine"**
  → **"Resource Recommendation"**. You recommend; the DDMA allocates. One word,
  and it makes the claim true.

Also check the copy for **computer vision** and **weather/sensor ingestion** —
both are promised on the landing page and neither exists.

---

## 5. Order of work

**Do first — half a day, and it makes the demo coherent**

1. Point the dashboard's incident list and map at `GET /incidents`
2. Show `confidence` next to `severity`
3. Show the `send` quantities instead of "Suggested: ambulance"
4. Wire the confirm/reject buttons to `POST /verify`

**Then — the resource layers**

5. `resources` + `facilities` tables, seeded, plus the read endpoints
6. Nearest-available matching by kind, so the recommendation names a real unit

**Then — if time**

7. A follow-ups panel
8. Both status chips

**Cut**

9. The three performance claims and the "allocation" wording. Ten minutes,
   removes the easiest thing to attack.

---

## 6. One integration detail that will bite

The backend is on Render's **free tier**: it sleeps after ~15 minutes idle and
takes ~50 seconds to wake. The dashboard polling every 10 seconds will keep it
awake during the demo, but the *first* load after a quiet period will hang.

Handle it in the frontend: show "waking the server…" rather than an error, and
hit `/health` on page load before the first real request.
