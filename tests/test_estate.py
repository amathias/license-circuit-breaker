"""Local data estate tests.

The estate is the part of the product that makes containment falsifiable, so
these tests care about two things above all: that the artifacts are genuinely
built and genuinely served *before* containment, and that the build is
deterministic enough for "the rebuild reproduced it" to be a meaningful claim.
"""

from __future__ import annotations

import pytest

from demo import graph
from demo.corpus import APPROVED_REVIEWS, PARTNER_PREFIX, PARTNER_REVIEWS, normalize
from demo.estate import (
    ARTIFACTS_BY_URN,
    BLOCKED,
    LOCAL_ARTIFACTS,
    SERVING,
    EstateError,
    EstatePaths,
    ServingControl,
    active_version,
    build_estate,
    estate_status,
    export_path,
    index_manifest,
    load_index,
    read_table,
    reset_estate,
    resolve_artifact,
    training_manifest,
)
from demo.serving import ServingRefused, fetch_export, predict, search
from demo.tfidf import cosine, fit_tfidf, tokenize


@pytest.fixture
def paths(tmp_path) -> EstatePaths:
    built = EstatePaths.under(tmp_path)
    build_estate(built)
    return built


class TestTfidf:
    def test_tokenizer_drops_single_characters_and_lowercases(self):
        assert tokenize("A Battery, the 4 CHARGE") == ["battery", "the", "charge"]

    def test_vectors_are_l2_normalized(self):
        model = fit_tfidf(["battery charge fast", "screen cracked slow"])
        vector = model.transform("battery charge fast")
        magnitude = sum(value * value for value in vector.values()) ** 0.5
        assert magnitude == pytest.approx(1.0)

    def test_out_of_vocabulary_terms_are_dropped(self):
        # A query must never introduce dimensions the index does not have,
        # or similarity scores stop being comparable across documents.
        model = fit_tfidf(["battery charge"])
        assert model.transform("battery quokka") == {"battery": pytest.approx(1.0)}

    def test_unrelated_documents_score_zero(self):
        model = fit_tfidf(["battery charge fast", "screen cracked slow"])
        assert cosine(model.transform("battery charge"), model.transform("screen cracked")) == 0.0

    def test_fit_is_deterministic(self):
        documents = [review.text for review in PARTNER_REVIEWS]
        assert fit_tfidf(documents).to_dict() == fit_tfidf(documents).to_dict()


class TestBuild:
    def test_builds_the_full_warehouse_chain(self, paths):
        for table in ("partner_feed", "approved_feed", "normalized", "review_sentiment"):
            assert read_table(paths, table), f"{table} is empty"

    def test_normalized_derives_from_the_partner_feed(self, paths):
        rows = read_table(paths, "normalized")
        assert len(rows) == len(PARTNER_REVIEWS)
        assert {row["source_feed"] for row in rows} == {"partner"}
        assert rows[0]["text"] == normalize(rows[0]["text"])

    def test_analytics_branch_covers_every_rating(self, paths):
        volumes = read_table(paths, "review_volume")
        assert sum(int(row["review_count"]) for row in volumes) == len(PARTNER_REVIEWS)

    def test_build_is_deterministic(self, tmp_path):
        first = EstatePaths.under(tmp_path / "one")
        second = EstatePaths.under(tmp_path / "two")
        build_estate(first)
        build_estate(second)

        # Content hashes exclude build timestamps by construction; if they did
        # not, "the rebuild reproduced the index" would be unprovable.
        assert index_manifest(first)["content_hash"] == index_manifest(second)["content_hash"]
        assert (
            training_manifest(first, "review_sentiment")["content_hash"]
            == training_manifest(second, "review_sentiment")["content_hash"]
        )

    def test_rebuilding_over_an_existing_estate_converges(self, paths):
        before = index_manifest(paths)["content_hash"]
        result = build_estate(paths)
        assert result.rebuilt is True
        assert index_manifest(paths)["content_hash"] == before

    def test_reading_a_missing_table_fails_loudly(self, paths):
        with pytest.raises(EstateError, match="does not exist"):
            read_table(paths, "no_such_table")

    def test_reading_before_build_fails_loudly(self, tmp_path):
        with pytest.raises(EstateError, match="build the estate first"):
            read_table(EstatePaths.under(tmp_path), "partner_feed")


class TestExposureBeforeContainment:
    """The demo is worthless unless prohibited content is genuinely served first."""

    def test_index_holds_partner_derived_documents(self, paths):
        _model, entries = load_index(paths)
        assert entries
        assert all(entry["review_id"].startswith(PARTNER_PREFIX) for entry in entries)

    def test_search_returns_partner_derived_content(self, paths):
        hits = search(paths, "battery charge")
        assert hits
        assert any(hit.review_id.startswith(PARTNER_PREFIX) for hit in hits)

    def test_active_model_was_trained_on_partner_rows(self, paths):
        manifest = training_manifest(paths, "review_sentiment")
        assert manifest["training_sources"] == [graph.FEATURES]
        assert all(row.startswith(PARTNER_PREFIX) for row in manifest["row_ids"])

    def test_prediction_endpoint_answers(self, paths):
        prediction = predict(paths, "the battery lasts all weekend and charges fast")
        assert prediction.label == 1
        assert prediction.model_version == "v1"

    def test_export_is_published_and_readable(self, paths):
        content = fetch_export(paths)
        assert content.splitlines()[0].startswith("review_id")
        assert PARTNER_PREFIX in content

    def test_approved_branch_is_trained_only_on_approved_rows(self, paths):
        manifest = training_manifest(paths, "approved_sentiment")
        assert manifest["training_sources"] == [graph.REPLACEMENT_SOURCE]
        assert len(manifest["row_ids"]) == len(APPROVED_REVIEWS)
        assert not any(row.startswith(PARTNER_PREFIX) for row in manifest["row_ids"])


class TestServingControl:
    def test_defaults_to_serving(self, paths):
        assert ServingControl.load(paths.serving_path).state(graph.PREDICT_API) == SERVING

    def test_first_block_reports_a_change_and_the_second_does_not(self, paths):
        control = ServingControl.load(paths.serving_path)
        assert control.set_state(graph.PREDICT_API, BLOCKED, reason="test") is True
        assert control.set_state(graph.PREDICT_API, BLOCKED, reason="test") is False

    def test_state_survives_a_reload(self, paths):
        ServingControl.load(paths.serving_path).set_state(graph.PREDICT_API, BLOCKED, "test")
        assert ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)

    def test_unknown_state_is_refused(self, paths):
        with pytest.raises(EstateError, match="unknown serving state"):
            ServingControl.load(paths.serving_path).set_state(graph.PREDICT_API, "maybe")

    def test_blocked_service_refuses_to_predict(self, paths):
        ServingControl.load(paths.serving_path).set_state(
            graph.PREDICT_API, BLOCKED, "frozen by test"
        )
        with pytest.raises(ServingRefused) as excinfo:
            predict(paths, "anything at all")
        assert excinfo.value.urn == graph.PREDICT_API
        assert "frozen by test" in excinfo.value.reason

    def test_blocked_service_refuses_to_search(self, paths):
        ServingControl.load(paths.serving_path).set_state(graph.PREDICT_API, BLOCKED, "frozen")
        with pytest.raises(ServingRefused):
            search(paths, "battery")


class TestRegistry:
    def test_every_graph_node_resolves_to_a_local_artifact(self):
        # A URN in the impact plan with no local artifact would silently produce
        # an adapter that cannot act, so the two must stay in step.
        for node in graph.NODES:
            assert node.urn in ARTIFACTS_BY_URN, f"{node.urn} has no local artifact"

    def test_registry_covers_no_urns_outside_the_graph(self):
        assert {a.urn for a in LOCAL_ARTIFACTS} == {node.urn for node in graph.NODES}

    def test_unknown_urn_is_refused(self):
        with pytest.raises(EstateError, match="no local artifact"):
            resolve_artifact("urn:li:dataset:(urn:li:dataPlatform:duckdb,other.thing,PROD)")


class TestReset:
    def test_reset_removes_the_estate(self, paths):
        assert reset_estate(paths) is True
        assert not paths.root.exists()

    def test_reset_on_a_missing_estate_is_a_no_op(self, tmp_path):
        assert reset_estate(EstatePaths.under(tmp_path)) is False

    def test_reset_refuses_a_root_that_is_not_an_estate(self, tmp_path):
        # A misconfigured APP_STATE_DIR must not be able to turn a reset into a
        # wider delete of whatever directory it happens to point at.
        stray = EstatePaths(root=tmp_path / "not-an-estate")
        stray.root.mkdir()
        with pytest.raises(EstateError, match="not an estate root"):
            reset_estate(stray)
        assert stray.root.exists()

    def test_rebuild_after_reset_reproduces_the_estate(self, paths):
        before = index_manifest(paths)["content_hash"]
        reset_estate(paths)
        build_estate(paths)
        assert index_manifest(paths)["content_hash"] == before


class TestStatus:
    def test_reports_exposure_before_containment(self, paths):
        status = estate_status(paths)
        assert status["built"] is True
        assert status["index"]["holds_partner_rows"] is True
        assert status["model"]["holds_partner_rows"] is True
        assert status["export"]["published"] is True
        assert status["serving"][graph.PREDICT_API] == SERVING

    def test_reports_an_unbuilt_estate(self, tmp_path):
        status = estate_status(EstatePaths.under(tmp_path))
        assert status["built"] is False
        assert status["index"]["present"] is False
        assert active_version(EstatePaths.under(tmp_path), "review_sentiment") is None

    def test_missing_export_is_refused_rather_than_returning_empty(self, paths):
        export_path(paths).unlink()
        with pytest.raises(ServingRefused, match="quarantined"):
            fetch_export(paths)
