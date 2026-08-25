# AapdaAi — ingestion service

Turns WhatsApp messages from people on the ground into a live picture of where
help is needed and how much.

**This service does not dispatch anything.** It produces recommendations for the
District Disaster Management Authority, who decide.

---

## Run it

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then fill it in
fastapi dev app/main.py
```

Health check: <http://127.0.0.1:8000/health>
Interactive docs: <http://127.0.0.1:8000/docs>

## Point WhatsApp at it

Meta cannot reach `localhost`, so tunnel it:

```
ngrok http 8000
```

Then in Meta → WhatsApp → Configuration:

- **Callback URL** — `https://<your-ngrok>.ngrok-free.app/webhook`
- **Verify token** — the same string you put in `.env`
- Subscribe to **messages**

Meta immediately sends a `GET /webhook`. If the token matches, it's verified.

---

## The pipeline

```
receive  ->  extract  ->  fill gaps  ->  cluster  ->  compute  ->  show  ->  confirm
  done       next        next           later      later      later    later
```

**Only `receive` exists so far**, and it deliberately does nothing clever:
store the message exactly as it arrived, answer 200 fast, move on.

Two decisions in there worth knowing:

- **Dedup is a `UNIQUE` constraint**, not a Python set. Meta retries slow
  requests; a restart must not reopen the door to duplicates.
- **The raw payload is kept forever.** When the parser turns out to be wrong,
  the original is the only thing that lets you re-run history.

---

## Layout

```
app/
  config.py   every environment variable, in one place
  db.py       schema + connection
  main.py     the webhook. Meta talks to this and nothing else.
  models.py   <- not written yet
```

## Next

`app/models.py` — what a Need is, and which facts we must have before it can be
acted on.
