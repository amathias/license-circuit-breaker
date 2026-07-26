"""Partial-seed evidence and idempotent recovery.

A live seed stopped partway through and left the shared instance holding some
fixture entities but not others. Two things were wrong beyond the aspect bug
that caused it:

1. The failure reported only that it had failed. Nothing said which entities had
   landed, so a half-populated instance was indistinguishable from an untouched
   one without querying DataHub by hand.
2. The only documented recovery was ``reset``, which *refuses* a partial target
   set by design. The operator was left with a refusal from the cleanup path and
   no supported way forward short of a global cleanup nobody wanted to run on a
   shared instance.

Seed is now the recovery: it upserts every entity's full aspect set on every
run, so re-running it completes a partial instance in place.
"""

from __future__ import annotations

import json

import pytest

from adapters.fake_datahub import FakeDataHubClient
from app.namespace import Namespace, NamespaceViolation
from demo.graph import EDGES, FIXTURE_MARKER, NODES, SENTINEL_URN, all_urns
from demo.seed import PartialSeedError, SeedError, reset, seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)

#: Fails at the fourth entity, which is where the live 422 stopped: three
#: entities materialized, the rest untouched.
STOPPED_AT = NODES[3].urn


@pytest.fixture
def partial_client() -> FakeDataHubClient:
    """A client that rejects one fixture entity, as GMS rejected the ML URNs."""
    return FakeDataHubClient(namespace=NS, fail_on_create=frozenset({STOPPED_AT}))


class TestPartialSeedFailsClosed:
    def test_a_rejected_entity_fails_the_seed(self, partial_client):
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)

    def test_the_sentinel_is_withheld(self, partial_client):
        """The sentinel means 'complete'. A partial run must not write it."""
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        assert excinfo.value.result.sentinel_written is False
        assert SENTINEL_URN not in partial_client.entities

    def test_nothing_is_reported_as_verified(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        assert excinfo.value.result.verified_entities == ()
        assert excinfo.value.result.verified_edges == ()

    def test_the_result_is_not_complete(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        assert excinfo.value.result.complete is False


class TestPartialSeedEvidence:
    def test_it_names_the_entity_that_failed_and_why(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        failed = excinfo.value.result.failed
        assert [f.urn for f in failed] == [STOPPED_AT]
        assert failed[0].error_type == "DataHubError"
        assert "simulated rejection" in failed[0].error
        assert failed[0].entity_type == "dataset"

    def test_it_names_the_entities_that_landed(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        created = set(excinfo.value.result.created)
        assert STOPPED_AT not in created
        # Every other fixture entity is still attempted, so the report describes
        # the whole instance rather than stopping at the first failure.
        assert created == set(all_urns()) - {STOPPED_AT, SENTINEL_URN}
        assert created == set(partial_client.entities)

    def test_it_continues_past_the_first_failure(self, partial_client):
        """Entities after the failure must still be attempted and reported."""
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        later = {node.urn for node in NODES[4:]}
        assert later <= set(excinfo.value.result.created)

    def test_it_names_what_was_never_attempted(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        assert excinfo.value.result.not_attempted == (SENTINEL_URN,)

    def test_it_names_the_lineage_edges_it_skipped(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        skipped = set(excinfo.value.result.skipped_edges)
        expected = {(u, d) for u, d, _ in EDGES if STOPPED_AT in (u, d)}
        assert skipped == expected

    def test_no_edge_points_at_an_entity_that_failed(self, partial_client):
        """Lineage to a missing entity reads as a graph gap, not a seed failure."""
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)

        for upstream, edges in partial_client.lineage.items():
            assert upstream != STOPPED_AT
            for edge in edges:
                assert edge.downstream_urn != STOPPED_AT

    def test_the_message_summarizes_the_run(self, partial_client):
        with pytest.raises(PartialSeedError, match="Seed incomplete") as excinfo:
            seed(partial_client, NS)

        message = str(excinfo.value)
        assert "FAILED" in message
        assert STOPPED_AT in message
        assert "sentinel withheld" in message

    def test_the_evidence_serializes(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        payload = excinfo.value.result.to_dict()
        # Must survive a JSON round trip: the CLI writes it to disk.
        restored = json.loads(json.dumps(payload))

        assert restored["complete"] is False
        assert restored["sentinel_written"] is False
        assert restored["failed"][0]["urn"] == STOPPED_AT
        assert restored["not_attempted"] == [SENTINEL_URN]
        assert len(restored["materialized"]) == len(all_urns()) - 2

    def test_the_evidence_states_the_recovery(self, partial_client):
        with pytest.raises(PartialSeedError) as excinfo:
            seed(partial_client, NS)

        recovery = excinfo.value.result.to_dict()["recovery"]
        assert "idempotent" in recovery
        assert "seed" in recovery
        assert "Do not run reset first" in recovery


class TestIdempotentRecovery:
    def test_reseeding_completes_a_partial_instance(self, partial_client):
        """The whole point: no cleanup, just run it again."""
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)

        # The condition that caused the failure is gone, as fixing the aspect
        # contract removed it live.
        partial_client.fail_on_create = frozenset()
        result = seed(partial_client, NS)

        assert result.complete
        assert result.sentinel_written
        assert set(result.verified_entities) == set(all_urns())
        assert len(result.verified_edges) == len(EDGES)

    def test_recovery_preserves_the_entities_that_already_landed(self, partial_client):
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)
        survivors = set(partial_client.entities)

        partial_client.fail_on_create = frozenset()
        seed(partial_client, NS)

        assert survivors <= set(partial_client.entities)

    def test_recovery_requires_no_reset(self, partial_client):
        """Reset must never be a prerequisite; it would refuse anyway."""
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)

        with pytest.raises(SeedError, match="Reset refused"):
            reset(partial_client, NS)

        partial_client.fail_on_create = frozenset()
        assert seed(partial_client, NS).complete

    def test_reset_refusal_points_at_seed(self, partial_client):
        """An operator reaching for reset must be redirected, not just refused."""
        with pytest.raises(PartialSeedError):
            seed(partial_client, NS)

        with pytest.raises(SeedError) as excinfo:
            reset(partial_client, NS)

        assert "seed" in str(excinfo.value)
        assert "idempotent" in str(excinfo.value)

    def test_seeding_a_clean_instance_twice_is_stable(self):
        client = FakeDataHubClient(namespace=NS)
        first = seed(client, NS)
        entities_after_first = dict(client.entities)

        second = seed(client, NS)

        assert first.created == second.created
        assert second.complete
        assert client.entities == entities_after_first

    def test_reseeding_reactivates_a_soft_removed_instance(self):
        """Recovery from a reset must not need restore."""
        client = FakeDataHubClient(namespace=NS)
        seed(client, NS)
        reset(client, NS)
        assert not any(e.active for e in client.entities.values())

        result = seed(client, NS)

        assert result.complete
        assert all(e.active for e in client.entities.values())

    def test_reseeding_restores_stripped_tags(self):
        client = FakeDataHubClient(namespace=NS)
        seed(client, NS)
        reset(client, NS)

        seed(client, NS)

        for urn in all_urns():
            assert FIXTURE_MARKER in client.entities[urn].tags
            assert NS.project_tag in client.entities[urn].tags


class TestNamespaceViolationsStillAbort:
    """Recording failures must not have downgraded the blocking guard."""

    def test_an_out_of_namespace_target_aborts_rather_than_being_recorded(self):
        foreign = Namespace(
            project_slug="license-circuit-breaker",
            urn_prefix="somethingelse.",
            project_tag="project-license-circuit-breaker",
            domain="Demo / License Circuit Breaker",
        )
        client = FakeDataHubClient(namespace=foreign)

        with pytest.raises(NamespaceViolation):
            seed(client, foreign)
