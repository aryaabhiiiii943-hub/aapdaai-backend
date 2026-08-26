# The strategy document vs. what actually exists

Read against `SIH 2026 Strategy Master Plan.pdf`, 25 August. Four days to the
internal round.

---

## 0. The divergence nobody has reconciled

**The plan never mentions WhatsApp. Not once.**

Its entire ingestion argument is a *web form*: "pinpoint browser GPS (±5 metres)",
"tiny JSON packets under 2 KB over degraded 2G", "offline-first PWA".

You built a **WhatsApp pipeline**. Different channel, different strengths, and
several of the plan's specific claims are false about it — WhatsApp needs data,
not 2G, and its location pin is not ±5 m browser GPS.

Neither is wrong. But somebody will read that document and then watch your demo,
and the two won't match.

**The reconciliation, and it's the honest one:**

> Web form and WhatsApp are two adapters onto the same pipeline. The form gives
> precise GPS and works offline as a PWA. WhatsApp needs no install, no URL, and
> is already on the phone of everyone in the country. We take both.

That sentence costs nothing and makes the document and the demo agree. **Have it
with your team before the 29th, not on it.**

---

## 1. Where you are AHEAD of the plan

Three things you built that the document doesn't even ask for, and each is
stronger than what it does ask for:

**Confidence, separate from severity.** The plan has one priority number
throughout. You have two, and the distinction — *how bad if true* vs *how sure
we are* — is the single most defensible idea in the project. It isn't in the
strategy at all. **Put it in.**

**Voice notes transcribed by Whisper.** The plan proposes the *Web Speech API*
for voice input, which needs a browser, a good connection and a supported
language. Whisper on a WhatsApp voice note works on any phone, auto-detects the
language, and you already built it.

**Corroboration-based trust.** The plan proposes "proximity upvoting" — actively
asking nearby citizens to confirm. You compute confidence from *independent*
reporters weighted by source trust, which needs no extra round trip and no
notification permission. Same goal, cheaper mechanism.

---

## 2. The plan promises it — YOU HAVE IT

| Claim in the document | Status |
|---|---|
| "Where to go first" — ranked priority | ✅ severity + banding |
| "How many citizens" | ✅ headcount, median across reports |
| "What exact supplies are required" | ✅ litres, packs, ambulances — Sphere-based |
| **Spatial-temporal clustering, 500 m / 10 min** | ✅ built, at 400 m / 3 h |
| "Deterministic core over unstable models" | ✅ exactly the architecture — rules first, model as fallback |
| React + FastAPI + OSM/Leaflet + SQLite | ✅ matches |
| Multilingual input | ✅ Hindi/Odia/Hinglish parsing + Whisper |
| Status updates back to the citizen | ✅ acknowledge + follow-up |
| Golden-hour triage | ✅ severity ranks medical and rescue above supplies |

Align the clustering numbers with the document, or change the document. 400 m /
3 h vs 500 m / 10 min is a detail a judge could catch.

---

## 3. The plan promises it — IT DOES NOT EXIST

Ordered by how likely a judge is to ask.

### Blocking the demo

**No resource inventory.** The plan promises *"which team to dispatch"*, the gap
analysis (*"Zone B needs 40 kits, stock is 10, shortage is 30"*), and NDRF
resource mapping. The dashboard has six layers for it. **Your backend has zero
resources.** Without this you can state a need but never name a unit — and the
shortage math the document leads with is impossible.

**No "Simulate Crisis" button.** The document is right that you must never type
mock reports live. You have `seed.py`, which is the logic — it just isn't a
button. One endpoint away.

**No vulnerable-demographics bonus.** The plan explicitly promises extra priority
when children, pregnant women, elderly or disabled people are reported. Your
scoring has no such term. Cheap to add, and it's a stated commitment.

### Asked about, not demoed

**No web form.** The plan's GPS-accuracy and 2G arguments both depend on it, and
it's ~30 minutes of work onto the existing API.

**No 112 operator screen.** The document's second-strongest argument is that 112
operators use this as their input console. Nothing addresses it — and it's just
the web form with a different label.

**No SMS parser.** `HELP FLOOD 4-PEOPLE MEDICAL` is in the plan. Needs a gateway.

**No offline PWA.** Promised, absent.

**No OTP validation.** Promised as the anti-spam measure.

**No admin panel for tuning weights.** Promised.

**No route guidance.** The comparison table claims "GIS map routing integrated
with hazard barriers" against the helpline's "rescuers risk blocked roads". You
have `access_blocked` from a follow-up question and nothing else. **This is an
overclaim in the document** — soften it or build road-block avoidance.

### Integrations — described, not built

112 ERSS · ISRO Bhuvan · IMD feeds · NDRF resource standards.

All fine to describe, provided you say "designed for" rather than "integrated
with". The adapter interface makes that credible.

---

## 4. Four days: what I'd actually do

**Must — the demo doesn't hold together without these**

1. **Resource inventory**, seeded. Unlocks "which team", the six map layers,
   available/deployed counts, and the shortage math.
2. **Simulate Crisis endpoint.** `POST /demo/seed` → 30 incidents across
   Bhubaneswar. Wire to a button.
3. **Vulnerable-demographics term** in severity. An hour, and it's promised.

**Should — cheap, and closes a stated argument**

4. **Web form** posting to the same pipeline. Doubles as the 112 operator screen.

**Say, don't build**

5. SMS, PWA, OTP, ERSS, Bhuvan, IMD, admin panel. The adapter interface is what
   makes "designed for" true rather than aspirational.

**Fix in the document**

6. Add WhatsApp as a channel.
7. Add confidence — your best idea is missing from your own strategy.
8. Soften "GIS routing with hazard barriers" to match reality.
9. Align 500 m / 10 min with the code, or the code with it.
