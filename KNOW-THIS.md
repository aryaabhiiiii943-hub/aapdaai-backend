# Everything you should know before you walk in

Backend, end to end. What it does, why each decision was made, and what to say
when someone pushes.

Read the four boxed answers at the end even if you skip everything else.

---

## 1 · The one sentence

> People in a disaster already send messages. We turn those scattered messages
> into a ranked queue of incidents an officer can act on — and then we check
> whether help actually arrived.

If you only get one line out, make it that. Every part of the system is one of
those three verbs: **collect, structure, verify.**

---

## 2 · The problem, stated properly

When a disaster hits, information does not arrive as a database. It arrives as:

- a hundred WhatsApp messages saying roughly the same thing
- half of them with no location
- some exaggerated, some panicked, a few wrong
- no way to tell one flood reported ten times from ten separate floods

A District Emergency Operations Centre (DEOC) runs 24×7 in every Odisha
district. Its problem is not a shortage of reports. It is that reports arrive
unstructured and unranked, and nobody can tell which are real.

**Evidence this is real, not invented:**

- **Kerala, 2018.** Citizens built keralarescue.in and the Amrita Kripa app
  themselves because official channels couldn't cope. Contemporary accounts
  note that as landline and mobile networks faltered, conventional
  communication proved inadequate.
- **Darjeeling DEOC already runs a 24×7 WhatsApp control room.** So the channel
  we chose is not hypothetical — a district in India does this today. We're
  proposing the software layer they don't have.

**Why WhatsApp:** ~500 million users in India. No install, no training, no
literacy barrier, works on a feature phone, and people already have it open. A
new app is the one thing nobody downloads during a flood.

---

## 3 · The core idea — two gates

This is the piece to have straight, because it's the design decision everything
else follows from.

```
GATE 1   Government confirms THAT a disaster occurred
GATE 2   Citizens report WHERE and HOW MUCH
```

We do not ask citizens to prove a cyclone happened — IMD and the state say
that. We ask them the one thing only they know: *what is happening at your
exact spot, and how many of you are there.*

That split is why the system doesn't need to be a rumour detector. It needs to
be good at **locating and quantifying** need inside an event that is already
confirmed.

---

## 4 · End to end, in order

```
WhatsApp message
      │
      ▼
POST /webhook ──────► store raw, unmodified, dedup by message id
      │
      ▼
extract   rules first, LLM only for what rules missed
      │
      ▼
merge     everything one person said → one report
      │
      ▼
cluster   reports within 400 m and 3 hours → one incident
      │
      ▼
score     severity (how bad if true) + confidence (how sure)
      │
      ▼
match     nearest available unit of the right kind
      │
      ▼
officer   verifies, dispatches
      │
      ▼
notify    every reporter is told which unit was sent
      │
      ▼
ASK       "did help reach you?" ──► their answer overrides our record
```

**The whole pipeline is ~3,900 lines of Python across 19 files, with 34 tests.**

---

## 5 · Every component, and why

### 5.1 Intake — `main.py`, `intake.py`

Meta POSTs every incoming message to `/webhook`. We return **200 immediately**
and do the work in a background task.

**Why:** Meta retries anything that doesn't answer fast. Slow processing would
turn one message into four duplicates.

**Dedup lives in a `UNIQUE` constraint on `wa_message_id`, not in Python.**

> This was a real bug. The first version kept seen IDs in a Python set, which
> died with the process. Every restart re-processed everything. A database
> constraint survives restarts, and it's the database's job anyway.

This matters more than it sounds: the free tier sleeps after 15 minutes, and
Meta re-sends what it couldn't deliver during the wake-up. Dedup is what makes
that harmless.

### 5.2 Raw storage — `db.py`

Every message is stored **exactly as received**, before any parsing.

**Why:** parsing improves. If we stored only our interpretation, every parser
fix would be unable to reach messages already received. Incidents are
**recomputed from raw messages on every read** — so a better parser
retroactively improves every report ever sent.

**Trade-off, and say it before they find it:** recomputing everything on each
request is fine at hundreds of messages and wrong at hundreds of thousands. The
fix then is to cache the derived incidents, not to mutate them in place. Raw
stays the single source of truth either way.

### 5.3 Extraction — `extract.py` (379 lines)

Pulls headcount, injured, trapped, needs, location and hazard out of free text
in **English, Hindi and Odia**.

**Rules run first. The LLM only sees what rules couldn't fill.**

**Why that order — this is a strong answer, use it:**

- rules are deterministic, free, instant, and testable
- an LLM that is down or rate-limited must not stop a disaster system
- the LLM cannot overwrite something a rule already established

Groq (`llama-3.3-70b-versatile`) fills gaps; `whisper-large-v3` transcribes
voice notes — which matters, because a person in water talks rather than types.

**If asked "so is it even AI?":**

> The intelligence is in the aggregation, not the parsing. Deciding that six
> messages are one incident, scoring how believable it is, and computing what
> to send — that's the system. The LLM is a fallback for messy text, and it's
> optional by design.

**A bug worth telling, because it shows how you test:**

`"we are 190 people, 1 injured"` came out as headcount **1**. The
number-proximity search measured distance from the *start* of the number, so
"190" was closer to the word "injured" than "1" was. Fixed to measure
edge-to-edge.

### 5.4 Merge — one person, one report

Everything one phone sends inside 30 minutes folds into a single report.
Later messages win for anything they actually state, but **never overwrite a
known value with a blank**. Deficits accumulate — "no food" after "no water"
means both, not a correction.

**Why:** five messages from one phone is one voice, not five. This is what
stops a single panicking person from looking like a crowd.

### 5.5 Clustering — `incident.py`, `store.py`

Two reports join the same incident if they are within **400 metres** and
**3 hours** of each other.

**Why those numbers:** 400 m is roughly the distance at which people describe
the same event with different landmarks — one says "near the temple", another
"behind the school", same flood. 3 hours is how long a situation stays
recognisably the same one.

**Defend it honestly:** they're judgement calls, tunable in one place, and the
right values would come from real incident data we don't have. Saying that is
stronger than pretending they're derived.

**Across reporters, quantities are combined by MEDIAN, not sum.**

> Three people describing one crowd of 200 say 150, 200 and 300. Summing gives
> 650 people who don't exist. The median gives 200. Median also throws out one
> panicked exaggeration without needing to decide who was lying.

### 5.6 Severity — `compute.py`

**0–100: how bad this is IF TRUE.** Built from:

| Input | Weight |
|---|---|
| worst deficit | rescue 40 · medical 35 · water 30 · food 20 · shelter 20 |
| headcount | up to 20 |
| injured | up to 20 |
| trapped | up to 20 |
| people who can't self-evacuate | up to 15 |
| **age of an unattended incident** | **up to 15** |

**Rescue outranks everything: a trapped person has hours, a thirsty person has
a day.**

**Vulnerability is not a sympathy weighting.** A group with children, elderly,
pregnant women or people who can't walk takes longer to move and needs more
hands. Same headcount, harder rescue — so it goes higher in the queue.

**Age makes severity go UP. This is a deliberate correction and a good thing to
raise yourself:**

> The existing site decayed priority toward zero over 20 hours — which says an
> unattended collapse becomes *less* urgent the longer nobody goes. That's the
> wrong sign. Unmet need gets louder, so ours climbs 2 points an hour, capped
> at 15.

### 5.7 Confidence — the one to lead with

**Separate number. Never mixed with severity.**

```
confidence = 1 − ∏ (1 − trust of each DISTINCT reporter)
```

| Source | Trust |
|---|---|
| official (government body) | 0.95 |
| responder (NDRF/SDRF, identified) | 0.80 |
| web form on our own site | 0.40 |
| WhatsApp / SMS (public) | 0.30 |
| scraped social media | 0.10 |

**Why this formula:** if one anonymous reporter is right 30% of the time, the
chance three *independent* people are all wrong about the same thing at the
same place is 0.7³ ≈ 34%. So confidence ≈ 66%. It rises with independent
corroboration and **never with repetition** — that's why merge-per-reporter
happens first.

**The line that sells it:**

> A single anonymous report of a building collapse with 40 trapped is
> **severity 95, confidence 0.30**. Send someone to look — don't send
> everything. One priority number physically cannot say that.

Social media is 0.10 on purpose: in a real disaster it is unverified and
sometimes deliberately adversarial.

### 5.8 Resource quantities — `compute.py`

People report what they can see. **The system computes what it means.**

> A man standing in floodwater can tell you there are 200 people and no
> drinking water. He cannot tell you that's 600 litres for the first day.

| Figure | Value | Source |
|---|---|---|
| water, sustained | 15 L/person/day | **Sphere Handbook** (cited) |
| water, first response | 3 L/person/day | survival minimum (cited) |
| food | 1 pack/person/day | our assumption |
| shelter | 1 tent / 5 people | our assumption |
| ambulance | 1 per 4 injured | our assumption |
| rescue team | 1 per 10 trapped | our assumption |
| tanker | 5,000 L | our assumption |

**Know which are cited and which are ours.** If a judge asks where
"1 ambulance per 4 injured" comes from, the answer is *"that's our assumption,
written down in `compute.py` so it can be challenged and changed in one
place"* — not a bluff. Being able to separate the two is the credibility.

**Capacity is FLAGGED, not capped:**

> 200 people trapped genuinely implies 20 rescue teams. A district can field
> about 4. Capping the number at 4 would quietly turn 200 trapped people into
> 40, and the officer would never learn the requirement is beyond them. So the
> requirement stands and we say plainly that it exceeds district capacity and
> needs state escalation. **That is what escalation is.**

### 5.9 Dispatch matching — `resources.py`

An inventory of real unit types — ambulances, rescue teams, boats, fire
tenders, supply vehicles — each with a location, an organisation and a status.

Hazard maps to equipment (flood → boat, fire → fire tender), casualties always
add an ambulance, and it picks the **nearest available** unit.

**Why it matters:** *"send 2 ambulances"* is a requirement. *"send Capital
Hospital Ambulance 04, 4.2 km away"* is a decision. The difference is an
inventory. Assigned units leave the pool, so the next incident is offered a
different one — you cannot double-book a vehicle.

Where inventory falls short it reports a **shortage** rather than silently
recommending less than is needed.

### 5.10 Conversation — `ask.py`, `pipeline.py`

If a report can't be acted on, we ask **one** question, with tappable options,
derived from what they already said.

Rules that took real work:

- **One question outstanding at a time.** A new message used to trigger a
  *different* question, which is nagging, not patience.
- **Give up after two misses.** Someone in a flood asked the same thing three
  times puts the phone down.
- **Answers count however they arrive.** A shared location pin answers "where
  are you?" perfectly and arrives as a message with no text. That used to
  register as a failure to answer — so the system apologised for not
  understanding something the person had answered correctly.
- **Questions never block help.** The report is on the map and in front of an
  officer before any question is asked. Questions sharpen the estimate; they
  are not a gate.

### 5.11 Notification — `notify.py`, `send.py`

Officer gets a brief. Every reporter is told which unit was dispatched, by
name.

**The 24-hour window (error 131047) — know this cold:**

> WhatsApp forbids free-form messages to anyone who hasn't messaged you in the
> last 24 hours. It's an anti-spam rule. In production you use pre-approved
> template messages; for the demo, every phone must message the bot that
> morning.

If a message doesn't arrive during your demo, **this is almost certainly why**
— say so immediately rather than looking puzzled.

### 5.12 The assistance ledger — `assistance.py` — **your differentiator**

```
response    what WE did          pending → assigned → resolved
assistance  what THEY experienced unassisted → assisted → arrived
                                                        ↘ unreachable
```

25 minutes after dispatch, the system asks every reporter: *did help reach
you?* It reads yes/no in English and Hindi — `"nahi koi nahi aaya"` parses
correctly.

**"No" outranks the dispatch record and puts the incident back on the queue.**

`GET /unassisted` sorts by **how long people have waited, not by severity**.

> A small emergency nobody has touched for three hours is a worse failure than
> a large one being actively worked.

**The pitch line:**

> Of the 340 reports that came in on Tuesday, which ones did anyone actually
> reach? No control room can answer that today. Dispatch logs record what we
> sent, and a map that turns green when a truck leaves the depot is a map that
> lies. Only the people standing there know whether it arrived — so we ask
> them.

**A bug from this file worth telling, because it's the feature failing inside
itself:**

> SQLite returns booleans as `0`, and in Python `0 is False` evaluates to
> `False`. So the check silently never matched: the "nobody came" reply was
> recorded and never counted. The map would have turned green on a report where
> someone had just said nobody had come — the exact failure the feature exists
> to prevent, hiding inside the feature.

---

## 6 · Data model

Ten tables: `raw_messages`, `conversations`, `incidents`, `incident_reports`,
`verifications`, `resources`, `facilities`, `road_blocks`, `assignments`,
`arrival_checks`.

**The governing rule: persist DECISIONS, derive NUMBERS.**

Incident identity and human decisions are stored. Severity, confidence and
quantities are recomputed from the reports on every read.

**Why:** a stored severity drifts out of step with the reports underneath it.
A recomputed one cannot. And an officer's decision must still mean the same
thing tomorrow, so that gets a row.

**SQLite locally, Postgres in production**, chosen by whether `DATABASE_URL`
exists. One file (`db.py`) knows which is underneath; nothing else does.

**Why Postgres in production:** Render's free tier has no persistent disk. A
SQLite file is wiped on every deploy — along with every report and every
decision an officer made.

---

## 7 · Infrastructure

```
Backend    FastAPI + raw httpx, no vendor SDKs    Render, Singapore
Database   Postgres                                Render, Singapore
Dashboard  static site                             Render
WhatsApp   Meta Cloud API                          → /webhook
```

**Why raw `httpx` and no SDK:** three HTTP calls don't justify a dependency
that can break, and you can read exactly what goes to Meta.

**The infrastructure failure worth knowing**, because it's a good answer to
"what went wrong":

> The database region defaulted to Oregon while the service was pinned to
> Singapore. Render's internal hostnames only resolve within a region, so the
> app crashed on startup — but Render keeps the last working build serving when
> a deploy fails. **The API stayed up and looked healthy for a full day while
> nothing new was actually live.** A green health check is not proof that your
> code is running.

---

## 8 · What it does NOT do

Say these before a judge finds them. Every one of them raises your credibility.

- **No route optimization.** One `access_blocked` yes/no field. No alternate
  path computation. Do not claim it.
- **No authentication.** Anyone with the URL can act as an officer. Correct for
  a hackathon, disqualifying for deployment.
- **The LLM is optional**, and the system is deliberately weaker but working
  without it.
- **Clustering thresholds are judgement calls**, not derived from data.
- **Free tier sleeps.** ~50 second first load.
- **Never tested in a real disaster.** Nothing here has been near one.

---

## 9 · Four questions you will be asked

> **"How do you stop false reports?"**
>
> We don't block them — we score them. Confidence rises only with *independent*
> reporters, so one person messaging five times doesn't move it. Anonymous
> WhatsApp is 0.30; an official confirmation is 0.95. A single unverified
> report of something severe shows as high severity, low confidence — which
> tells the officer to send one unit to look, not everything. And every report
> carries the number it came from, so a repeat fabricator is traceable.

> **"Isn't this just a form / a chatbot?"**
>
> A form gives you 340 rows. This gives you a ranked queue of *distinct
> incidents*, each with how many people, what they need in litres and vehicles,
> how sure we are, which unit to send, and whether anyone got there. The work
> is the aggregation, not the collection.

> **"What if there's no internet?"**
>
> Then nothing that requires internet works, ours included — and we won't
> pretend otherwise. WhatsApp is the most degraded-network-tolerant channel
> available: text is tiny and it retries. SMS intake is in the data model as
> the next fallback. Kerala 2018 is the honest precedent — networks faltered
> and citizens still got messages out.

> **"Why should a District Collector use this instead of phone calls?"**
>
> Phone calls don't aggregate. Ten calls about one fire are ten calls. This
> merges them into one incident with rising confidence, and it can answer a
> question no control room can answer today: which reports has nobody reached
> yet.

---

## 10 · Numbers to have ready

```
400 m / 3 h        clustering thresholds
0.95 / 0.30 / 0.10 official / public / social trust
15 L               water per person per day (Sphere)
3 L                drinking-only first response
1 : 4              ambulances per injured (our assumption)
1 : 10             rescue teams per trapped (our assumption)
25 min             before asking "did help arrive?"
24 hours           WhatsApp free-form messaging window
34                 tests
~3,900             lines of Python, 19 files
10                 database tables
```

---

## 11 · The demo, in order

1. **Send a WhatsApp** — *"We are 200 people stuck at Patia, no drinking water,
   3 injured"*. Reply arrives, plus one follow-up question with options.
2. **Show the triage queue** — it's already there, ranked.
3. **Point at two incidents with the same severity and different confidence.**
   This is the moment. "Same badness. Very different certainty. One report
   versus four independent ones."
4. **Open it** — 200 people → 600 litres now, 3,000 litres a day, 1 ambulance,
   nearest unit named with distance.
5. **Confirm and dispatch.** The reporter's phone buzzes with the unit name.
6. **Press "Ask reporters: did help reach you?"** Reply **no** from that phone.
   The card jumps to the top bucket, the map circle gets a dashed red ring, the
   green counter stays at zero.
7. **Close on it:** *"Every system here can show you what was dispatched. This
   is the only one that can tell you it didn't arrive."*

---

## 12 · If something breaks on stage

- **No WhatsApp reply** → 24-hour window. Say it out loud, message the bot from
  that phone, continue.
- **Dashboard empty** → backend asleep. `POST /demo/simulate?clear=true`.
- **Everything slow** → free tier waking. Say so; it's a hosting choice, not
  the architecture.

**Open both URLs a minute before you start.**

---

## 13 · The thing to actually remember

Three sentences. If you have them, you can rebuild the rest live:

1. **Severity and confidence are separate numbers** — how bad if true, versus
   how sure we are.
2. **Confidence rises with independent voices, never with repetition.**
3. **A dispatch record says what we did. Only the people there know whether it
   arrived — so we ask them, and their answer wins.**
