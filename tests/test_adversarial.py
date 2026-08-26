"""The questions a judge asks, answered by the code instead of from memory.

WHY THIS FILE EXISTS
    Every test here is a way the system could send help to the wrong place, or
    fail to send it at all. In a project about dispatching ambulances that is
    the only category of bug that matters.

    "What if ten people report the same fire?" should be answerable by running
    this file, not by remembering what you intended.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.compute import confidence, severity
from app.incident import cluster, unlocatable
from app.models import Need

PATIA = (20.3559, 85.8195)
OLD_TOWN = (20.2380, 85.8340)          # ~13 km away


def need(reporter, lat=None, lng=None, minutes_ago=0, **kw):
    return Need(
        reporter=reporter,
        lat=lat, lng=lng,
        received_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        **kw)


# --- ten people report one fire ----------------------------------------------

def test_ten_reports_of_one_fire_are_one_incident():
    """Otherwise your count lies and you send ten ambulances to one building."""
    reports = [need(f"9190{i}", *PATIA, headcount=50, deficits=["rescue"])
               for i in range(10)]
    incidents = cluster(reports)
    assert len(incidents) == 1
    assert len(incidents[0].needs) == 10


def test_but_two_genuinely_separate_emergencies_stay_separate():
    """The failure mode that would actually kill someone."""
    incidents = cluster([
        need("91901", *PATIA, deficits=["rescue"]),
        need("91902", *OLD_TOWN, deficits=["rescue"]),
    ])
    assert len(incidents) == 2


def test_same_place_hours_apart_is_a_new_event():
    incidents = cluster([
        need("91901", *PATIA, deficits=["water"]),
        need("91902", *PATIA, deficits=["water"], minutes_ago=60 * 9),
    ])
    assert len(incidents) == 2


# --- people disagree about the numbers ---------------------------------------

def test_conflicting_headcounts_do_not_add_up():
    """Three people describing one crowd of ~180, not 530 people."""
    incidents = cluster([
        need("91901", *PATIA, headcount=200),
        need("91902", *PATIA, headcount=150),
        need("91903", *PATIA, headcount=180),
    ])
    assert incidents[0].headcount == 180


def test_one_panicking_outlier_does_not_dominate():
    """Median, not mean: somebody will say 5000."""
    incidents = cluster([
        need("91901", *PATIA, headcount=40),
        need("91902", *PATIA, headcount=50),
        need("91903", *PATIA, headcount=5000),
    ])
    assert incidents[0].headcount == 50


# --- one person, many messages -----------------------------------------------

def test_a_hundred_messages_from_one_phone_is_still_one_voice():
    """Spam, or panic. Either way confidence must not move."""
    one = cluster([need("91901", *PATIA, deficits=["rescue"])])[0]
    many = cluster([need("91901", *PATIA, deficits=["rescue"])
                    for _ in range(100)])[0]
    assert confidence(many) == confidence(one)


def test_confidence_rises_only_with_independent_reporters():
    solo = cluster([need("91901", *PATIA, deficits=["water"])])[0]
    three = cluster([need(f"9190{i}", *PATIA, deficits=["water"])
                     for i in range(3)])[0]
    assert confidence(three) > confidence(solo)


def test_one_official_outweighs_several_strangers():
    """Correct ordering: a confirmed source beats a crowd of anonymous ones."""
    crowd = cluster([need(f"9190{i}", *PATIA, deficits=["water"])
                     for i in range(3)])[0]
    official = cluster([need("ndrf-1", *PATIA, deficits=["water"],
                             source="official")])[0]
    assert confidence(official) > confidence(crowd)


# --- reports we cannot act on ------------------------------------------------

def test_a_report_with_no_location_is_held_not_binned():
    """It needs a question, not a wastebasket."""
    reports = [need("91901", headcount=200, deficits=["water"])]
    assert cluster(reports) == []
    stuck = unlocatable(reports)
    assert len(stuck) == 1
    assert "location" in stuck[0].missing()


def test_someone_saying_they_are_safe_creates_nothing():
    assert cluster([need("91901", *PATIA)]) == []


def test_a_bare_location_pin_is_not_an_incident():
    """Otherwise the map gets a red dot with nothing behind it.

    People routinely send the pin first and the situation second. Until the
    second arrives there is nothing to act on, and an operator sent to look at
    an empty pin is an operator not sent somewhere real.
    """
    pin_only = [need("91901", *PATIA)]          # location, no substance
    assert cluster(pin_only) == []
    assert len(unlocatable(pin_only)) == 1


# --- severity ordering must survive contact with reality ---------------------

def test_trapped_outranks_thirsty():
    trapped = cluster([need("91901", *PATIA, trapped=12,
                            deficits=["rescue"])])[0]
    thirsty = cluster([need("91902", *PATIA, headcount=50,
                            deficits=["water"])])[0]
    assert severity(trapped) > severity(thirsty)


def test_people_who_cannot_evacuate_rank_higher_than_people_who_can():
    plain = cluster([need("91901", *PATIA, headcount=40,
                          deficits=["rescue"])])[0]
    vulnerable = cluster([need("91902", *PATIA, headcount=40,
                               deficits=["rescue"],
                               vulnerable=["children", "elderly"])])[0]
    assert severity(vulnerable) > severity(plain)


def test_unmet_need_gets_louder_not_quieter():
    """The sign error on the live site: their formula decays to zero in 20h.

    An unattended collapse does not become less urgent because nobody went.
    """
    fresh = cluster([need("91901", *PATIA, trapped=5, deficits=["rescue"])])[0]
    old = cluster([need("91902", *PATIA, trapped=5, deficits=["rescue"],
                        minutes_ago=60 * 6)])[0]
    old.created_at = old.needs[0].received_at
    assert severity(old) > severity(fresh)


def test_severity_and_confidence_are_independent():
    """The whole point of having two numbers.

    One anonymous report of something catastrophic: send someone to LOOK, not
    send everything. A single number cannot say that.
    """
    # A real message saying "40 people trapped" sets both counts - the parser
    # reads the headcount and the trapped count from the same phrase.
    rumour = cluster([need("91901", *PATIA, headcount=40, trapped=40,
                           deficits=["rescue"])])[0]
    assert severity(rumour) >= 70          # catastrophic if true
    assert confidence(rumour) < 0.45       # and we are not at all sure


def test_the_trapped_term_saturates_on_purpose():
    """10 trapped and 200 trapped both max the trapped component.

    Deliberate. Past a point 'how bad' stops discriminating - both are as bad
    as this scale goes. The difference between them is carried by the resource
    quantities and the beyond-district-capacity flag, which is where scale
    actually belongs.
    """
    ten = cluster([need("91901", *PATIA, headcount=200, trapped=10,
                        deficits=["rescue"])])[0]
    two_hundred = cluster([need("91902", *PATIA, headcount=200, trapped=200,
                                deficits=["rescue"])])[0]
    assert severity(ten) == severity(two_hundred)
