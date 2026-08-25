"""Turn a WhatsApp message into a Need.

WHY RULES BEFORE AI
    An LLM here would be a second API key, a second rate limit, and a second
    way for the demo to fail on stage. Most disaster messages are short and
    blunt - "we are 200 people, no water, 3 injured" - and a parser handles
    them for free, offline, every time.

    Same shape as the restaurant bot: a deterministic fast path first, the
    model only for what the parser can't read. `extract` returns what it is
    sure about and reports what it missed; the LLM layer plugs in later
    against that same gap list.

    Hindi, Odia and Hinglish matter more than elegance here. The keyword lists
    are long on purpose.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app import llm, media
from app.models import DEFICITS, Need

# --- vocabulary -------------------------------------------------------------
# Written the way people actually type in Odisha, not the way a dictionary does.

_PEOPLE = r"(?:people|persons?|log|loka|aadmi|admi|humans?|families|parivar|folks?)"
_INJURED = r"(?:injured|hurt|wounded|bleeding|ghayal|ghaayal|achot)"
_TRAPPED = r"(?:trapped|stuck|buried|phasa|phanse|fansa|atkey|atke)"

_DEFICIT_WORDS = {
    "water": ["water", "pani", "paani", "drinking water", "thirsty", "jala"],
    "food": ["food", "khana", "khaana", "hungry", "bhookha", "ration", "khadya"],
    "medical": ["medical", "medicine", "doctor", "injured", "hurt", "ghayal",
                "dawai", "davai", "first aid", "ambulance", "bleeding"],
    "shelter": ["shelter", "roof", "tent", "nowhere to go", "ghar nahi",
                "homeless", "sleeping outside"],
    "rescue": ["rescue", "trapped", "stuck", "boat", "bachao", "help us",
               "save us", "phasa", "drowning"],
}

# "no water" / "water nahi" / "without food" - a deficit is usually a negation
_NEGATORS = ("no ", "not ", "without ", "nahi", "nahin", "na ", "out of ",
             "ran out", "finished", "khatam", "sesh")


def _number_near(text: str, pattern: str, window: int = 25) -> int | None:
    """Find the number *closest* to a keyword.

    Nearest, not first. "hum 150 log hai, 2 ghayal" has two numbers; taking the
    first one inside the window made 'ghayal' (injured) read 150 instead of 2.
    Distance is the only signal we have about which number belongs to which
    word, so use it properly.

    We look both sides because word order in Hinglish is not reliable -
    "200 people", "people are 200" and "around 200 of us" all occur.
    """
    numbers = [(m.start(), m.end(), int(m.group(1)))
               for m in re.finditer(r"\b(\d{1,5})\b", text)]
    if not numbers:
        return None

    best: tuple[int, int] | None = None      # (distance, value)
    for m in re.finditer(pattern, text, re.I):
        for start, end, value in numbers:
            # Measure edge to edge. Measuring from the start of a number that
            # sits BEFORE the keyword punishes long numbers for being long:
            # in "we are 190 people, 1 injured", 190 scored 4 away from
            # "people" and the 1 scored 2, so headcount came out as 1.
            if end <= m.start():
                distance = m.start() - end
            else:
                distance = start - m.end()
            if distance < 0 or distance > window:
                continue
            if best is None or distance < best[0]:
                best = (distance, value)
    return best[1] if best else None


def _deficits_in(text: str) -> list[str]:
    """Which categories are mentioned as missing.

    A bare mention isn't enough - "we have water" must not become a water
    deficit. We look for a negation nearby, and treat an explicit plea
    ("need water", "send water") the same way.
    """
    found: list[str] = []
    low = text.lower()
    for category, words in _DEFICIT_WORDS.items():
        for word in words:
            idx = low.find(word)
            if idx == -1:
                continue
            context = low[max(0, idx - 25):idx + len(word) + 10]
            negated = any(n in context for n in _NEGATORS)
            pleaded = any(v in context for v in ("need", "send", "chahiye",
                                                 "want", "requir", "darkar"))
            # 'rescue' and 'medical' words are already a plea by themselves
            implicit = category in ("rescue", "medical")
            if negated or pleaded or implicit:
                found.append(category)
                break
    return found


def prepare(payload: dict) -> dict:
    """Make a non-text message readable, before extraction sees it.

    Voice notes become text. Everything downstream then treats them as if the
    person had typed it - which is the point: a woman standing in floodwater
    will send a voice note, not a paragraph, and her report should not be worth
    less because of it.

    Photos and videos keep their caption and are attached for the human
    verifier. We deliberately do not run damage classification on them: showing
    a responder the actual photo is most of the value, and a classifier that's
    wrong a third of the time is worse than no classifier at all.

    This function touches the network. `extract` does not - keep it that way,
    it's what makes extract testable.
    """
    kind = payload.get("type", "")
    if kind != "audio":
        return payload

    media_id = (payload.get("audio") or {}).get("id", "")
    audio = media.download(media_id)
    if not audio:
        return payload

    text = llm.transcribe(audio, filename=f"{media_id}.ogg")
    if not text:
        return payload

    print(f"[voice] transcribed {len(audio)} bytes -> {text[:60]!r}")
    prepared = dict(payload)
    prepared["type"] = "text"
    prepared["text"] = {"body": text}
    prepared["_transcript"] = text          # so we can show it in the UI
    return prepared


def extract(payload: dict) -> Need:
    """One WhatsApp message -> one Need.

    `payload` is the message object exactly as Meta sends it, which is what we
    stored in raw_messages. Nothing here reaches back out to the network.
    """
    need = Need(
        source="whatsapp",
        reporter=payload.get("from", ""),
        wa_message_id=payload.get("id", ""),
    )

    ts = payload.get("timestamp")
    if ts:
        need.observed_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)

    kind = payload.get("type", "")

    # A shared pin is the best location we will ever get. Take it and stop.
    if kind == "location":
        loc = payload.get("location", {})
        need.lat = loc.get("latitude")
        need.lng = loc.get("longitude")
        need.place_text = loc.get("name") or loc.get("address") or ""
        need.raw_text = f"[location] {need.place_text}".strip()
        return need

    if kind == "text":
        need.raw_text = payload.get("text", {}).get("body", "")
        need.transcript = payload.get("_transcript", "")   # set by prepare()
    else:
        # image / audio / video / sticker - we keep the caption if there is one
        need.raw_text = payload.get(kind, {}).get("caption", "") \
            if isinstance(payload.get(kind), dict) else ""

    text = need.raw_text
    if not text:
        return need

    need.injured = _number_near(text,_INJURED)
    need.trapped = _number_near(text,_TRAPPED)
    need.headcount = _number_near(text,_PEOPLE)

    # "we are 200" with no noun - common, and worth catching
    if need.headcount is None:
        m = re.search(r"\b(?:we are|hum|ame|there are|about|around|approx\w*)"
                      r"\s+(\d{1,5})\b", text, re.I)
        if m:
            need.headcount = int(m.group(1))

    need.deficits = _deficits_in(text)

    # An injured or trapped count implies the matching need even if unsaid.
    if need.injured and "medical" not in need.deficits:
        need.deficits.append("medical")
    if need.trapped and "rescue" not in need.deficits:
        need.deficits.append("rescue")

    return need


def extract_from_row(row) -> Need:
    """Convenience: a raw_messages row -> a Need."""
    return extract(json.loads(row["payload"]))


# --- the model, for what the rules couldn't read -----------------------------

def enrich(need: Need) -> Need:
    """Ask the model only about the gaps the parser left.

    Two deliberate choices:

    * The rules win where they fired. A regex that matched is inspectable and
      repeatable; a model's number is neither. The model fills nulls, it does
      not overwrite.
    * We only call it when something is actually missing. Most messages are
      blunt enough for the parser, so this is a handful of calls, not one per
      message - cheaper, faster, and fewer chances to fail.

    Any failure leaves `need` exactly as it was.
    """
    if not llm.available() or not need.raw_text:
        return need

    gaps = [f for f in ("headcount", "injured", "trapped")
            if getattr(need, f) is None]
    if not gaps and need.deficits:
        return need                              # parser got everything

    result = llm.read_message(need.raw_text)
    if not result:
        return need

    for name in ("headcount", "injured", "trapped"):
        if getattr(need, name) is not None:
            continue                             # rules already knew
        value = result.get(name)
        if isinstance(value, int) and 0 <= value <= 100_000:
            setattr(need, name, value)
            need.llm_fields.append(name)

    for d in result.get("deficits") or []:
        if d in DEFICITS and d not in need.deficits:
            need.deficits.append(d)
            if "deficits" not in need.llm_fields:
                need.llm_fields.append("deficits")

    place = result.get("place_text")
    if place and not need.place_text and isinstance(place, str):
        need.place_text = place[:120]
        need.llm_fields.append("place_text")

    return need


# --- one person, several messages -------------------------------------------
# WHY THIS EXISTS
#     Testing the seed data showed every single Need coming out incomplete: the
#     text messages had no location, and the location pins had no substance.
#     That isn't a bug in the parser - it's how WhatsApp works. You type the
#     situation, then share the pin as a *second* message.
#
#     So a Need is never one message. It's what one person told us across a
#     short conversation. Merge first, judge completeness after.

MERGE_WINDOW_SECONDS = 30 * 60


def merge(needs: list[Need]) -> Need | None:
    """Fold everything one reporter said into a single Need.

    Later messages win for anything they actually state; they never overwrite a
    known value with a blank. Deficits accumulate - saying "no food" after
    "no water" means both, not a correction.
    """
    needs = [n for n in needs if n]
    if not needs:
        return None

    ordered = sorted(needs, key=lambda n: n.received_at)
    merged = Need(
        source=ordered[0].source,
        reporter=ordered[0].reporter,
        observed_at=ordered[0].observed_at,
        received_at=ordered[-1].received_at,
        wa_message_id=ordered[-1].wa_message_id,
        first_message_id=ordered[0].wa_message_id,
    )

    texts: list[str] = []
    for n in ordered:
        if n.raw_text:
            texts.append(n.raw_text)
        if n.has_location:
            merged.lat, merged.lng = n.lat, n.lng
        if n.place_text and not merged.place_text:
            merged.place_text = n.place_text
        for attr in ("headcount", "injured", "trapped"):
            value = getattr(n, attr)
            if value is not None:
                setattr(merged, attr, value)
        for d in n.deficits:
            if d not in merged.deficits:
                merged.deficits.append(d)

    merged.raw_text = " | ".join(texts)
    return merged


def group_by_reporter(needs: list[Need]) -> list[Need]:
    """Merge each reporter's messages, splitting on long gaps.

    A gap longer than MERGE_WINDOW_SECONDS means a new episode - the same
    person reporting a different thing hours later is not the same report.
    """
    by_person: dict[str, list[Need]] = {}
    for n in needs:
        by_person.setdefault(n.reporter, []).append(n)

    out: list[Need] = []
    for messages in by_person.values():
        messages.sort(key=lambda n: n.received_at)
        batch: list[Need] = []
        for n in messages:
            if batch and (n.received_at - batch[-1].received_at
                          ).total_seconds() > MERGE_WINDOW_SECONDS:
                out.append(merge(batch))
                batch = []
            batch.append(n)
        if batch:
            out.append(merge(batch))
    return [n for n in out if n]
