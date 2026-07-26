"""Parser regressions pinned to payloads captured from the live MCP server.

The third live-gate failure, and the most misleading. The transport worked and
tool discovery passed; the seed was correct -- a read-only DB audit confirmed all
12 allowlisted ``dataset`` URNs active. Readiness still reported **12/12 entities
unusable**, because the normalizers did not recognize the envelope and returned
``[]`` for it. "I cannot read the response" rendered identically to "the data is
not there."

Every payload in this module is the exact shape observed from the recovered
instance, not an invention:

- ``get_entities`` -> ``{"result": [entity, ...]}`` with ``properties``,
  ``tags``, and ``domain`` each nested one level deeper than the old code
  assumed, and ``customProperties`` as a list of ``{key, value}`` pairs;
- ``get_lineage`` -> ``{"downstreams": {"total": N, "searchResults":
  [{"entity": {"urn": ...}, "degree": D}]}}``.

Nothing here asserts on source text. Each test drives the real normalizer with a
captured payload and checks the governance-relevant fact that comes out of it.

``TestLineageCountsAreNumbersNotBooleans`` came later, from the coordinator's
pre-deployment review of the parser fix itself. Its payloads are deliberately
*not* captured shapes -- they are the malformed ones the parser must refuse, and
the point is that refusing them is the only safe reading.
"""

from __future__ import annotations

import pytest

from adapters.datahub import (
    EntityContext,
    PayloadError,
    _custom_properties,
    _domain_urn,
    _is_active,
    _iter_entities,
    _tag_names,
    _to_entity_context,
    _to_lineage_edges,
)

SOURCE = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)"
NORMALIZED = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.normalized,PROD)"
MODEL = "urn:li:dataset:(urn:li:dataPlatform:mlflow,license.models.review_sentiment,PROD)"

PROJECT_TAG = "project-license-circuit-breaker"
FIXTURE_MARKER = "lcb-demo-fixture"
DOMAIN_URN = "urn:li:domain:demo-license-circuit-breaker"


def captured_entity(urn: str = SOURCE) -> dict:
    """One entity exactly as the live server returns it."""
    return {
        "urn": urn,
        "type": "dataset",
        "name": urn,
        "properties": {
            "name": urn,
            "description": "Licensed partner review feed. Subject of the rights revocation.",
            "customProperties": [
                {"key": "artifact_class", "value": "dataset"},
                {"key": "purposes", "value": "analytics,retrieval,training"},
                {"key": "exposure", "value": "internal"},
                {"key": "criticality", "value": "high"},
                {"key": "rebuildable", "value": "false"},
                {"key": "fixture_marker", "value": FIXTURE_MARKER},
            ],
        },
        "tags": {
            "tags": [
                {"tag": {"urn": f"urn:li:tag:{FIXTURE_MARKER}"}},
                {"tag": {"urn": f"urn:li:tag:{PROJECT_TAG}"}},
            ]
        },
        "domain": {"domain": {"urn": DOMAIN_URN, "type": "DOMAIN"}},
    }


def captured_entities_payload(*urns: str) -> dict:
    """The full ``get_entities`` envelope."""
    return {"result": [captured_entity(urn) for urn in (urns or (SOURCE,))]}


def captured_lineage_payload(*, total: int = 1, degree: int = 1, urn: str = NORMALIZED) -> dict:
    """The full ``get_lineage`` envelope."""
    return {
        "downstreams": {
            "total": total,
            "searchResults": [{"entity": {"urn": urn}, "degree": degree}],
            "start": 0,
            "count": 10,
        }
    }


class TestEntityEnvelope:
    """``{"result": [...]}`` -- the key the old normalizer never looked for."""

    def test_the_result_key_is_unwrapped(self):
        entities = _iter_entities(captured_entities_payload(SOURCE, NORMALIZED))

        assert [e["urn"] for e in entities] == [SOURCE, NORMALIZED]

    def test_an_empty_result_is_an_empty_list_not_an_error(self):
        assert _iter_entities({"result": []}) == []

    def test_a_missing_result_key_raises_rather_than_reporting_nothing(self):
        """The exact regression: silence read as absence.

        The old code returned ``[]`` here, so readiness said 12 entities were
        missing from an instance that held all 12.
        """
        with pytest.raises(PayloadError, match="no 'result' key"):
            _iter_entities({"entities": [captured_entity()]})

    def test_the_error_names_the_keys_it_did_get(self):
        with pytest.raises(PayloadError, match="entities"):
            _iter_entities({"entities": []})

    @pytest.mark.parametrize("payload", [None, [], "result", 42])
    def test_a_non_object_payload_raises(self, payload):
        with pytest.raises(PayloadError):
            _iter_entities(payload)

    def test_a_non_list_result_raises(self):
        with pytest.raises(PayloadError, match="must be a list"):
            _iter_entities({"result": {"urn": SOURCE}})

    def test_a_non_object_entity_raises(self):
        with pytest.raises(PayloadError, match=r"result\[1\]"):
            _iter_entities({"result": [captured_entity(), "not-an-entity"]})


class TestEntityContextFromCapturedPayload:
    """Every governance-relevant field readiness reads."""

    @pytest.fixture
    def context(self) -> EntityContext:
        return _to_entity_context(SOURCE, captured_entity())

    def test_the_urn_and_type_survive(self, context):
        assert context.urn == SOURCE
        assert context.entity_type == "dataset"

    def test_the_nested_project_tag_is_found(self, context):
        # tags.tags[].tag.urn -- three levels deeper than the old code looked.
        assert context.has_tag(PROJECT_TAG)

    def test_the_nested_fixture_marker_is_found(self, context):
        assert context.has_tag(FIXTURE_MARKER)

    def test_tag_urns_are_reduced_to_bare_names(self, context):
        assert context.tags == (FIXTURE_MARKER, PROJECT_TAG)

    def test_the_old_parser_would_have_read_the_literal_string_tags(self, context):
        """Iterating the outer object yielded ``"tags"`` as the only tag name."""
        assert "tags" not in context.tags

    def test_the_domain_urn_is_extracted(self, context):
        # readiness compares against adapters.catalog.domain_urn, so the URN --
        # not a display name -- is what has to come back.
        assert context.domain == DOMAIN_URN

    def test_the_required_custom_properties_are_present(self, context):
        assert context.missing_properties() == frozenset()

    def test_custom_properties_come_back_as_a_mapping(self, context):
        assert context.custom_properties["artifact_class"] == "dataset"
        assert context.custom_properties["purposes"] == "analytics,retrieval,training"

    def test_the_description_comes_from_the_properties_aspect(self, context):
        assert context.description is not None
        assert "partner review feed" in context.description

    def test_an_entity_with_no_status_is_active(self, context):
        # The observed envelope carries no status for entities known active.
        assert context.active is True

    def test_the_seeded_entity_passes_every_readiness_predicate(self, context):
        """The whole point: this entity must not read as unusable."""
        assert context.active
        assert context.has_tag(FIXTURE_MARKER)
        assert context.has_tag(PROJECT_TAG)
        assert not context.missing_properties()
        assert context.domain is not None


class TestActiveStatusSemantics:
    def test_an_explicit_removed_false_is_active(self):
        assert _is_active({"status": {"removed": False}}) is True

    def test_an_explicit_removed_true_is_inactive(self):
        assert _is_active({"status": {"removed": True}}) is False

    def test_a_status_without_removed_is_active(self):
        assert _is_active({"status": {}}) is True

    def test_a_string_removed_raises_rather_than_being_coerced(self):
        # bool("false") is True, which would report a soft-deleted entity as live.
        with pytest.raises(PayloadError, match="must be a boolean"):
            _is_active({"status": {"removed": "false"}})

    def test_a_soft_deleted_entity_reads_as_inactive_end_to_end(self):
        raw = captured_entity()
        raw["status"] = {"removed": True}

        assert _to_entity_context(SOURCE, raw).active is False


class TestCustomPropertyPairs:
    def test_the_observed_list_of_pairs_becomes_a_mapping(self):
        pairs = [{"key": "artifact_class", "value": "model"}, {"key": "purposes", "value": "x,y"}]

        assert _custom_properties(pairs) == {"artifact_class": "model", "purposes": "x,y"}

    def test_an_absent_aspect_is_empty(self):
        assert _custom_properties(None) == {}

    def test_an_empty_list_is_empty(self):
        assert _custom_properties([]) == {}

    def test_a_missing_value_becomes_an_empty_string(self):
        assert _custom_properties([{"key": "purposes"}]) == {"purposes": ""}

    def test_a_mapping_raises_rather_than_being_accepted(self):
        # The old code called dict() on whatever it found; a mapping here would
        # mean the server changed shape and the pairs are no longer pairs.
        with pytest.raises(PayloadError, match="must be a list"):
            _custom_properties({"artifact_class": "dataset"})

    def test_a_pair_without_a_key_raises(self):
        with pytest.raises(PayloadError, match=r"customProperties\[0\]"):
            _custom_properties([{"value": "orphaned"}])


class TestTagAspect:
    def test_an_absent_aspect_means_untagged(self):
        assert _tag_names(None) == ()

    def test_an_empty_tag_list_means_untagged(self):
        assert _tag_names({"tags": []}) == ()

    def test_a_missing_inner_tags_key_means_untagged(self):
        assert _tag_names({}) == ()

    def test_a_non_urn_tag_value_is_left_alone(self):
        assert _tag_names({"tags": [{"tag": {"urn": "bare-name"}}]}) == ("bare-name",)

    def test_a_flat_tag_list_raises(self):
        with pytest.raises(PayloadError):
            _tag_names({"tags": ["lcb-demo-fixture"]})

    def test_a_tag_without_a_urn_raises(self):
        with pytest.raises(PayloadError, match="no urn"):
            _tag_names({"tags": [{"tag": {}}]})


class TestDomainAspect:
    def test_the_nested_urn_is_extracted(self):
        assert _domain_urn({"domain": {"urn": DOMAIN_URN}}) == DOMAIN_URN

    def test_an_absent_aspect_is_none(self):
        assert _domain_urn(None) is None

    def test_an_aspect_with_no_domain_is_none(self):
        assert _domain_urn({}) is None

    def test_a_domain_without_a_urn_raises(self):
        with pytest.raises(PayloadError, match="no urn"):
            _domain_urn({"domain": {"type": "DOMAIN"}})

    def test_a_flat_domain_urn_raises(self):
        # The old code accepted this shape and returned None for the real one.
        with pytest.raises(PayloadError):
            _domain_urn(DOMAIN_URN)


class TestLineageEnvelope:
    """``{"downstreams": {"total": N, "searchResults": [...]}}``."""

    def test_the_captured_payload_yields_one_exact_edge(self):
        edges = _to_lineage_edges(SOURCE, captured_lineage_payload())

        assert len(edges) == 1
        assert edges[0].upstream_urn == SOURCE
        assert edges[0].downstream_urn == NORMALIZED
        assert edges[0].resolved is True

    def test_a_degree_one_descendant_is_a_provable_edge(self):
        edges = _to_lineage_edges(SOURCE, captured_lineage_payload(degree=1))

        assert edges[0].resolved is True

    def test_a_deeper_descendant_is_reported_but_not_claimed_as_a_hop(self):
        """``degree`` says how far, never through what.

        Emitting a degree-3 descendant as a one-hop edge would let the report
        cite a lineage path that does not exist. Marking it unresolved keeps the
        descendant in scope and escalates instead of inventing evidence.
        """
        edges = _to_lineage_edges(SOURCE, captured_lineage_payload(degree=3, urn=MODEL))

        assert edges[0].downstream_urn == MODEL
        assert edges[0].resolved is False

    def test_an_exact_empty_downstream_set_is_not_an_error(self):
        payload = {"downstreams": {"total": 0, "searchResults": []}}

        assert _to_lineage_edges(SOURCE, payload) == []

    def test_an_empty_set_with_the_key_omitted_is_accepted_when_total_is_zero(self):
        assert _to_lineage_edges(SOURCE, {"downstreams": {"total": 0}}) == []

    def test_an_empty_set_with_no_total_raises(self):
        """Absence is not zero.

        A missing ``searchResults`` beside a missing ``total`` is two dropped
        keys, not a node with no descendants. Accepting it returned an empty
        impact set from a payload that proved nothing -- the fail-open direction,
        because an empty impact set is a clean bill of health.
        """
        with pytest.raises(PayloadError, match="no proof"):
            _to_lineage_edges(SOURCE, {"downstreams": {}})

    def test_a_dropped_results_key_with_a_nonzero_total_raises(self):
        # Otherwise a dropped key reads as "this node has no descendants".
        with pytest.raises(PayloadError, match="carries no 'searchResults'"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": 4}})

    def test_a_truncated_result_set_raises(self):
        """A partial descendant set is a smaller blast radius than the real one."""
        payload = captured_lineage_payload(total=7)

        with pytest.raises(PayloadError, match="truncated"):
            _to_lineage_edges(SOURCE, payload)

    def test_a_missing_downstreams_key_raises(self):
        with pytest.raises(PayloadError, match="no 'downstreams' key"):
            _to_lineage_edges(SOURCE, {"relationships": []})

    def test_the_old_flat_shape_is_no_longer_guessed_at(self):
        with pytest.raises(PayloadError):
            _to_lineage_edges(SOURCE, {"entities": [{"urn": NORMALIZED}]})

    def test_an_entity_without_a_urn_raises(self):
        payload = {"downstreams": {"total": 1, "searchResults": [{"entity": {}, "degree": 1}]}}

        with pytest.raises(PayloadError, match="has no urn"):
            _to_lineage_edges(SOURCE, payload)

    def test_a_missing_degree_raises(self):
        payload = {
            "downstreams": {"total": 1, "searchResults": [{"entity": {"urn": NORMALIZED}}]}
        }

        with pytest.raises(PayloadError, match="degree"):
            _to_lineage_edges(SOURCE, payload)

    def test_a_non_integer_degree_raises(self):
        payload = {
            "downstreams": {
                "total": 1,
                "searchResults": [{"entity": {"urn": NORMALIZED}, "degree": "1"}],
            }
        }

        with pytest.raises(PayloadError, match="degree"):
            _to_lineage_edges(SOURCE, payload)

    def test_a_stringified_entity_is_never_used_as_a_urn(self):
        """``str(dict)`` produced a URN-shaped nonsense string in the old code."""
        edges = _to_lineage_edges(SOURCE, captured_lineage_payload())

        assert edges[0].downstream_urn.startswith("urn:li:dataset:")
        assert "{" not in edges[0].downstream_urn


class TestLineageCountsAreNumbersNotBooleans:
    """The counts in this envelope must be genuine non-negative integers.

    Found by the coordinator's independent pre-deployment review of the payload
    parser candidate. ``bool`` subclasses ``int`` in Python, so the previous
    ``isinstance(total, int)`` and ``isinstance(degree, int)`` guards admitted
    ``True`` and ``False`` as numbers. Both admissions fail *open*:

    - ``total=False`` compared equal to 0, which is the one value that lets a
      missing ``searchResults`` be read as "this node has no descendants" -- so a
      malformed payload returned an empty impact set, a clean bill of health;
    - ``degree=True`` compared equal to 1, which is the one degree this envelope
      treats as a *provable* lineage edge -- so a malformed payload produced an
      edge the server never asserted.

    Neither shape has ever been observed from the live server. That is the point:
    a shape this project cannot explain must raise, not resolve to the most
    permissive reading of itself.
    """

    def test_the_language_trap_this_class_exists_for(self):
        """Documented, not assumed -- the guard is only necessary because of this."""
        assert isinstance(False, int) and isinstance(True, int)
        assert False == 0 and True == 1

    # -- total ----------------------------------------------------------

    def test_a_false_total_is_not_an_empty_descendant_set(self):
        """The exact defect. ``False == 0`` returned ``[]`` -- no impact at all."""
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": False}})

    def test_a_true_total_is_not_the_number_one(self):
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": True}})

    def test_a_boolean_total_raises_even_beside_a_real_result_set(self):
        """``True`` would have passed the truncation check against one result."""
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, captured_lineage_payload(total=True))

    def test_a_false_total_raises_even_beside_a_real_result_set(self):
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, captured_lineage_payload(total=False))

    def test_the_boolean_total_error_names_the_field_and_the_value(self):
        with pytest.raises(PayloadError, match=r"downstreams\.total.*False"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": False}})

    def test_a_float_total_raises(self):
        # 0.0 == 0, so a float zero is the same fail-open reading as False.
        with pytest.raises(PayloadError, match=r"downstreams\.total"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": 0.0}})

    def test_a_negative_total_raises(self):
        """There is no descendant set of size -1, and it defeats truncation."""
        with pytest.raises(PayloadError, match="negative"):
            _to_lineage_edges(SOURCE, captured_lineage_payload(total=-1))

    def test_a_negative_total_raises_with_no_result_set(self):
        with pytest.raises(PayloadError, match="negative"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": -1}})

    @pytest.mark.parametrize("total", ["0", [], {}, "many"])
    def test_a_malformed_total_raises(self, total):
        with pytest.raises(PayloadError, match=r"downstreams\.total"):
            _to_lineage_edges(SOURCE, {"downstreams": {"total": total}})

    def test_an_exact_integer_zero_is_still_the_one_accepted_empty_set(self):
        assert _to_lineage_edges(SOURCE, {"downstreams": {"total": 0}}) == []

    # -- degree ---------------------------------------------------------

    def _with_degree(self, degree) -> dict:
        return {
            "downstreams": {
                "total": 1,
                "searchResults": [{"entity": {"urn": NORMALIZED}, "degree": degree}],
            }
        }

    def test_a_true_degree_is_not_a_provable_one_hop_edge(self):
        """``True == 1`` would have emitted ``resolved=True`` from a non-number."""
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, self._with_degree(True))

    def test_a_false_degree_raises_rather_than_becoming_an_unresolved_edge(self):
        with pytest.raises(PayloadError, match="boolean"):
            _to_lineage_edges(SOURCE, self._with_degree(False))

    def test_the_boolean_degree_error_names_the_indexed_field(self):
        with pytest.raises(PayloadError, match=r"searchResults\[0\]\.degree.*True"):
            _to_lineage_edges(SOURCE, self._with_degree(True))

    def test_a_float_degree_raises(self):
        with pytest.raises(PayloadError, match=r"searchResults\[0\]\.degree"):
            _to_lineage_edges(SOURCE, self._with_degree(1.0))

    def test_a_negative_degree_raises(self):
        with pytest.raises(PayloadError, match="negative"):
            _to_lineage_edges(SOURCE, self._with_degree(-1))

    def test_a_missing_degree_raises(self):
        with pytest.raises(PayloadError, match=r"searchResults\[0\]\.degree"):
            _to_lineage_edges(SOURCE, self._with_degree(None))

    @pytest.mark.parametrize("degree", ["1", [], {}])
    def test_a_malformed_degree_raises(self, degree):
        with pytest.raises(PayloadError, match=r"searchResults\[0\]\.degree"):
            _to_lineage_edges(SOURCE, self._with_degree(degree))

    def test_a_real_integer_degree_still_resolves(self):
        """The guard must not cost the parser the payloads it was written for."""
        assert _to_lineage_edges(SOURCE, self._with_degree(1))[0].resolved is True
        assert _to_lineage_edges(SOURCE, self._with_degree(2))[0].resolved is False

    # -- audit of the remaining count-like fields in this parser ---------

    def test_the_paging_fields_are_not_consumed_at_all(self):
        """``start`` and ``count`` ride along in the captured envelope.

        Neither reaches a decision, so neither can carry the trap. Asserted
        rather than assumed: if a later change starts reading them, this test is
        where the same guard has to be applied.
        """
        payload = captured_lineage_payload()
        payload["downstreams"]["start"] = False
        payload["downstreams"]["count"] = True

        edges = _to_lineage_edges(SOURCE, payload)

        assert [(e.downstream_urn, e.resolved) for e in edges] == [(NORMALIZED, True)]

    def test_the_only_boolean_field_in_the_parser_rejects_integers(self):
        """The same trap in the other direction, and already closed.

        ``status.removed`` is the one field that genuinely *is* a boolean, and it
        must not accept ``1``/``0`` any more than a count accepts ``True``.
        """
        for value in (1, 0):
            with pytest.raises(PayloadError, match="boolean"):
                _is_active({"status": {"removed": value}})


class TestParsersAreWiredIntoTheClient:
    """The normalizers must be what the live client actually calls."""

    def _client(self, payloads: dict):
        from adapters.datahub import LiveDataHubClient
        from adapters.mcp_client import ToolSchema
        from app.namespace import Namespace

        namespace = Namespace(
            project_slug="license-circuit-breaker",
            urn_prefix="license.",
            project_tag=PROJECT_TAG,
            domain="Demo / License Circuit Breaker",
        )

        class _Transport:
            def tool_names(self):
                return frozenset(payloads)

            def schema_for(self, name):
                return ToolSchema(
                    name=name,
                    input_schema={
                        "properties": {
                            "urn": {},
                            "urns": {},
                            "upstream": {},
                            "max_hops": {},
                            "max_results": {},
                        }
                    },
                )

            def call(self, name, arguments):
                return payloads[name]

        client = LiveDataHubClient(
            gms_url="http://gms.invalid",
            mcp_url="http://mcp.invalid/mcp",
            token="fixture-token",
            namespace=namespace,
        )
        client._transport = _Transport()
        return client

    def test_get_entities_reads_the_captured_envelope(self):
        client = self._client({"get_entities": captured_entities_payload(SOURCE, NORMALIZED)})

        contexts = client.get_entities([SOURCE, NORMALIZED])

        assert set(contexts) == {SOURCE, NORMALIZED}
        assert contexts[SOURCE].has_tag(PROJECT_TAG)
        assert contexts[SOURCE].domain == DOMAIN_URN
        assert not contexts[SOURCE].missing_properties()

    def test_get_downstream_lineage_reads_the_captured_envelope(self):
        client = self._client({"get_lineage": captured_lineage_payload()})

        edges = client.get_downstream_lineage(SOURCE)

        assert [(e.upstream_urn, e.downstream_urn) for e in edges] == [(SOURCE, NORMALIZED)]

    def test_has_edge_confirms_a_declared_fixture_edge(self):
        """Readiness verifies each declared edge, one exact question at a time."""
        client = self._client({"get_lineage": captured_lineage_payload()})

        assert client.has_edge(SOURCE, NORMALIZED) is True
        assert client.has_edge(SOURCE, MODEL) is False

    def test_has_edge_rejects_an_indirect_descendant(self):
        # degree 3 proves descent, not a direct edge.
        client = self._client({"get_lineage": captured_lineage_payload(degree=3, urn=MODEL)})

        assert client.has_edge(SOURCE, MODEL) is False

    def test_an_unreadable_payload_surfaces_rather_than_reading_as_empty(self):
        client = self._client({"get_entities": {"entities": []}})

        with pytest.raises(PayloadError):
            client.get_entities([SOURCE])
