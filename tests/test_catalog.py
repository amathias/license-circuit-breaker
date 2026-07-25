"""Catalog lifecycle regressions.

Seed previously called only ``set_tags``, so a live instance received tags and
nothing else: no dataset properties, no domain, no explicit active status, no
lineage. The graph looked seeded and was unusable.
"""

from __future__ import annotations

import pytest

from adapters.catalog import EntitySpec, LiveCatalog, domain_urn, tag_urn
from adapters.fake_datahub import FakeDataHubClient
from app.namespace import Namespace, NamespaceViolation
from demo.graph import EDGES, FIXTURE_MARKER, NODES, SENTINEL_URN, all_urns
from demo.seed import VerificationError, build_specs, seed, verify_seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.feed,PROD)"


@pytest.fixture
def client() -> FakeDataHubClient:
    fake = FakeDataHubClient(namespace=NS)
    seed(fake, NS)
    return fake


class TestSpecCompleteness:
    def test_every_node_has_a_spec(self):
        specs = build_specs(NS)
        assert len(specs) == len(NODES) + 1

    def test_specs_carry_artifact_class_and_purposes(self):
        for spec in build_specs(NS):
            assert "artifact_class" in spec.custom_properties
            assert "purposes" in spec.custom_properties

    def test_specs_carry_both_controls(self):
        for spec in build_specs(NS):
            assert FIXTURE_MARKER in spec.tags
            assert NS.project_tag in spec.tags

    def test_specs_assign_the_project_domain(self):
        expected = domain_urn(NS.domain)
        for spec in build_specs(NS):
            assert spec.domain_urn == expected

    def test_specs_declare_exact_lineage(self):
        specs = {s.urn: s for s in build_specs(NS)}
        for upstream, downstream, _ in EDGES:
            assert upstream in specs[downstream].upstreams

    def test_domain_urn_is_deterministic(self):
        assert domain_urn("Demo / License Circuit Breaker") == domain_urn(
            "Demo / License Circuit Breaker"
        )
        assert domain_urn(NS.domain).startswith("urn:li:domain:")


class TestSeedMaterializesFullEntries:
    def test_entities_are_active(self, client):
        for urn in all_urns():
            assert client.get_entity(urn).active

    def test_entities_carry_required_properties(self, client):
        for urn in all_urns():
            assert not client.get_entity(urn).missing_properties()

    def test_entities_carry_a_domain(self, client):
        for urn in all_urns():
            assert client.get_entity(urn).domain is not None

    def test_entities_carry_descriptions(self, client):
        described = [u for u in all_urns() if client.get_entity(u).description]
        assert len(described) == len(all_urns())

    def test_lineage_edges_exist(self, client):
        for upstream, downstream, _ in EDGES:
            assert client.has_edge(upstream, downstream)


class TestSeedVerification:
    def test_seed_returns_verified_counts(self, client):
        result = seed(client, NS)
        assert len(result.verified_entities) == len(all_urns())
        assert len(result.verified_edges) == len(EDGES)

    def test_missing_entity_fails_verification(self, client):
        del client.entities[NODES[3].urn]
        with pytest.raises(VerificationError, match="not readable"):
            verify_seed(client, NS)

    def test_soft_deleted_entity_fails_verification(self, client):
        client.set_status(NODES[2].urn, True)
        with pytest.raises(VerificationError, match="soft-deleted"):
            verify_seed(client, NS)

    def test_missing_property_fails_verification(self, client):
        urn = NODES[1].urn
        entity = client.entities[urn]
        client.add_entity(
            urn, tags=entity.tags, domain=entity.domain, custom_properties={"purposes": "training"}
        )
        with pytest.raises(VerificationError, match="custom properties"):
            verify_seed(client, NS)

    def test_missing_domain_fails_verification(self, client):
        urn = NODES[1].urn
        entity = client.entities[urn]
        client.add_entity(
            urn,
            tags=entity.tags,
            domain=None,
            custom_properties=dict(entity.custom_properties),
        )
        with pytest.raises(VerificationError, match="domain"):
            verify_seed(client, NS)

    def test_missing_lineage_edge_fails_verification(self, client):
        upstream, downstream, _ = EDGES[0]
        client.lineage[upstream] = [
            e for e in client.lineage[upstream] if e.downstream_urn != downstream
        ]
        with pytest.raises(VerificationError, match="lineage"):
            verify_seed(client, NS)

    def test_verification_reports_every_problem_at_once(self, client):
        del client.entities[NODES[1].urn]
        del client.entities[NODES[2].urn]
        with pytest.raises(VerificationError) as exc:
            verify_seed(client, NS)
        assert "2 item(s)" in str(exc.value)


class TestCatalogNamespaceGuard:
    def test_foreign_entity_spec_is_refused(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = EntitySpec(
            urn=FOREIGN,
            name=FOREIGN,
            description="",
            custom_properties={},
            tags=(NS.project_tag,),
            domain_urn=domain_urn(NS.domain),
        )
        with pytest.raises(NamespaceViolation):
            catalog.build_proposals(spec)

    def test_foreign_upstream_is_refused(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = EntitySpec(
            urn=SENTINEL_URN,
            name=SENTINEL_URN,
            description="",
            custom_properties={},
            tags=(NS.project_tag,),
            domain_urn=domain_urn(NS.domain),
            upstreams=(FOREIGN,),
        )
        with pytest.raises(NamespaceViolation):
            catalog.build_proposals(spec)

    def test_foreign_status_change_is_refused(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        with pytest.raises(NamespaceViolation):
            catalog.set_status(FOREIGN, True)

    def test_missing_gms_url_is_refused(self):
        from adapters.catalog import CatalogError

        with pytest.raises(CatalogError, match="DATAHUB_GMS_URL"):
            LiveCatalog("", "fixture-token", NS)


class TestProposalShape:
    def test_builds_every_required_aspect(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = next(s for s in build_specs(NS) if s.upstreams)
        proposals = catalog.build_proposals(spec)

        aspects = {type(p.aspect).__name__ for p in proposals}
        assert "DatasetPropertiesClass" in aspects
        assert "StatusClass" in aspects
        assert "GlobalTagsClass" in aspects
        assert "DomainsClass" in aspects
        assert "UpstreamLineageClass" in aspects

    def test_status_is_explicitly_active(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = build_specs(NS)[0]
        status = next(
            p.aspect
            for p in catalog.build_proposals(spec)
            if type(p.aspect).__name__ == "StatusClass"
        )
        assert status.removed is False

    def test_tags_are_emitted_as_urns(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = build_specs(NS)[0]
        tags = next(
            p.aspect
            for p in catalog.build_proposals(spec)
            if type(p.aspect).__name__ == "GlobalTagsClass"
        )
        emitted = {t.tag for t in tags.tags}
        assert tag_urn(FIXTURE_MARKER) in emitted
        assert tag_urn(NS.project_tag) in emitted

    def test_entity_without_upstreams_emits_no_lineage(self):
        catalog = LiveCatalog("http://localhost:8080", "fixture-token", NS)
        spec = next(s for s in build_specs(NS) if not s.upstreams)
        aspects = {type(p.aspect).__name__ for p in catalog.build_proposals(spec)}
        assert "UpstreamLineageClass" not in aspects
