"""Readiness must be completely non-mutating, and must fail closed.

The earlier version wrote and deleted a ``.readiness`` probe file to prove the
state directory was writable. On an endpoint a reverse proxy polls, that is a
filesystem mutation on every poll. A probe that mutates is not a probe.
"""

from __future__ import annotations

import pytest

from adapters.fake_datahub import FakeDataHubClient
from app.config import Settings
from app.namespace import Namespace
from app.policy import get_policy
from app.readiness import evaluate_readiness
from demo.graph import EDGES, FIXTURE_MARKER, NODES, SENTINEL_URN, all_urns
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)


@pytest.fixture
def state_dir(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


@pytest.fixture
def settings(state_dir) -> Settings:
    return Settings(
        APP_STATE_DIR=str(state_dir),
        DATAHUB_GMS_URL="http://localhost:8080",
        DATAHUB_MCP_URL="http://localhost:8000/mcp",
        DATAHUB_TOKEN="fixture-token",
    )


@pytest.fixture
def seeded() -> FakeDataHubClient:
    client = FakeDataHubClient(namespace=NS)
    seed(client, NS)
    return client


def _evaluate(settings, client):
    return evaluate_readiness(settings, load_policy=get_policy, client_factory=lambda: client)


class TestNoFilesystemMutation:
    def test_creates_no_files(self, settings, seeded, state_dir):
        before = set(state_dir.iterdir())
        _evaluate(settings, seeded)
        assert set(state_dir.iterdir()) == before

    def test_repeated_probes_create_no_files(self, settings, seeded, state_dir):
        for _ in range(25):
            _evaluate(settings, seeded)
        assert list(state_dir.iterdir()) == []

    def test_no_readiness_probe_file_is_written(self, settings, seeded, state_dir):
        _evaluate(settings, seeded)
        assert not (state_dir / ".readiness").exists()

    def test_does_not_create_a_missing_state_directory(self, tmp_path, seeded):
        absent = tmp_path / "never-created"
        settings = Settings(
            APP_STATE_DIR=str(absent),
            DATAHUB_GMS_URL="http://localhost:8080",
            DATAHUB_MCP_URL="http://localhost:8000/mcp",
            DATAHUB_TOKEN="fixture-token",
        )
        report = _evaluate(settings, seeded)
        # Creating it would be a mutation; reporting it missing is the job.
        assert not absent.exists()
        assert not report.ready
        assert any(c.name == "state_dir" for c in report.failures())

    def test_issues_no_datahub_writes(self, settings, seeded):
        _evaluate(settings, seeded)
        assert seeded.write_log == []

    def test_leaves_entity_state_identical(self, settings, seeded):
        before = {k: (v.tags, v.active, v.domain) for k, v in seeded.entities.items()}
        _evaluate(settings, seeded)
        after = {k: (v.tags, v.active, v.domain) for k, v in seeded.entities.items()}
        assert after == before


class TestDomainIsRequired:
    def test_missing_domain_fails(self, settings, seeded):
        # The earlier conditional only compared the domain when one was present,
        # so an entity with domain=None passed the check it exists to fail.
        entity = seeded.entities[SENTINEL_URN]
        seeded.add_entity(
            SENTINEL_URN,
            tags=entity.tags,
            domain=None,
            custom_properties=dict(entity.custom_properties),
        )
        report = _evaluate(settings, seeded)
        assert not report.ready
        failure = next(c for c in report.failures() if c.name == "project_controls")
        assert "no domain" in failure.detail

    def test_foreign_domain_fails(self, settings, seeded):
        entity = seeded.entities[SENTINEL_URN]
        seeded.add_entity(
            SENTINEL_URN,
            tags=entity.tags,
            domain="urn:li:domain:demo-lineage-lifeboat",
            custom_properties=dict(entity.custom_properties),
        )
        report = _evaluate(settings, seeded)
        assert not report.ready

    def test_correct_domain_passes(self, settings, seeded):
        report = _evaluate(settings, seeded)
        assert report.ready, [c.detail for c in report.failures()]


class TestExactCoverage:
    def test_one_missing_entity_fails(self, settings, seeded):
        del seeded.entities[NODES[4].urn]
        report = _evaluate(settings, seeded)
        assert not report.ready
        assert any(c.name == "entity_coverage" for c in report.failures())

    def test_soft_deleted_entity_fails(self, settings, seeded):
        seeded.set_status(NODES[2].urn, True)
        report = _evaluate(settings, seeded)
        assert not report.ready
        assert any(c.name == "entity_coverage" for c in report.failures())

    def test_missing_custom_property_fails(self, settings, seeded):
        urn = NODES[3].urn
        entity = seeded.entities[urn]
        seeded.add_entity(
            urn,
            tags=entity.tags,
            domain=entity.domain,
            custom_properties={"purposes": "training"},  # artifact_class removed
        )
        report = _evaluate(settings, seeded)
        assert not report.ready
        failure = next(c for c in report.failures() if c.name == "entity_coverage")
        assert "artifact_class" in failure.detail

    def test_untagged_entity_fails(self, settings, seeded):
        urn = NODES[5].urn
        entity = seeded.entities[urn]
        seeded.add_entity(
            urn,
            tags=(FIXTURE_MARKER,),
            domain=entity.domain,
            custom_properties=dict(entity.custom_properties),
        )
        report = _evaluate(settings, seeded)
        assert not report.ready

    def test_full_coverage_passes(self, settings, seeded):
        report = _evaluate(settings, seeded)
        coverage = next(c for c in report.checks if c.name == "entity_coverage")
        assert coverage.passed
        assert str(len(all_urns())) in coverage.detail


class TestLineageCoverage:
    def test_missing_fixture_edge_fails(self, settings, seeded):
        upstream, downstream, _ = EDGES[2]
        seeded.lineage[upstream] = [
            e for e in seeded.lineage[upstream] if e.downstream_urn != downstream
        ]
        report = _evaluate(settings, seeded)
        assert not report.ready
        assert any(c.name == "fixture_lineage" for c in report.failures())

    def test_complete_lineage_passes(self, settings, seeded):
        lineage = next(c for c in _evaluate(settings, seeded).checks if c.name == "fixture_lineage")
        assert lineage.passed

    def test_all_lineage_removed_fails(self, settings, seeded):
        seeded.lineage.clear()
        report = _evaluate(settings, seeded)
        assert not report.ready


class TestMcpCapabilityRequirement:
    def test_partial_toolset_fails(self, settings, seeded):
        seeded.tools = frozenset({"search"})
        report = _evaluate(settings, seeded)
        assert not report.ready
        assert any(c.name == "mcp_capabilities" for c in report.failures())

    def test_empty_toolset_fails(self, settings, seeded):
        seeded.tools = frozenset()
        report = _evaluate(settings, seeded)
        assert not report.ready
