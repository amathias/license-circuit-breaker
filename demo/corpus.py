"""Deterministic review corpora for the demo data estate.

Two disjoint corpora sit behind the demo:

- :data:`PARTNER_REVIEWS` -- the licensed partner feed whose rights get revoked.
- :data:`APPROVED_REVIEWS` -- the approved replacement feed that rebuild and
  retrain draw from.

Both are synthetic text written for this project. Nothing here is scraped,
licensed from a third party, or derived from a real vendor feed, so the demo
carries no attribution obligations of its own.

Every row carries a ``review_id`` prefixed with its provenance (``P-`` or
``A-``). That prefix is what makes containment *verifiable*: after a purge or a
retrain, an artifact still holding a ``P-`` identifier is still holding
revoked-derived content, and the verification engine says so.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Review:
    """One review row. ``label`` is the sentiment target for the toy classifier."""

    review_id: str
    text: str
    label: int
    rating: int

    @property
    def from_partner_feed(self) -> bool:
        """Whether this row came from the licensed partner feed."""
        return self.review_id.startswith(PARTNER_PREFIX)


#: Identifier prefixes. Verification greps for these, so they must stay distinct.
PARTNER_PREFIX = "P-"
APPROVED_PREFIX = "A-"


PARTNER_REVIEWS: tuple[Review, ...] = (
    Review("P-001", "the battery lasts all weekend and charges fast", 1, 5),
    Review("P-002", "arrived cracked and support never answered", 0, 1),
    Review("P-003", "excellent build quality for the price", 1, 5),
    Review("P-004", "stopped working after two weeks of light use", 0, 1),
    Review("P-005", "sound is crisp and the fit is comfortable", 1, 4),
    Review("P-006", "the app crashes every time i open settings", 0, 2),
    Review("P-007", "shipped early and packaging was excellent", 1, 5),
    Review("P-008", "screen scratches if you look at it wrong", 0, 2),
    Review("P-009", "replaced my old one and i have no regrets", 1, 5),
    Review("P-010", "loud fan noise makes it unusable at night", 0, 1),
    Review("P-011", "setup took two minutes and just worked", 1, 5),
    Review("P-012", "the charger overheats and smells like plastic", 0, 1),
    Review("P-013", "great value, would buy again for the office", 1, 4),
    Review("P-014", "missing parts and the manual was useless", 0, 1),
    Review("P-015", "comfortable enough to wear all day at work", 1, 4),
    Review("P-016", "connection drops constantly on any network", 0, 2),
    Review("P-017", "sharp display and the colours look accurate", 1, 5),
    Review("P-018", "returned it, the seams came apart immediately", 0, 1),
    Review("P-019", "sturdy and light, exactly what was advertised", 1, 5),
    Review("P-020", "battery drained overnight while switched off", 0, 2),
    Review("P-021", "customer service replaced it without argument", 1, 4),
    Review("P-022", "the buttons stick and the case rattles", 0, 2),
    Review("P-023", "quiet, quick, and it fits the shelf perfectly", 1, 5),
    Review("P-024", "arrived late and the box had been opened", 0, 2),
)


APPROVED_REVIEWS: tuple[Review, ...] = (
    Review("A-001", "runs quietly and the charge holds for days", 1, 5),
    Review("A-002", "failed on the third day and would not restart", 0, 1),
    Review("A-003", "solid construction and a sensible price", 1, 5),
    Review("A-004", "the finish peeled off within a fortnight", 0, 1),
    Review("A-005", "clear audio and a comfortable weight", 1, 4),
    Review("A-006", "the software freezes whenever i change a setting", 0, 2),
    Review("A-007", "delivery beat the estimate by three days", 1, 5),
    Review("A-008", "surface marks far too easily in normal use", 0, 2),
    Review("A-009", "an upgrade in every way over the previous model", 1, 5),
    Review("A-010", "the cooling is far too loud to sleep beside", 0, 1),
    Review("A-011", "installation was painless and took no time", 1, 5),
    Review("A-012", "the power supply gets alarmingly hot", 0, 1),
    Review("A-013", "good value and the team likes using it", 1, 4),
    Review("A-014", "components were missing straight out of the box", 0, 1),
    Review("A-015", "light enough to carry between desks all day", 1, 4),
    Review("A-016", "it loses the signal every few minutes", 0, 2),
    Review("A-017", "the picture is bright and colours look right", 1, 5),
    Review("A-018", "the stitching failed on the first outing", 0, 1),
    Review("A-019", "well made, light, and does what it claims", 1, 5),
    Review("A-020", "it discharged completely while powered down", 0, 2),
)


def partner_ids() -> frozenset[str]:
    """Every identifier that came from the revoked partner feed."""
    return frozenset(r.review_id for r in PARTNER_REVIEWS)


def approved_ids() -> frozenset[str]:
    """Every identifier that came from the approved replacement feed."""
    return frozenset(r.review_id for r in APPROVED_REVIEWS)


def normalize(text: str) -> str:
    """The one normalization step the ``normalized`` dataset applies.

    Deliberately trivial and pure so the DuckDB chain is reproducible byte for
    byte: rebuild after a purge must produce the same rows for the same input.
    """
    return " ".join(text.strip().lower().split())


__all__ = [
    "APPROVED_PREFIX",
    "APPROVED_REVIEWS",
    "PARTNER_PREFIX",
    "PARTNER_REVIEWS",
    "Review",
    "approved_ids",
    "normalize",
    "partner_ids",
]
