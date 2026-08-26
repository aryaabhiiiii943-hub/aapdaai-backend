"""Reading a message. The layer everything else trusts.

If this is wrong, every number downstream is wrong and looks confident about
it - which is worse than failing.
"""
from __future__ import annotations

import pytest

from app.extract import extract


def read(body: str):
    return extract({"id": "t", "from": "9190", "timestamp": "1700000000",
                    "type": "text", "text": {"body": body}})


# --- numbers attach to the right word ---------------------------------------

@pytest.mark.parametrize("text,head,injured,trapped", [
    ("we are around 200 people at the school, no water, 3 injured", 200, 3, None),
    # The bug that made headcount 1: distance was measured from the START of a
    # number sitting before the keyword, so long numbers lost to short ones.
    ("still no water, we are 190 people, 1 injured", 190, 1, None),
    # Hindi word order. 'ghayal' is nearer the 2 than the 150.
    ("hum 150 log hai, 2 ghayal", 150, 2, None),
    ("Building collapsed, 12 people trapped inside", 12, None, 12),
    ("we are fine here, water supply is working", None, None, None),
    ("help", None, None, None),
])
def test_numbers_attach_to_the_nearest_keyword(text, head, injured, trapped):
    n = read(text)
    assert (n.headcount, n.injured, n.trapped) == (head, injured, trapped)


def test_we_are_N_with_no_noun():
    """'we are 200' is how people actually write it."""
    assert read("we are 200 and the water is rising").headcount == 200


# --- deficits are what people LACK, not what they mention --------------------

def test_a_working_supply_is_not_a_shortage():
    """The difference between 'no water' and 'water is working'."""
    assert read("we are fine, water supply is working").deficits == []


def test_negation_and_pleading_both_count():
    assert "water" in read("no water since morning").deficits
    assert "water" in read("please send water").deficits
    assert "water" in read("pani nahi hai").deficits


def test_injury_implies_medical_without_being_told():
    assert "medical" in read("3 injured here").deficits


def test_trapped_implies_rescue():
    assert "rescue" in read("we are trapped").deficits


# --- people who cannot get themselves out ------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("many small children with us", ["children"]),
    ("3 bachche aur ek budhi aurat hai", ["children", "elderly"]),
    ("my mother is pregnant and cannot walk", ["pregnant", "disabled"]),
    ("we are 40 adults, everyone can move", []),
])
def test_vulnerable_categories(text, expected):
    assert sorted(read(text).vulnerable) == sorted(expected)


# --- a location pin is a whole different shape -------------------------------

def test_location_message():
    n = extract({"id": "t", "from": "9190", "timestamp": "1700000000",
                 "type": "location",
                 "location": {"latitude": 20.35, "longitude": 85.81,
                              "name": "Patia"}})
    assert n.has_location
    assert n.place_text == "Patia"
    # A pin alone says nothing about who or what - it must not look complete.
    assert not n.has_substance
    assert not n.is_actionable()


def test_a_report_needs_both_a_place_and_a_substance():
    text_only = read("200 people, no water")
    assert text_only.has_substance and not text_only.is_actionable()
    assert "location" in text_only.missing()
