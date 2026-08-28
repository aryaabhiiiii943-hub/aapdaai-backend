# AapdaAi — submission

Smart India Hackathon 2026 · disaster response · **backend: Abhinav**

---

## Live right now

| | |
|---|---|
| **Dashboard** | https://aapdaai-dashboard.onrender.com |
| **API** | https://aapdaai-backend.onrender.com |
| **API docs (interactive)** | https://aapdaai-backend.onrender.com/docs |
| **WhatsApp intake** | +1 555 663-4460 |

Both run on Render's free tier and sleep after 15 minutes idle. **Open them a
minute before reviewing** — the first request takes ~50 seconds to wake.

---

## Code

| | |
|---|---|
| **Backend** | https://github.com/aryaabhiiiii943-hub/aapdaai-backend · branch `main` |
| **Dashboard** | https://github.com/aryaabhiiiii943-hub/Aapda_AI · branch `backend-integration` |

The dashboard repo is a fork of a teammate's frontend. My work is the
`backend-integration` branch — six files, one of them a small edit to his
`App.jsx`. His `main` is untouched.

---

## What this actually does

A district control room gets flooded with scattered reports and no way to tell
which are real, which are the same event, or which anyone actually reached.

```
WhatsApp message  →  parse  →  merge per person  →  cluster into incidents
                                                          ↓
                              severity + confidence, computed separately
                                                          ↓
                          ranked queue  →  officer decides  →  unit dispatched
                                                          ↓
                            "has help reached you?"  →  the person answers
```

### Three things worth looking at

**1 · Severity and confidence are separate numbers.**
Severity is *how bad if true*. Confidence is *how sure we are*. One anonymous
report of a building collapse with 40 trapped is severity 95, confidence 0.30 —
send someone to look, don't send everything. A single priority score cannot
express that.

Confidence is `1 − ∏(1 − trust)` over **distinct** reporters, so it rises with
independent voices and never with repetition. Five messages from one phone
stay one voice.

**2 · Quantities are computed, not asked for.**
A man standing in floodwater can tell you there are 200 people and no drinking
water. He cannot tell you that's 600 litres for day one. So we ask him to
count, and do the arithmetic — against the Sphere Handbook's 15 L/person/day,
with our own assumptions written down in `app/compute.py` so they can be
argued with rather than guessed at.

**3 · The assistance ledger — `app/assistance.py`.**
`response` is what the control room *did*. `assistance` is what the person
there *experienced*. They are not the same, and conflating them is how a map
turns green because a truck was dispatched.

After a unit is assigned, the system asks every reporter whether help arrived.
"No" outranks the dispatch record and puts the incident back on the queue.
`GET /unassisted` sorts by how long people have waited, not by severity — a
small emergency nobody has touched for three hours is a worse failure than a
large one being actively worked.

**Of the reports that came in on Tuesday, which ones did anyone reach?** No
control room can answer that today.

---

## Reviewing it in five minutes

```
1.  https://aapdaai-backend.onrender.com/docs      wait for it to wake
2.  POST /demo/simulate?clear=true                 generates a realistic crisis
3.  https://aapdaai-dashboard.onrender.com         log in as Management Member
4.  Triage queue                                   ranked, worst first
5.  Open an incident                               confidence bar, dispatch, shortages
```

To see the intake path, WhatsApp **+1 555 663-4460** with something like
*"we are 200 people stuck at Patia, no drinking water"* — it will reply, ask
one follow-up question, and appear on the dashboard within ten seconds.

---

## Running it locally

```bash
git clone https://github.com/aryaabhiiiii943-hub/aapdaai-backend
cd aapdaai-backend
pip install -r requirements.txt
cp .env.example .env          # WhatsApp + Groq keys optional; it runs without them
uvicorn app.main:app --reload
pytest                        # 42 tests
```

No `DATABASE_URL` set means SQLite; setting one switches to Postgres. Nothing
else changes.

---

## Honest notes

Written down because a reviewer will find them anyway, and finding them listed
is better than finding them hidden.

- **The LLM is optional.** Rules parse first; Groq only fills gaps. Without an
  API key the system still works, slightly less well. This was deliberate — a
  disaster tool that dies when a vendor rate-limits you is not a disaster tool.
- **No route optimization.** There is one `access_blocked` yes/no field. The
  system does not compute alternate paths and does not claim to.
- **No authentication.** Anyone with the URL can act as an officer. Correct
  for a hackathon build, disqualifying for a real deployment.
- **Capacity is flagged, not enforced.** 200 people trapped really does imply
  20 rescue teams. We say so, and say plainly that it exceeds what a district
  can field, rather than quietly capping the number at something achievable.
- **Free tier.** Both services sleep. First load is slow.
