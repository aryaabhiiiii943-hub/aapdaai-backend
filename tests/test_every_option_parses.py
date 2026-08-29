"""Every option we print must be an option we can read back.

THE BUG THIS EXISTS TO PREVENT

    Can a vehicle still reach you by road?
      1. Yes, the road is clear
      2. No, the road is blocked
      3. I don't know

    > 3

    Sorry, I didn't catch that.

The parser handled options 1 and 2. Option 3 fell through to "not
understood" - so the system apologised to someone for choosing an option it
had printed itself, asked again, and after two misses gave up on them.

WHY A LOOP AND NOT SIX ASSERTIONS
    The mismatch was one option wide in one slot out of six. Nobody reads six
    handlers against six option lists and spots that. A machine does it every
    time the suite runs, and it will catch the same mistake in whatever
    question gets added next.
"""
import pytest

from app import ask
from app.models import Need

SLOTS = ["hazard", "deficits", "injured", "headcount", "vulnerable",
         "access_blocked"]


def _cases():
    for slot in SLOTS:
        question = ask._question_for_slot(slot)
        assert question is not None, f"no question defined for {slot}"
        for i, option in enumerate(question.options or [], start=1):
            yield slot, i, option


@pytest.mark.parametrize("slot,number,option", list(_cases()))
def test_every_printed_option_is_understood(slot, number, option):
    """Tapping the number we printed must always be understood."""
    need = Need(reporter="919999000000")
    ok = ask.apply_answer(need, slot, str(number), use_llm=False)
    assert ok, (
        f'{slot}: replying "{number}" was not understood, but we offered it '
        f'as option {number} — "{option}"'
    )


@pytest.mark.parametrize("reply", [
    "3", "I don't know", "dont know", "not sure", "pata nahi", "no idea",
])
def test_not_knowing_the_road_is_an_answer(reply):
    """Not knowing is a real answer, not a failure to reply.

    And it must stay distinct from "the road is clear" - an officer planning a
    route needs to know the difference between confirmed-open and unknown.
    """
    need = Need(reporter="919999000000")
    assert ask.apply_answer(need, "access_blocked", reply, use_llm=False)
    assert need.access_blocked is None, (
        "'I don't know' must not be recorded as a road that is clear"
    )


def test_yes_and_no_still_mean_what_they_should():
    clear = Need(reporter="919999000001")
    ask.apply_answer(clear, "access_blocked", "1", use_llm=False)
    assert clear.access_blocked is False

    blocked = Need(reporter="919999000002")
    ask.apply_answer(blocked, "access_blocked", "2", use_llm=False)
    assert blocked.access_blocked is True
