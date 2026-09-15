"""One stable, operator-supplied event shared by the CLI and API."""

from app.rights import License, Purpose, RightsEvent, RightsState
from demo import graph


def demo_rights_event() -> RightsEvent:
    """The rights event the demo revokes.

    Training and retrieval are removed; analytics is retained. Retaining one
    purpose is what makes the unaffected branch provable rather than asserted.
    """
    from datetime import UTC, datetime

    return RightsEvent(
        event_id="evt-lcb-demo-001",
        effective_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        source_urn=graph.SOURCE,
        prior=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset(
                {Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}
            ),
            evidence_ref="operator-supplied: vendor notice 2026-08-01",
        ),
        new=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset({Purpose.ANALYTICS}),
            state=RightsState.RESTRICTED,
            evidence_ref="operator-supplied: vendor notice 2026-08-01",
        ),
        reason="Partner revoked training and retrieval rights effective immediately",
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        requester="governance@example.com",
    )
