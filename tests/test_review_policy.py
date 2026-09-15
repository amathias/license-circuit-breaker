"""Missing evidence must remain visible and cannot authorize enforcement."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from adapters.fake_datahub import FakeDataHubClient
from app.config import Settings
from app.policy import PolicyError, get_policy, load_policy
from app.rights import Action
from app.workflow import WorkflowError, build_impact_plan
from demo import graph
from demo.events import demo_rights_event
from demo.seed import seed


@pytest.fixture
def catalog():
    ns = Settings(_env_file=None).namespace
    client = FakeDataHubClient(namespace=ns)
    seed(client, ns)
    return client, ns


@pytest.mark.parametrize("purposes", [None, "", "Training, Retrieval", "analytics,typo"])
def test_missing_purposes_escalate_artifact_and_dependent_export(catalog, purposes):
    client, ns = catalog
    entity = client.entities[graph.NORMALIZED]
    properties = dict(entity.custom_properties)
    if purposes is None:
        properties.pop("purposes", None)
    else:
        properties["purposes"] = purposes
    client.entities[graph.NORMALIZED] = replace(entity, custom_properties=properties)
    plan = build_impact_plan(client, demo_rights_event(), ns)
    for urn in (graph.NORMALIZED, graph.EXPORT):
        decision = plan.decision_for(urn)
        assert decision.actions == (Action.ESCALATE,)
        assert any("purpose" in issue for issue in decision.missing_evidence)
    assert plan.decision_for(graph.ANALYTICS).actions == (Action.NO_ACTION,)


def test_untagged_descendant_is_not_executable(catalog):
    client, ns = catalog
    client.entities[graph.NORMALIZED] = replace(client.entities[graph.NORMALIZED], tags=())
    plan = build_impact_plan(client, demo_rights_event(), ns)
    assert plan.decision_for(graph.NORMALIZED).actions == (Action.ESCALATE,)


def test_foreign_descendant_is_visible_but_never_in_enforcement_scope(catalog):
    client, ns = catalog
    urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,foreign.models.copy,PROD)"
    client.add_entity(urn, tags=(ns.project_tag,), custom_properties={
        "artifact_class": "model", "purposes": "training",
    })
    client.add_edge(graph.SOURCE, urn)
    plan = build_impact_plan(client, demo_rights_event(), ns)
    assert plan.decision_for(urn).actions == (Action.ESCALATE,)
    assert urn not in plan.enforcement_scope()
    assert not plan.all_clear


def test_discovery_continues_past_first_depth_window(catalog):
    client, ns = catalog
    parent = graph.SOURCE
    for index in range(1, 10):
        urn = f"urn:li:dataset:(urn:li:dataPlatform:duckdb,license.deep{index},PROD)"
        client.add_entity(urn, tags=(ns.project_tag,), domain=ns.domain, custom_properties={
            "artifact_class": "dataset", "purposes": "training",
        })
        client.add_edge(parent, urn)
        parent = urn
    plan = build_impact_plan(client, demo_rights_event(), ns)
    assert plan.decision_for(parent) is not None
    assert plan.decision_for(parent).paths[0].depth == 9


@pytest.mark.parametrize("value", ["", "revoked"])
def test_missing_or_revoked_replacement_grant_escalates_rebuilds(catalog, value):
    client, ns = catalog
    entity = client.entities[graph.REPLACEMENT_SOURCE]
    client.entities[graph.REPLACEMENT_SOURCE] = replace(entity, custom_properties={
        **entity.custom_properties, "rights_state": value,
    })
    plan = build_impact_plan(client, demo_rights_event(), ns)
    assert plan.decision_for(graph.NORMALIZED).is_escalation
    assert plan.decision_for(graph.MODEL).is_escalation


def test_plan_hash_binds_facts_even_when_actions_do_not_change(catalog):
    client, ns = catalog
    first = build_impact_plan(client, demo_rights_event(), ns)
    entity = client.entities[graph.NORMALIZED]
    client.entities[graph.NORMALIZED] = replace(entity, custom_properties={
        **entity.custom_properties, "purposes": "training,retrieval,export",
    })
    second = build_impact_plan(client, demo_rights_event(), ns)
    assert first.enforcement_scope() == second.enforcement_scope()
    assert first.plan_hash() != second.plan_hash()


def test_plan_hash_binds_policy_version(catalog):
    client, ns = catalog
    first = build_impact_plan(client, demo_rights_event(), ns)
    table = replace(get_policy(), version=999)
    second = build_impact_plan(client, demo_rights_event(), ns, table=table)
    assert first.enforcement_scope() == second.enforcement_scope()
    assert first.plan_hash() != second.plan_hash()


@pytest.mark.parametrize("condition", ["typo: true", "affected: 'false'", "artifact_class: typo"])
def test_policy_rejects_malformed_conditions(tmp_path, condition):
    path = tmp_path / "rules.yaml"
    path.write_text(
        f"rules:\n  - id: invalid\n    precedence: 1\n    when: {{{condition}}}\n"
        "    then: {actions: [escalate]}\n", encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_policy(path)


def test_future_event_cannot_authorize_enforcement(catalog):
    client, ns = catalog
    event = demo_rights_event().model_copy(update={
        "effective_at": datetime.now(UTC) + timedelta(days=1),
    })
    with pytest.raises(WorkflowError, match="not effective"):
        build_impact_plan(client, event, ns)


def test_unsupported_environment_is_not_ignored(catalog):
    client, ns = catalog
    event = demo_rights_event()
    event = event.model_copy(update={"new": event.new.model_copy(update={
        "environments": frozenset({"DEV"}),
    })})
    with pytest.raises(WorkflowError, match="PROD"):
        build_impact_plan(client, event, ns)
