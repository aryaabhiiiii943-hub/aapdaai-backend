# AapdaAI Backend Presentation Notes

This document is written for explaining the backend in front of judges. It is
not a production security report. AapdaAI is a hackathon project, so the goal is
to clearly show what was built, why the architecture makes sense, and what the
current limitations are.

---

## 1. One-Line Explanation

AapdaAI takes disaster reports from WhatsApp or a web form, converts scattered
messages into structured incident briefs, ranks them by severity and confidence,
and shows decision-makers what help may be needed.

The backend does not automatically dispatch emergency services. It gives
recommendations and records human decisions.

---

## 2. Problem We Are Solving

During disasters, people report emergencies from many places at the same time.
Control rooms face three hard questions:

1. Where is help needed?
2. Are multiple people reporting the same incident?
3. Which incidents are urgent, and how confident are we that they are real?

A normal helpline or dashboard may show reports one by one. AapdaAI tries to
turn those reports into a live operational picture.

---

## 3. High-Level Flow

```text
Citizen / Disaster Reporter
        ↓
WhatsApp or Web Form
        ↓
FastAPI Backend
        ↓
Raw Message Storage
        ↓
Extraction and Conversation Logic
        ↓
Incident Clustering
        ↓
Severity + Confidence Calculation
        ↓
Resource Recommendation
        ↓
Frontend Dashboard
        ↓
District Disaster Management Authority
```

Important point for judges:

The system treats WhatsApp and the web form as input channels. The core pipeline
after storage is the same.

---

## 4. Backend Tech Stack

| Layer | Technology |
|---|---|
| API server | FastAPI |
| Local database | SQLite |
| Deployed database | PostgreSQL on Render |
| HTTP client | httpx |
| Environment config | python-dotenv |
| Optional AI layer | Groq API for text extraction and voice transcription |
| Deployment | Render web service |

SQLite is used locally so development stays simple. On Render, `DATABASE_URL`
switches the same code to PostgreSQL.

---

## 5. Important Backend Files

| File | What It Does |
|---|---|
| `app/main.py` | FastAPI app, routes, WhatsApp webhook, dashboard API endpoints |
| `app/db.py` | Database schema and database connection wrapper |
| `app/config.py` | Reads environment variables in one place |
| `app/extract.py` | Converts raw WhatsApp/web payloads into structured `Need` objects |
| `app/models.py` | Defines the `Need` model and trust weights by source |
| `app/pipeline.py` | Orchestrates raw messages -> needs -> incidents -> briefs |
| `app/store.py` | Gives incidents stable IDs and stores verification decisions |
| `app/incident.py` | Incident clustering and distance logic |
| `app/compute.py` | Severity, confidence, and required resource calculations |
| `app/resources.py` | Seeded resource inventory and nearest available resource matching |
| `app/notify.py` | Builds WhatsApp messages for officers and reporters |
| `app/send.py` | Sends outbound WhatsApp messages through Meta Graph API |
| `app/intake.py` | Accepts reports from non-WhatsApp channels, like the web form |
| `app/assistance.py` | Tracks whether dispatched help actually reached people |
| `app/llm.py` | Optional AI fallback for extraction and transcription |
| `app/media.py` | Downloads WhatsApp media such as voice notes |
| `seed.py` | Inserts local sample WhatsApp-shaped messages |
| `render.yaml` | Render deployment blueprint |
| `tests/` | Backend tests for parsing, clustering, adversarial cases, and conversations |

---

## 6. What Happens When a WhatsApp Message Arrives

1. Meta sends a POST request to `/webhook`.
2. The backend extracts only messages addressed to our WhatsApp phone number.
3. Each message is stored in `raw_messages`.
4. The WhatsApp message ID has a unique constraint, so duplicate retries are not
   processed again.
5. The backend immediately returns `200 OK` to Meta.
6. Follow-up replies are handled in the background so Meta does not time out.

Why this matters:

Meta can retry webhook requests if the server is slow. So the backend stores
quickly, returns quickly, and does slower reasoning after the response.

---

## 7. Database Design

The database stores:

| Table | Purpose |
|---|---|
| `raw_messages` | Original incoming WhatsApp/web messages |
| `conversations` | What follow-up question was last asked to a reporter |
| `incidents` | Stable incident identity, location, confirmation, response status |
| `incident_reports` | Links derived reports to stable incident IDs |
| `verifications` | Append-only record of human decisions |
| `resources` | Seeded ambulances, rescue teams, boats, trucks, etc. |
| `facilities` | Seeded hospitals and shelters |
| `road_blocks` | Seeded blocked-road examples |
| `assignments` | Which resource has been assigned to which incident |
| `arrival_checks` | Whether people confirmed that help reached them |

Important design choice:

Raw messages are kept as the source of truth. Incident severity and confidence
are recomputed from reports, while human decisions and incident IDs are stored.

---

## 8. Extraction Logic

The backend first uses deterministic rules, not AI.

It tries to extract:

- location
- headcount
- injured count
- trapped count
- needs such as water, food, medical help, shelter, rescue
- vulnerable groups such as children, elderly people, pregnant women, or people
  who cannot walk

Why rules first?

Rules are fast, free, testable, and do not fail if an AI API is unavailable.
The optional AI layer only fills gaps when the rule parser could not understand
enough.

---

## 9. Merging Messages From One Person

A real WhatsApp report is often split into multiple messages:

```text
"200 people stuck, no water"
then
location pin
```

The backend merges messages from the same reporter within a time window. That
way, a text message and a location pin become one complete report.

This is important because a single WhatsApp message is often not enough to act
on.

---

## 10. Incident Clustering

Multiple people may report the same flood, fire, or collapse. AapdaAI groups
nearby reports into one incident instead of showing duplicates.

The backend clusters reports when they are close enough in space and time.

This prevents:

- ten reports of one fire becoming ten incidents
- confidence increasing because one person sent many messages
- resources being recommended repeatedly for the same event

---

## 11. Severity vs Confidence

This is one of the most important ideas in the project.

Severity means:

> How bad is this incident if it is true?

Confidence means:

> How sure are we that this incident is real?

Example:

```text
One anonymous report:
"Building collapsed, 40 people trapped"

Severity: high
Confidence: low
```

That means the incident is serious, but the authority may first send a ground
check instead of immediately committing all resources.

Confidence increases with independent reporters, not repeated messages from the
same phone number.

---

## 12. Resource Recommendation

The backend has a seeded resource inventory for the hackathon demo:

- ambulances
- rescue teams
- fire trucks
- boats
- heavy rescue units
- supply vehicles
- hospitals
- shelters
- road blocks

This is intentionally seeded data, not a live government feed.

The backend recommends the nearest available unit of the needed type. When a
unit is assigned, its status changes from `available` to `deployed`, so it is
not recommended again for another incident.

Judge-facing wording:

> For the hackathon, the inventory is seeded. In a real deployment, this table
> would be replaced by a live 108/ERSS/NDRF feed without changing the rest of
> the pipeline.

---

## 13. Human Decision Flow

The backend separates two concepts:

| Concept | Meaning |
|---|---|
| `confirmation` | Is this incident believed to be real? |
| `response` | Is anyone acting on it? |

Possible verification decisions:

- `confirmed`
- `rejected`
- `duplicate`
- `ground_check`

Why this matters:

A report can be severe but unconfirmed. A report can be confirmed but still
pending. These are different operational states.

---

## 14. Assistance Tracking

AapdaAI also asks:

> Did help actually reach the people?

When a resource is assigned, the system can message reporters later and ask if
help arrived. Their answer is stored separately from the dispatch record.

This avoids the false assumption that:

```text
resource dispatched = people helped
```

That distinction is valuable in a disaster response system.

---

## 15. API Endpoints To Mention

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check if backend is awake |
| `GET` | `/incidents` | Dashboard incident list |
| `GET` | `/follow-ups` | Incomplete reports needing one more answer |
| `POST` | `/reports` | Web/operator report intake |
| `POST` | `/webhook` | WhatsApp message intake |
| `POST` | `/incidents/{id}/verify` | Record officer decision |
| `POST` | `/incidents/{id}/assign` | Assign a resource |
| `GET` | `/resources` | List seeded resources |
| `GET` | `/facilities` | List seeded hospitals/shelters |
| `GET` | `/roadblocks` | List seeded road blocks |
| `GET` | `/stats` | Dashboard summary cards |
| `POST` | `/demo/simulate` | Create demo crisis data |

---

## 16. What Is Actually Implemented

Implemented:

- FastAPI backend
- WhatsApp webhook verification and message receive path
- raw message storage
- duplicate message protection using unique WhatsApp message IDs
- rule-based extraction
- optional LLM/Groq fallback
- voice note transcription path
- merging multiple messages from the same reporter
- incident clustering
- severity calculation
- confidence calculation
- seeded resource inventory
- nearest resource recommendation
- incident verification
- resource assignment
- reporter notification messages
- arrival confirmation logic
- dashboard API endpoints
- Render deployment configuration

Partially implemented:

- frontend/backend integration
- live resource visualization
- media handling
- arrival confirmation workflow
- map experience

Not implemented in this hackathon version:

- real authentication
- live government resource feeds
- SMS gateway
- OTP validation
- offline PWA
- real route optimization
- automatic dispatch without a human

---

## 17. Honest Limitations

These are good to say before judges ask:

1. This is a hackathon prototype, not an official emergency service.
2. The resource data is seeded, not live.
3. The backend recommends actions; it does not automatically dispatch.
4. Authentication is not production-ready yet.
5. The system stores sensitive reports, so a real deployment would need stronger
   access control, retention policy, and privacy controls.
6. Render free tier sleeps, so the first request can take time.

This honesty makes the project stronger because it shows we understand the
difference between a working prototype and a production emergency platform.

---

## 18. What To Emphasize In The Demo

Say this clearly:

> The strongest part of AapdaAI is not just receiving WhatsApp messages. The
> stronger idea is turning many scattered, incomplete, duplicate reports into a
> ranked decision queue.

Then show:

1. a WhatsApp or simulated report entering the backend
2. `/incidents` showing structured incident briefs
3. severity and confidence shown separately
4. multiple reports merging into one incident
5. resource recommendation from the seeded inventory
6. human verification or assignment from the dashboard

---

## 19. Suggested 2-Minute Backend Explanation

Use this almost word for word:

> The backend is a FastAPI service. It receives WhatsApp webhook messages from
> Meta and also accepts reports from the dashboard form. Every incoming report
> is stored first as a raw message, so we never lose the original information.
>
> After that, the pipeline extracts useful fields like location, number of
> people, injuries, trapped people, and needs such as water, medical help,
> shelter, food, or rescue. The first parser is rules-based so the system still
> works without an AI dependency. If a Groq API key is available, the AI layer
> can fill gaps or transcribe voice notes, but it is optional.
>
> The system then merges multiple messages from the same reporter. This matters
> because on WhatsApp, people often send the situation in one message and the
> location pin separately. After merging, reports are clustered into incidents,
> so ten reports of one flood become one incident with higher confidence instead
> of ten duplicate rows.
>
> For every incident, we calculate severity and confidence separately. Severity
> means how bad the situation is if true. Confidence means how sure we are,
> based on independent reporters and source trust. This helps the authority
> decide whether to dispatch immediately or first send a ground check.
>
> The backend also has a seeded resource inventory for the hackathon demo. It
> recommends the nearest available resource of the needed type. Once a resource
> is assigned, it is marked deployed so it is not recommended again. The final
> decision still belongs to the disaster management authority; our backend
> supports that decision with structured information.

---

## 20. Final Closing Line

> AapdaAI is built as a decision-support pipeline: receive reports, understand
> them, remove duplicates, rank urgency, estimate confidence, recommend
> resources, and keep the human authority in control.
