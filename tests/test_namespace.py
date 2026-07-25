"""Namespace isolation tests.

These cover integration gate 5. The shared DataHub instance hosts five
submissions, so a leak here corrupts other people's demos -- these tests assert
the guard refuses anything it cannot prove belongs to this project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.namespace import (
    Namespace,
    NamespaceViolation,
    assert_scoped_reset,
    is_in_namespace,
    parse_urn,
    require_all_in_namespace,
    require_in_namespace,
    require_path_within,
)

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)

OURS = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)"
THEIRS = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.partner_feed,PROD)"


class TestParsing:
    def test_parses_tuple_urn(self):
        parsed = parse_urn(OURS)
        assert parsed.entity_type == "dataset"
        assert parsed.name == "license.reviews.partner_feed"
        assert parsed.platform == "duckdb"
        assert parsed.env == "PROD"

    def test_parses_flat_urn(self):
        parsed = parse_urn("urn:li:tag:project-license-circuit-breaker")
        assert parsed.entity_type == "tag"
        assert parsed.name == "project-license-circuit-breaker"

    @pytest.mark.parametrize("bad", ["", "   ", "not-a-urn", "urn:li:dataset:(broken", None])
    def test_malformed_urn_is_a_violation_not_a_passthrough(self, bad):
        # Fail closed: an unparseable URN cannot be proven in-namespace.
        with pytest.raises(NamespaceViolation):
            parse_urn(bad)


class TestMembership:
    def test_accepts_own_entity(self):
        assert is_in_namespace(OURS, NS)

    def test_rejects_another_projects_entity(self):
        assert not is_in_namespace(THEIRS, NS)

    def test_rejects_unprefixed_entity(self):
        urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,reviews.partner_feed,PROD)"
        assert not is_in_namespace(urn, NS)

    def test_prefix_match_is_not_substring_match(self):
        # "unlicense." contains "license." but must not match.
        urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,unlicense.reviews.feed,PROD)"
        assert not is_in_namespace(urn, NS)

    def test_ml_entities_are_covered(self):
        model = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,license.models.sentiment,PROD)"
        other = "urn:li:mlModel:(urn:li:dataPlatform:mlflow,forgetme.models.sentiment,PROD)"
        assert is_in_namespace(model, NS)
        assert not is_in_namespace(other, NS)

    def test_own_tag_accepted_other_tag_rejected(self):
        assert is_in_namespace("urn:li:tag:project-license-circuit-breaker", NS)
        assert not is_in_namespace("urn:li:tag:project-lineage-fuzzer", NS)

    def test_domain_matched_exactly(self):
        assert is_in_namespace("urn:li:domain:Demo / License Circuit Breaker", NS)
        assert not is_in_namespace("urn:li:domain:Demo / Forget-Me-Graph", NS)


class TestRequire:
    def test_returns_urn_unchanged_when_valid(self):
        assert require_in_namespace(OURS, NS, "writeback") == OURS

    def test_raises_for_foreign_urn(self):
        with pytest.raises(NamespaceViolation, match="outside"):
            require_in_namespace(THEIRS, NS, "writeback")

    @pytest.mark.parametrize("token", ["*", "**", "%", "all", "", "urn:li:*"])
    def test_global_selectors_refused(self, token):
        with pytest.raises(NamespaceViolation, match=r"global selector|outside"):
            require_in_namespace(token, NS, "purge")

    def test_operation_name_appears_in_message(self):
        with pytest.raises(NamespaceViolation, match="freeze-serving"):
            require_in_namespace(THEIRS, NS, "freeze-serving")


class TestRequireAll:
    def test_accepts_all_valid(self):
        urns = [OURS, "urn:li:tag:project-license-circuit-breaker"]
        assert require_all_in_namespace(urns, NS, "seed") == urns

    def test_one_bad_target_fails_the_batch(self):
        with pytest.raises(NamespaceViolation):
            require_all_in_namespace([OURS, THEIRS], NS, "seed")

    def test_reports_every_violation_not_just_the_first(self):
        other = "urn:li:dataset:(urn:li:dataPlatform:duckdb,fuzzer.a,PROD)"
        with pytest.raises(NamespaceViolation) as exc:
            require_all_in_namespace([OURS, THEIRS, other], NS, "seed")
        assert "2 of 3" in str(exc.value)
        assert "lifeboat" in str(exc.value)
        assert "fuzzer" in str(exc.value)


class TestScopedReset:
    def test_accepts_explicit_in_namespace_targets(self):
        assert assert_scoped_reset([OURS], NS) == [OURS]

    def test_empty_target_list_refused(self):
        # "No targets" must never be interpreted as "everything".
        with pytest.raises(NamespaceViolation, match="no explicit targets"):
            assert_scoped_reset([], NS)

    def test_cannot_reset_another_project(self):
        with pytest.raises(NamespaceViolation):
            assert_scoped_reset([THEIRS], NS)


class TestPathGuard:
    def test_accepts_path_within_root(self, tmp_path: Path):
        root = tmp_path / "fixtures"
        target = root / "exports" / "reviews.csv"
        target.parent.mkdir(parents=True)
        target.write_text("data")
        assert require_path_within(target, root, "quarantine") == target.resolve()

    def test_rejects_path_outside_root(self, tmp_path: Path):
        root = tmp_path / "fixtures"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "secrets.csv"
        with pytest.raises(NamespaceViolation, match="outside"):
            require_path_within(outside, root, "quarantine")

    def test_rejects_traversal_escape(self, tmp_path: Path):
        root = tmp_path / "fixtures"
        root.mkdir()
        with pytest.raises(NamespaceViolation, match="outside"):
            require_path_within(root / ".." / "escaped.csv", root, "purge")

    def test_rejects_the_root_itself(self, tmp_path: Path):
        # Purging the root would wipe every fixture rather than one artifact.
        root = tmp_path / "fixtures"
        root.mkdir()
        with pytest.raises(NamespaceViolation, match="root"):
            require_path_within(root, root, "purge")


class TestNamespaceConstruction:
    def test_empty_prefix_rejected(self):
        # An empty prefix would make every check pass.
        with pytest.raises(ValueError, match="empty"):
            Namespace(
                project_slug="x", urn_prefix="", project_tag="t", domain="d"
            )

    def test_empty_tag_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            Namespace(project_slug="x", urn_prefix="license.", project_tag="", domain="d")
