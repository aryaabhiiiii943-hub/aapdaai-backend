"""Is this thing actually configured? Run before and after deploying.

    python check.py

Every line either says what's working or names the exact thing to fix. Nothing
here changes any state - it only asks questions.

Run it on Render too (Shell tab). "Works on my laptop" is not the claim you
need on the 29th.
"""
from __future__ import annotations

import sys

import httpx

from app import config

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label:<26} {detail}")


def check_env() -> bool:
    print("\n--- configuration -------------------------------------------")
    required = {
        "WHATSAPP_TOKEN": config.WHATSAPP_TOKEN,
        "WHATSAPP_PHONE_NUMBER_ID": config.WHATSAPP_PHONE_NUMBER_ID,
        "WHATSAPP_VERIFY_TOKEN": config.WHATSAPP_VERIFY_TOKEN,
    }
    ok = True
    for name, value in required.items():
        if value:
            line(OK, name, f"set ({len(value)} chars)")
        else:
            line(BAD, name, "MISSING - required")
            ok = False

    if config.OFFICER_NUMBER:
        line(OK, "OFFICER_NUMBER", config.OFFICER_NUMBER)
    else:
        line(WARN, "OFFICER_NUMBER", "not set - officer demo won't work")

    if config.GROQ_API_KEY:
        line(OK, "GROQ_API_KEY", "set")
    else:
        line(WARN, "GROQ_API_KEY",
             "not set - rules parser only, no voice notes")
    return ok


def check_whatsapp() -> None:
    print("\n--- whatsapp ------------------------------------------------")
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID):
        line(BAD, "token", "can't test - credentials missing")
        return
    try:
        r = httpx.get(
            f"{config.GRAPH_API}/{config.WHATSAPP_PHONE_NUMBER_ID}",
            headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
            timeout=15.0)
    except Exception as err:                      # noqa: BLE001
        line(BAD, "token", f"{type(err).__name__}: {err}")
        return

    if r.status_code == 200:
        data = r.json()
        line(OK, "token", "valid")
        line(OK, "number",
             f"{data.get('display_phone_number','?')}  "
             f"({data.get('verified_name','')})")
    elif r.status_code in (400, 401):
        # The single most likely failure on demo day. Temporary tokens last
        # 24 hours and expire without warning.
        line(BAD, "token", "REJECTED - almost certainly expired. "
                           "Generate a new one, or make a permanent "
                           "System User token before the 29th.")
    else:
        line(BAD, "token", f"HTTP {r.status_code}: {r.text[:90]}")


def check_groq() -> None:
    print("\n--- groq (optional) -----------------------------------------")
    if not config.GROQ_API_KEY:
        line(WARN, "key", "not set - skipping")
        return
    from app import llm
    result = llm.read_message("do sau log fase hain, khana nahi hai")
    if result is None:
        line(BAD, "text model", "no usable reply - see the [llm] line above")
        return
    head = result.get("headcount")
    line(OK, "text model", f"replied {result}")
    if head == 200:
        line(OK, "hindi numbers", "'do sau' read as 200 - the rules parser "
                                  "cannot do this")
    else:
        line(WARN, "hindi numbers", f"expected 200, got {head}")


def check_pipeline() -> None:
    print("\n--- pipeline ------------------------------------------------")
    try:
        from app import pipeline
        from app.db import get_db, init_db
        init_db()
        with get_db() as conn:
            raw = conn.execute(
                "SELECT count(*) FROM raw_messages").fetchone()[0]
        line(OK, "database", f"reachable, {raw} raw message(s)")
        if raw == 0:
            line(WARN, "data", "empty - run `python seed.py` to test with "
                               "fixtures")
            return
        briefs = pipeline.briefs()
        line(OK, "incidents", f"{len(briefs)} built")
        for b in briefs[:3]:
            line(OK, f"  #{b['id']} {b['severity_band']}",
                 f"{b['place'][:30]}  sev={b['severity']} "
                 f"conf={b['confidence']}")
    except Exception as err:                      # noqa: BLE001
        line(BAD, "pipeline", f"{type(err).__name__}: {err}")


if __name__ == "__main__":
    print("AapdaAi configuration check")
    essential = check_env()
    check_whatsapp()
    check_groq()
    check_pipeline()
    print()
    if not essential:
        print("Required configuration is missing - fix the FAIL lines above.")
        sys.exit(1)
    print("Essentials present.")
