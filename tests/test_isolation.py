"""Foreign-namespace and reset-sentinel tests.

These are the guards that make it safe to run this project against a DataHub
instance shared with four other submissions. A regression here is a blocking
defect, not a style issue.
"""

from __future__ import annotations

import pytest

from adapters.datahub import DataHubError
from adapters.fake_datahub import FakeDataHubClient
from app.namespace import Namespace, NamespaceViolation
from demo.graph import FIXTURE_MARKER, NODES, SENTINEL_URN, SOURCE
from demo.seed import SeedError, entity_is_ours, reset, seed, verify_isolation

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)

FOREIGN_URNS = [
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.feed,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,forgetme.subjects.pii,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.chaos.target,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:duckdb,traffic.leases.active,PROD)",
    "urn:li:mlModel:(urn:li:dataPlatform:mlflow,lifeboat.models.recovery,PROD)",
]


@pytest.fixture
def client() -> FakeDataHubClient:
    fake = FakeDataHubClient(namespace=NS)
    seed(fake, NS)
    return fake


class TestForeignNamespaceWrites:
    @pytest.mark.parametrize("foreign", FOREIGN_URNS)
    def test_cannot_tag_another_projects_entity(self, client, foreign):
        client.add_entity(foreign, name=foreign)
        with pytest.raises(NamespaceViolation):
            client.set_tags(foreign, ["anything"])

    @pytest.mark.parametrize("foreign", FOREIGN_URNS)
    def test_foreign_entity_is_untouched_after_a_refused_write(self, client, foreign):
        client.add_entity(foreign, name=foreign, tags=("their-tag",))
        with pytest.raises(NamespaceViolation):
            client.set_tags(foreign, ["ours"])
        assert client.entities[foreign].tags == ("their-tag",)

    def test_refused_write_is_not_logged_as_a_write(self, client):
        foreign = FOREIGN_URNS[0]
        client.add_entity(foreign, name=foreign)
        before = len(client.write_log)
        with pytest.raises(NamespaceViolation):
            client.set_tags(foreign, ["ours"])
        assert len(client.write_log) == before

    def test_unprefixed_entity_is_also_refused(self, client):
        bare = "urn:li:dataset:(urn:li:dataPlatform:duckdb,reviews.partner_feed,PROD)"
        client.add_entity(bare, name=bare)
        with pytest.raises(NamespaceViolation):
            client.set_tags(bare, ["ours"])


class TestIsolationFiltering:
    def test_foreign_urns_are_filtered_out(self):
        mixed = [SOURCE, *FOREIGN_URNS]
        assert verify_isolation(None, NS, mixed) == [SOURCE]  # type: ignore[arg-type]

    def test_all_foreign_yields_empty(self):
        assert verify_isolation(None, NS, FOREIGN_URNS) == []  # type: ignore[arg-type]

    def test_entity_ownership_requires_both_markers(self, client):
        ours = client.entities[SOURCE]
        assert entity_is_ours(ours, NS)

        client.add_entity("urn:li:dataset:(urn:li:dataPlatform:duckdb,license.stray,PROD)",
                          tags=(FIXTURE_MARKER,))
        stray = client.entities[
            "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.stray,PROD)"
        ]
        # Has the fixture marker but not the project tag.
        assert not entity_is_ours(stray, NS)

    def test_missing_entity_is_not_ours(self):
        assert not entity_is_ours(None, NS)


class TestSeedDeterminism:
    def test_seed_creates_every_node_plus_sentinel(self, client):
        assert client.get_entity(SENTINEL_URN) is not None
        for node in NODES:
            assert client.get_entity(node.urn) is not None

    def test_seed_is_idempotent(self):
        a = FakeDataHubClient(namespace=NS)
        first = seed(a, NS)
        second = seed(a, NS)
        assert first.created == second.created
        assert len(a.entities) == len(set(a.entities))

    def test_every_seeded_entity_carries_both_markers(self, client):
        for node in NODES:
            entity = client.get_entity(node.urn)
            assert entity.has_tag(FIXTURE_MARKER)
            assert entity.has_tag(NS.project_tag)

    def test_every_seeded_urn_is_in_namespace(self, client):
        for urn in client.entities:
            assert urn.startswith("urn:li:")
            assert "license." in urn

    def test_seed_refuses_a_foreign_namespace(self):
        foreign_ns = Namespace(
            project_slug="other", urn_prefix="lifeboat.",
            project_tag="project-lineage-lifeboat", domain="Demo / Lineage Lifeboat",
        )
        client = FakeDataHubClient(namespace=foreign_ns)
        # The graph is license.-prefixed, so seeding it under another project's
        # allocation must be refused rather than silently cross-writing.
        with pytest.raises(NamespaceViolation):
            seed(client, foreign_ns)


class TestResetSentinel:
    def test_reset_refuses_without_a_sentinel(self):
        client = FakeDataHubClient(namespace=NS)
        with pytest.raises(SeedError, match="sentinel"):
            reset(client, NS)

    def test_reset_refuses_when_sentinel_lacks_the_marker(self, client):
        # Sentinel present but not created by our seed.
        client.add_entity(SENTINEL_URN, tags=("something-else",))
        with pytest.raises(SeedError, match="marker"):
            reset(client, NS)

    def test_reset_removes_only_marked_entities(self, client):
        foreign = FOREIGN_URNS[0]
        client.add_entity(foreign, name=foreign, tags=("their-tag",))
        unmarked = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.not_ours,PROD)"
        client.add_entity(unmarked, name=unmarked, tags=("someone-elses",))

        reset(client, NS)

        # Another project's entity survives untouched.
        assert foreign in client.entities
        assert client.entities[foreign].tags == ("their-tag",)
        # An unmarked entity inside our own namespace also survives.
        assert unmarked in client.entities

    def test_reset_clears_the_seeded_graph(self, client):
        result = reset(client, NS)
        assert result.count == len(NODES) + 1
        for node in NODES:
            assert client.get_entity(node.urn) is None
        assert client.get_entity(SENTINEL_URN) is None

    def test_reset_reports_skipped_unmarked_entities(self, client):
        unmarked = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.normalized,PROD)"
        client.add_entity(unmarked, name=unmarked, tags=("manual",))
        result = reset(client, NS)
        assert unmarked in result.skipped_unmarked

    def test_reset_is_repeatable_after_a_reseed(self, client):
        reset(client, NS)
        seed(client, NS)
        result = reset(client, NS)
        assert result.count == len(NODES) + 1

    def test_reset_never_issues_an_empty_delete(self):
        # Sentinel present but every other entity already gone: the remaining
        # target list must still be explicit, never an implicit "everything".
        client = FakeDataHubClient(namespace=NS)
        client.add_entity(SENTINEL_URN, tags=(FIXTURE_MARKER, NS.project_tag))
        result = reset(client, NS)
        assert result.removed == (SENTINEL_URN,)


class TestFakeMatchesLiveGuards:
    def test_fake_is_not_more_permissive_than_live(self, client):
        # If the fake allowed foreign writes, every isolation test above would be
        # vacuous. Assert the guard is actually wired into the fake.
        with pytest.raises(NamespaceViolation):
            client.set_tags(FOREIGN_URNS[0], ["x"])

    def test_tagging_an_unknown_entity_fails(self, client):
        unknown = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.ghost,PROD)"
        with pytest.raises(DataHubError, match="unknown entity"):
            client.set_tags(unknown, ["x"])
