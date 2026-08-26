# Owning this code

4,100 lines. You need three things from it at the hackathon, and they're
different depths:

1. **Answer** — a judge or mentor asks how something works
2. **Change** — your team says "can it also do X", at 11pm
3. **Debug** — something breaks and you have twenty minutes

This is the map for all three. Read it once with the files open; come back to
section 4 when something's on fire.

---

## 1 · The twelve files, and when you'd touch each

Ordered the way a message travels, not alphabetically.

| File | Lines | What it does | You'd change it when… |
|---|---|---|---|
| `config.py` | 35 | every env var, one place | you add a new key or service |
| `db.py` | 238 | schema + the SQLite/Postgres switch | you add a table or column |
| `main.py` | 386 | **every HTTP route.** The only file the outside world touches | you add an endpoint |
| `models.py` | 159 | what a `Need` is; source trust weights | you track a new fact about a report |
| `extract.py` | 370 | message → structured facts. Rules, merging, LLM enrichment | the parser misreads something |
| `llm.py` | 185 | Groq text + Whisper. **Optional by design** | you change model or prompt |
| `media.py` | 44 | fetch voice notes/photos from Meta | rarely |
| `intake.py` | 108 | web form / 112 operator reports | you add another non-WhatsApp channel |
| `ask.py` | 287 | which question to ask next, and reading the answer | you add a follow-up question |
| `incident.py` | 177 | clustering; median aggregation | you change the 400 m / 3 h window |
| `compute.py` | 255 | severity, confidence, litres, shortage | **the scoring is challenged** |
| `resources.py` | 234 | inventory, nearest-available, assignment | you change what gets dispatched |
| `store.py` | 187 | incident identity + decisions, persisted | you add a decision type |
| `pipeline.py` | 251 | ties it together; the reply conversation | the follow-up flow changes |
| `notify.py` | 191 | the three outbound messages | the officer brief wording changes |

**If you remember one thing:** `main.py` is the only file with routes,
`compute.py` is the only file with judgement, and `extract.py` is the only file
that reads free text. Almost every question maps to one of those three.

---

## 2 · One message, file by file

Someone sends *"we are 200 hostelers stuck here"*, then a location pin.

```
main.py       webhook receives it, stores raw, answers 200 immediately
              → hands off to a background task, so Meta isn't kept waiting

extract.py    prepare()   voice note? transcribe it first
              extract()   "200" near "stuck" → trapped=200, headcount=200
              group_by_reporter()  folds the text and the pin into ONE report

pipeline.py   respond_to()  did they answer our last question?
                            is the report usable yet?
                            what's the most valuable thing we still don't know?

ask.py        next_question()  location beats hazard beats headcount
              → sends "What has happened where you are?" with options

store.py      assign()   is this near an incident that already exists?
                         yes → attach.  no → create one, with a stable id

incident.py   clustering + median across everyone who reported it

compute.py    severity()    how bad if true
              confidence()  how sure, from INDEPENDENT reporters
              resources_for()  180 people, no water → 540 L today

resources.py  which KIND of unit (flood → boat, fire → tender)
              nearest available of that kind
              shortage: need 20, have 4

notify.py     the six-line officer brief
main.py       GET /incidents serves all of it to the dashboard
```

---

## 3 · The eight questions you'll be asked

Learn these. Each answer has a file behind it, so you can open the code if
pushed.

**"Why would anyone WhatsApp instead of calling 112?"**
> Most will call. In a real disaster most won't get through — one operator
> handles one call, and telecom exchanges saturate. Text queues, clusters and
> ranks. Kerala 2018 is the evidence: as networks faltered, people fell back to
> messaging. → *strategy, not a file*

**"What if ten people report the same fire?"**
> One incident with ten reports. Clustered by 400 m and 3 hours. Otherwise the
> count lies and you send ten ambulances to one building. → `incident.py`

**"What if someone spams you?"**
> Confidence rises with *independent* reporters, not messages. A hundred from
> one phone is one voice. And nothing expensive moves until a named human
> confirms. → `compute.py: confidence()`, `store.py: verify()`

**"How do you know it's true?"**
> We don't, and we say so. Severity is how bad if true; confidence is how sure.
> They're separate numbers on purpose — an unverified report of a collapse and
> a confirmed minor one must not look alike. → `compute.py`

**"Where do the resource numbers come from?"**
> 15 litres per person per day is the Sphere standard; 3 litres is the drinking
> survival minimum for the first hours. Ambulances-per-injured is our own
> assumption, labelled as such in the file. → `compute.py`, top of file

**"Isn't the AI unreliable?"**
> It never decides anything. Rules parse first; the model only fills gaps and
> reads answers the rules can't. With no API key the service still runs, just
> reads less. → `extract.py: enrich()`, `llm.py`

**"Do you actually dispatch?"**
> No. We recommend to the DDMA, who are the statutory authority under the
> Disaster Management Act. The bottleneck was never that ambulances refuse to
> move — it's that the control room doesn't know where the need is for hours.
> → `notify.py`

**"What can't it do?"**
> Networks dying completely. No auth on the API. The inventory is seeded — no
> Indian state publishes live ambulance positions. Stuck vs buried are the same
> field. All four are honest and all four have a designed answer.

---

## 4 · When it breaks — symptom to file

| Symptom | Look at | Usually |
|---|---|---|
| Messages arrive, no reply sent | Render logs, filter `send` | token expired (24h temp), or 24-hour window |
| Nothing arrives at all | logs, filter `webhook` | subscription, or the app got unpublished |
| Reply is wrong / misread | `extract.py` | a keyword missing from the vocabulary |
| Two incidents that should be one | `incident.py` RADIUS_M | pins further apart than 400 m |
| One incident that should be two | same | genuinely close together — widen carefully |
| Confidence looks wrong | `compute.py: confidence()` | same reporter counted twice |
| Numbers vanish after deploy | Render logs, `db ready` | it's on SQLite, not Postgres |
| Dashboard shows nothing | browser console | CORS, or it's still on seeded data |
| Everything times out | `/health` | free instance asleep, 50s |

**The first move is always the same: read the logs and filter.** `[recv]`,
`[send]`, `[ask]`, `[llm]`, `[db]` — every consequential step prints one.

---

## 5 · Test yourself — no files open

If you can't answer one, that's the file to read.

1. Three people say 200, 150 and 180 people. What does the incident report, and why not 530?
2. Someone sends five messages. What happens to confidence?
3. A location pin arrives with no text. Does that create an incident?
4. The Groq key is missing. What still works and what stops?
5. Why is severity separate from confidence? Give the case that needs both.
6. What decides whether a flood gets boats or a collapse gets cutting gear?
7. Why does the reply happen *after* the 200 goes back to Meta?
8. Where is dedup implemented, and why not in a Python set?
9. What does `ground_check` do that `confirmed` doesn't?
10. Age makes severity go up. The live site makes it go down. Who's right?

---

## 6 · If you get 90 minutes before the 29th

**Do this, in order:**

1. Work through `TESTING.md` yourself. Not reading — doing.
2. Answer the ten questions above out loud, with the files closed.
3. Break something on purpose: change `RADIUS_M` to 50, re-run `pytest`, watch
   which tests fail and why. Change it back.

That third one is worth more than the first two. You learn what a thing is for
by watching its absence — and you'll have felt the failure before a judge asks
you to imagine it.
