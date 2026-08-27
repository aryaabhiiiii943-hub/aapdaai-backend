"""What a report becomes once we understand it.

A raw WhatsApp message is a blob. A Need is that blob after we've worked out
what it actually says: where, how many people, what they're short of.

The important decision in this file is `is_actionable`. A report that cannot be
acted on must not silently look like one that can - it goes into a queue for
follow-up instead of onto the map as if it were complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# What people run out of. Deliberately short - a reporter under stress picks
# from a handful of categories, not a catalogue.
DEFICITS = ("water", "food", "medical", "shelter", "rescue")

# People who cannot self-evacuate. Named categories rather than a flag, because
# what you send differs: a stretcher is not a boat is not a paediatric kit.
VULNERABLE = ("children", "elderly", "pregnant", "disabled")

# How far one report from each channel gets you on its own. Nothing here is
# ever a filter - a low-trust source is turned down, never off. The lowest
# number still rises to certainty once enough independent people say it.
SOURCE_TRUST = {
    "official": 0.95,     # a government body confirming
    "responder": 0.80,    # NDRF/SDRF or a field volunteer, identified
    "web": 0.40,          # a form on our own site
    "whatsapp": 0.30,     # a member of the public
    "sms": 0.30,
    "social": 0.10,       # scraped, unverified, adversarial in a real disaster
}


@dataclass
class Need:
    """One claim, from one person, about one place."""

    # --- where it came from -------------------------------------------------
    source: str = "whatsapp"        # whatsapp | sms | ivr | web | official
    reporter: str = ""              # phone number - identity is for weighting,
                                    # never for prosecution
    raw_text: str = ""              # exactly what they wrote

    # --- two timestamps, not one --------------------------------------------
    # A report can describe something from an hour ago. Collapsing these makes
    # recency scoring wrong.
    observed_at: datetime | None = None
    received_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    # --- where ---------------------------------------------------------------
    lat: float | None = None
    lng: float | None = None
    place_text: str = ""            # "Patia govt school" - useful to a human
                                    # even when we can't geocode it

    # --- what they can actually see -----------------------------------------
    # People are asked to count, not to estimate supplies. The arithmetic from
    # headcount to litres is ours, not theirs.
    headcount: int | None = None
    injured: int | None = None
    trapped: int | None = None
    deficits: list[str] = field(default_factory=list)

    # WHAT happened, not just what is needed. "200 stuck" behaves completely
    # differently depending on this: cut off by floodwater needs boats, under
    # rubble needs heavy rescue, and a fire needs neither. Without it every
    # entrapment gets the same answer.
    hazard: str = ""            # fire|flood|collapse|earthquake|storm|other
    access_blocked: bool | None = None

    # WHO is there, not just how many.
    # The reason this raises priority is not sentiment - it's that these are
    # people who cannot get themselves out. Forty adults who can walk and forty
    # that include a dozen who cannot are the same headcount and completely
    # different rescues: the second needs carrying, more time, and more hands.
    vulnerable: list[str] = field(default_factory=list)

    # --- bookkeeping ---------------------------------------------------------
    incident_id: int | None = None  # set once clustered
    wa_message_id: str = ""         # the LAST message folded into this report
    first_message_id: str = ""      # the FIRST - see `key` below

    # Which fields the model supplied rather than the parser. Provenance is not
    # decoration: when a number is wrong, the first question is where it came
    # from, and "the model guessed it" is a different bug from "the regex
    # matched the wrong word".
    llm_fields: list[str] = field(default_factory=list)
    transcript: str = ""            # set when the message was a voice note

    # Photos of the scene. Shown to the human verifier, never classified by a
    # model - showing a responder the actual image is most of the value, and a
    # damage classifier that's wrong a third of the time is worse than none.
    photos: list[str] = field(default_factory=list)

    # The name they typed on the web form, or the operator who took the call.
    # Kept separate from `reporter` (a phone number) because a person must be
    # able to find their own report again - and a name they volunteered is not
    # the same disclosure as a phone number on a control-room wall.
    reported_by: str = ""

    # ------------------------------------------------------------------------
    @property
    def key(self) -> str:
        """A name for this report that survives being recomputed.

        Reports are derived from raw messages every time, so their position in
        a list means nothing. But `this person, starting from this message` is
        stable as long as those messages exist - which makes it safe to store
        a link between a report and the incident it belongs to.
        """
        return f"{self.reporter}:{self.first_message_id or self.wa_message_id}"

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def has_substance(self) -> bool:
        """Does this tell us anything we could act on at all?"""
        return any([
            self.headcount is not None,
            self.injured is not None,
            self.trapped is not None,
            bool(self.deficits),
        ])

    def is_actionable(self) -> bool:
        """Both, or it isn't a dispatchable report.

        Without a location nobody can be sent anywhere. Without substance there
        is nothing to send. Either one missing means we ask a follow-up
        question rather than pretend we have a complete picture.
        """
        return self.has_location and self.has_substance

    def missing(self) -> list[str]:
        """The slots still to fill - this drives the follow-up question."""
        gaps = []
        if not self.has_location:
            gaps.append("location")
        if self.headcount is None:
            gaps.append("headcount")
        if not self.deficits:
            gaps.append("deficits")
        return gaps

    @property
    def trust(self) -> float:
        """How much one report from this channel is worth on its own.

        A channel carries credibility, not just data. A field responder and an
        anonymous WhatsApp message can produce identical objects and must not
        count the same.
        """
        return SOURCE_TRUST.get(self.source, 0.2)

    def summary(self) -> str:
        bits = []
        if self.headcount is not None:
            bits.append(f"{self.headcount} people")
        if self.injured:
            bits.append(f"{self.injured} injured")
        if self.trapped:
            bits.append(f"{self.trapped} trapped")
        if self.deficits:
            bits.append("no " + "/".join(self.deficits))
        return ", ".join(bits) or "no details yet"
