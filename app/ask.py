"""Deciding the one question worth asking next.

THE IDEA
    Don't run a form. Read what the person already said, work out the single
    most valuable thing you still don't know, and ask only that - with options,
    because someone standing in floodwater will tap "2" but will not compose a
    sentence.

    "We are 200 hostelers stuck here" already answers 'how many'. Asking it
    again wastes the one reply you might get. What you actually don't know is
    *stuck how* - and fire, flood and collapse need completely different
    resources.

TWO RULES
    1. NEVER BLOCK. The report is already usable. Questions refine an estimate;
       they are not a gate in front of it. If nobody ever answers, the incident
       still exists, still gets scored, still reaches an officer.

    2. ONE QUESTION. Not a list. People answer one thing and abandon five.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import Need


@dataclass
class Question:
    slot: str
    text: str
    options: list[str]
    # Higher = ask sooner. Ordered by how much the answer changes the response,
    # not by how curious we are.
    value: int

    def render(self) -> str:
        lines = [self.text]
        for i, option in enumerate(self.options, start=1):
            lines.append(f"{i}. {option}")
        lines.append("")
        lines.append("Reply with the number, or describe it in your own words.")
        return "\n".join(lines)


HAZARDS = ["fire", "flood", "collapse", "earthquake", "storm", "other"]

_HAZARD_Q = Question(
    slot="hazard",
    text="What has happened where you are?",
    options=["Fire", "Flood / water", "Building collapse",
             "Earthquake", "Storm / cyclone", "Something else"],
    value=100,
)

_NEED_Q = Question(
    slot="deficits",
    text="What do you need most urgently right now?",
    options=["Rescue / evacuation", "Medical help", "Drinking water",
             "Food", "Shelter"],
    value=90,
)

_INJURED_Q = Question(
    slot="injured",
    text="Is anyone there injured?",
    options=["No one", "1 or 2", "3 to 10", "More than 10"],
    value=80,
)

_VULNERABLE_Q = Question(
    slot="vulnerable",
    text="Is anyone there unable to get out on their own?",
    options=["Young children", "Elderly people", "A pregnant woman",
             "Someone who can't walk", "No — everyone can move"],
    value=85,          # just below 'what do you need', above 'how many'
)

_ACCESS_Q = Question(
    slot="access_blocked",
    text="Can a vehicle still reach you by road?",
    options=["Yes, the road is clear", "No, the road is blocked",
             "I don't know"],
    value=60,
)

_HEADCOUNT_Q = Question(
    slot="headcount",
    text="Roughly how many people are with you?",
    options=["Under 10", "10 to 50", "50 to 200", "More than 200"],
    value=70,
)

_LOCATION_Q = Question(
    slot="location",
    text=("We need your location to send help. Tap the attachment icon (📎), "
          "choose *Location*, then *Send your current location*."),
    options=[],
    value=1000,          # nothing else matters until we can find them
)


def next_question(need: Need, asked: set[str] | None = None) -> Question | None:
    """The single most valuable thing we still don't know.

    Ordered by how much the answer changes what gets sent - which is why
    'what happened' beats 'how many', and why both lose to 'where are you'.

    `asked` is every slot we've already put to this person, whether or not
    they answered it. Without it, a question they couldn't answer stays top of
    the list forever and the conversation dead-ends there.
    """
    asked = asked or set()
    candidates: list[Question] = []

    if not need.has_location:
        candidates.append(_LOCATION_Q)

    # Someone is stuck and we don't know what from. This is the question that
    # decides boats vs cutting gear vs a fire engine.
    if (need.trapped or "rescue" in need.deficits) and not need.hazard:
        candidates.append(_HAZARD_Q)

    if not need.deficits:
        candidates.append(_NEED_Q)

    # Only worth asking once we know something is wrong there.
    if need.injured is None and (need.trapped or need.hazard
                                 or need.deficits):
        candidates.append(_INJURED_Q)

    # Only once we know something is actually wrong - asking this of someone
    # who just said "hello" is intrusive and useless.
    if not need.vulnerable and (need.trapped or need.hazard or need.deficits):
        candidates.append(_VULNERABLE_Q)

    if need.headcount is None:
        candidates.append(_HEADCOUNT_Q)

    if need.access_blocked is None and (need.trapped or need.deficits):
        candidates.append(_ACCESS_Q)

    candidates = [q for q in candidates if q.slot not in asked]
    if not candidates:
        return None
    return max(candidates, key=lambda q: q.value)


# --- reading the answer ------------------------------------------------------

_HAZARD_BY_INDEX = ["fire", "flood", "collapse", "earthquake", "storm", "other"]
_NEED_BY_INDEX = ["rescue", "medical", "water", "food", "shelter"]
_INJURED_BY_INDEX = [0, 2, 6, 15]        # midpoints, honest guesses
_HEAD_BY_INDEX = [5, 30, 120, 300]


def _question_for_slot(slot: str) -> Question | None:
    return {"hazard": _HAZARD_Q, "deficits": _NEED_Q, "injured": _INJURED_Q,
            "headcount": _HEADCOUNT_Q, "access_blocked": _ACCESS_Q,
            "vulnerable": _VULNERABLE_Q}.get(slot)


def apply_answer(need: Need, slot: str, reply: str, use_llm: bool = True) -> bool:
    """Fold a reply to a known question back into the report.

    Three attempts, in this order:

      1. a tapped number      "2"
      2. an obvious keyword   "flood"
      3. the model            "the water is up to my waist"

    The model is genuinely last. A tapped "2" and a typed "flood" must never
    depend on an API being reachable, and calling one for an answer the rules
    already handle is latency spent for nothing - inside a webhook Meta is
    timing.
    """
    if _apply_rules(need, slot, reply):
        return True

    if not use_llm:
        return False

    question = _question_for_slot(slot)
    if not (question and question.options):
        return False

    from app import llm
    choice = llm.interpret_answer(question.text, question.options, reply)
    if not choice:
        return False

    print(f"[ask] model read {reply!r} as option {choice} "
          f"({question.options[choice - 1]})")
    return _apply_rules(need, slot, str(choice))


def _apply_rules(need: Need, slot: str, reply: str) -> bool:
    """Numbers and obvious keywords only. No network."""
    text = reply.strip().lower()
    index = None
    if text[:1].isdigit():
        try:
            index = int(text.split()[0].strip(".)")) - 1
        except ValueError:
            index = None

    if slot == "hazard":
        if index is not None and 0 <= index < len(_HAZARD_BY_INDEX):
            need.hazard = _HAZARD_BY_INDEX[index]
            return True
        for hazard in HAZARDS:
            if hazard in text:
                need.hazard = hazard
                return True
        if "water" in text or "flooded" in text:
            need.hazard = "flood"
            return True

    elif slot == "deficits":
        if index is not None and 0 <= index < len(_NEED_BY_INDEX):
            if _NEED_BY_INDEX[index] not in need.deficits:
                need.deficits.append(_NEED_BY_INDEX[index])
            return True

    elif slot == "injured":
        if index is not None and 0 <= index < len(_INJURED_BY_INDEX):
            need.injured = _INJURED_BY_INDEX[index]
            return True

    elif slot == "headcount":
        if index is not None and 0 <= index < len(_HEAD_BY_INDEX):
            need.headcount = _HEAD_BY_INDEX[index]
            return True

    elif slot == "vulnerable":
        by_index = ["children", "elderly", "pregnant", "disabled", None]
        if index is not None and 0 <= index < len(by_index):
            picked = by_index[index]
            if picked and picked not in need.vulnerable:
                need.vulnerable.append(picked)
            return True                # option 5 is a real answer: nobody
        for category, words in {
                "children": ["child", "kid", "baby", "bachch"],
                "elderly": ["elder", "old", "buzurg", "budha"],
                "pregnant": ["pregnan", "garbh"],
                "disabled": ["disab", "walk", "wheelchair", "viklang"]}.items():
            if any(w in text for w in words):
                if category not in need.vulnerable:
                    need.vulnerable.append(category)
                return True
        if text.startswith("no") or "everyone can" in text:
            return True

    elif slot == "access_blocked":
        # "I DON'T KNOW" IS AN ANSWER, AND IT IS CHECKED FIRST.
        #
        # Two bugs lived here.
        #
        # The question offers three options and this branch handled two, so a
        # tapped "3" fell through to False and the person was told "Sorry, I
        # didn't catch that" - for choosing an option we printed ourselves.
        # Twice, and then we gave up on them.
        #
        # Worse, and only found by testing every phrasing: "not sure" and
        # "no idea" both start with "no", so the check below recorded them as
        # THE ROAD IS BLOCKED. Someone saying they don't know would have sent
        # a rescue team round a detour that was never needed. Uncertainty must
        # be read before yes/no, not after.
        #
        # access_blocked stays None: we asked, and nobody knows. That is not
        # the same as "the road is clear" and an officer should see it.
        if index == 2 or any(w in text for w in _DONT_KNOW):
            return True
        if index == 0 or "clear" in text or text.startswith("yes"):
            need.access_blocked = False
            return True
        if index == 1 or "block" in text or text.startswith("no"):
            need.access_blocked = True
            return True

    return False


# "I don't know", in the languages people actually reply in. Not knowing is a
# legitimate answer to every question here and must never read as a failure.
_DONT_KNOW = (
    "don't know", "dont know", "do not know", "not know", "no idea",
    "unsure", "not sure", "can't say", "cant say", "unknown",
    "pata nahi", "pata nhi", "nahi pata", "nhi pata", "malum nahi",
    "maalum nahi", "jana nahi", "janu nahi",       # Odia: jaṇā nāhିଁ
)


# --- what the hazard changes -------------------------------------------------

# The whole reason for asking. Same "200 trapped", four different responses.
HAZARD_RESOURCES = {
    "flood": ["boats", "rescue_teams"],
    "collapse": ["heavy_rescue", "ambulances"],
    "earthquake": ["heavy_rescue", "ambulances"],
    "fire": ["fire_trucks", "ambulances"],
    "storm": ["rescue_teams"],
}


def hazard_note(need_or_hazard) -> str:
    """One line an officer can act on differently depending on the hazard."""
    hazard = (need_or_hazard if isinstance(need_or_hazard, str)
              else getattr(need_or_hazard, "hazard", ""))
    return {
        "flood": "Cut off by water - boats/high-clearance vehicles, not just teams.",
        "collapse": "Under structure - heavy rescue and cutting equipment.",
        "earthquake": "Structural - heavy rescue; expect more sites nearby.",
        "fire": "Fire - fire service leads; stage ambulances at a distance.",
        "storm": "Storm - expect blocked roads and downed lines en route.",
    }.get(hazard, "")
