# Manual test pass

Work through this in order. About 20 minutes. It is also, deliberately, the
demo — if all of this works you can run the pitch off this document.

**Base URL:** `https://aapdaai-backend.onrender.com`
**Interactive version of everything below:** `/docs`

> **Wake it first.** The free instance sleeps after ~15 minutes and takes about
> 50 seconds to come back. Open `/health` and wait for `{"ok":true}` before
> starting, or your first test will look broken when it's only asleep.

---

## A · Is it actually alive

| Do | Expect |
|---|---|
| Open `/health` | `{"ok": true}` |
| Render → Logs, search `db ready` | **`[db] ready (postgres)`** |

If that second line says `aapdaai.db`, stop. Everything below will work and
then vanish on your next deploy.

**Seed the inventory** (safe to call twice):

```
POST /demo/seed
```

Expect `{"seeded": true, "resources": 26, "facilities": 8, "road_blocks": 2}`.

---

## B · WhatsApp intake, end to end

From your phone, to **+1 555 663-4460**:

**1.** Send: `we are around 200 people at the school, no water since morning, 3 injured`

- **Your phone** should get a reply asking for your location.
- Render logs should show `[recv] text from 91…`

**2.** Share your location (📎 → Location → Send current location).

- Your phone gets *"passed to the district disaster authority"*.
- Then a question — *"What has happened where you are?"* with numbered options.

**3.** Reply `2` (Flood / water).

- Next question arrives — injured, or vulnerable people.

**4.** Open `/incidents`.

```jsonc
{ "people": 200, "injured": 3, "hazard": "flood",
  "needs": ["water","medical"],
  "send": { "water_litres_now": 600, "ambulances": 1, ... },
  "dispatch": [ { "kind": "boat", "unit": "ODRAF Boat Unit …" } ],
  "severity": 70+, "confidence": 0.3, "confidence_label": "low" }
```

**What you are checking:** it read you correctly, it asked only what it didn't
know, and severity and confidence are separate numbers.

---

## C · The second channel

```
POST /reports
{ "text": "flooding at Chandrasekharpur, about 60 people, no drinking water, 2 elderly",
  "lat": 20.3300, "lng": 85.8090, "place": "Chandrasekharpur", "source": "web" }
```

Expect `trust_weight: 0.4` and `actionable: true`.

Now the same place as a **112 operator**:

```
POST /reports
{ "text": "caller reports 60 stranded at Chandrasekharpur school, no water",
  "lat": 20.3305, "lng": 85.8095, "source": "responder",
  "reported_by": "Operator R. Das" }
```

Expect `trust_weight: 0.8`.

**Then `/incidents`:** those two must be **ONE** incident with
`independent_reporters: 2` and `confidence ≈ 0.88`.

**What you are checking:** a form and a phone call, different weights, same
pipeline, correctly merged. This is the "channel, not architecture" claim being
true rather than asserted.

---

## D · The clustering moment — the one judges remember

Get a second phone (a teammate's) to message the number with a similar report
from a nearby location.

- `/incidents` must still show **one** incident, not two
- `independent_reporters` goes up
- `confidence` rises — 0.3 → 0.51 → 0.66
- `people` is the **median**, not the sum

**Then break it:** have them report from somewhere genuinely far away.
Two incidents. If that merges, something is very wrong.

---

## E · Dispatch and inventory

| Do | Expect |
|---|---|
| `GET /resources` | 26 units, `counts` available/deployed |
| `GET /resources?kind=boat&status=available` | just the free boats |
| `GET /facilities?kind=shelter` | 4 shelters with capacity/occupancy |
| `GET /roadblocks` | 2 |

**Then commit one:**

```
POST /incidents/1/assign     { "resource_id": <id from dispatch> }
```

- That unit's status becomes `deployed`
- `GET /incidents` now recommends a **different, further** unit
- `/stats` available drops by one

**What you are checking:** an allocation, not a suggestion. A committed vehicle
stops being offered to the next incident.

`POST /resources/{id}/release` puts it back.

---

## F · The human gate

```
GET  /incidents/1/preview            read the officer's message, send nothing
POST /incidents/1/notify             actually send it
POST /incidents/1/verify   { "decided_by": "DDMA Khordha / R. Das",
                             "decision": "confirmed" }
GET  /incidents/1/history            who decided, when, on what
```

- `confirmation` flips to `confirmed` on `/incidents`
- The audit row is there and is append-only

**The officer's phone must have messaged the number within the last 24 hours**,
or the send fails with `outside_24h_window: true`. That is a WhatsApp platform
rule, not a bug — and worth saying out loud before someone finds it.

Try `"decision": "ground_check"` too: confirmation goes to `verifying`, not
`confirmed`. Sending one person to look is not the same as believing the report.

---

## G · Follow-ups

```
GET /follow-ups
```

Send `help` from a phone that hasn't reported before. It should appear here
with `missing: ["location", …]` and the exact question to ask.

**What you are checking:** an incomplete report is a person needing one
question, not a record thrown away.

---

## H · The failure cases — ask these of yourself before a judge does

| Try | Should happen |
|---|---|
| Send the same message twice quickly | one report, not two (`UNIQUE` on message id) |
| Send 5 messages from one phone | `independent_reporters` stays **1** |
| Send only a location pin, no text | **no incident** — appears in `/follow-ups` |
| Send `we are fine, water is working` | no deficits, no incident |
| Answer a question with nonsense twice | apologises once, then moves to the next question |
| `POST /incidents/999/verify` | 404, not a crash |
| `POST /verify` with no `decided_by` | 422 — a decision without a name isn't a decision |

And the automated version of all of it:

```
pip install -r requirements-dev.txt
pytest -q          # 41 tests
```

---

## Before the 29th

- [ ] `[db] ready (postgres)` confirmed
- [ ] Officer's phone has messaged the number **that day** (24h window)
- [ ] `/health` hit a minute before presenting, so it isn't asleep
- [ ] `POST /demo/seed` run on the live instance
- [ ] Token is the permanent System User one, not a 24-hour temporary
