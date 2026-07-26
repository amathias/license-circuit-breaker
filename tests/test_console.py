"""Judge console tests.

These cannot render React, and are not trying to. What they check is the thing
that actually breaks between a Python backend and a TypeScript frontend built in
separate steps: the contract between them drifting without either side failing
its own tests.

Every path the console fetches is asserted to exist on the FastAPI app, and
every field it reads is asserted to be present in a real response. A renamed
endpoint or a dropped key fails here rather than in a recorded demo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, reset_settings_cache
from app.main import WEB_DIST, app
from demo.estate import EstatePaths, build_estate

WEB = Path(__file__).resolve().parent.parent / "web"
API_SOURCE = WEB / "src" / "api.ts"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "offline")
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path / "state"))
    reset_settings_cache()
    build_estate(EstatePaths.under(get_settings().ensure_state_dir()))
    yield
    reset_settings_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _console_paths() -> set[str]:
    """Every ``/api/...`` literal the console's client fetches."""
    source = API_SOURCE.read_text(encoding="utf-8")
    found = set(re.findall(r"['\"`](/api/[a-z0-9/\-]+)", source))
    assert found, "no API paths found in the console client"
    return found


def _registered_paths() -> set[str]:
    """Every path the app serves, including those inside included routers.

    Read from the generated OpenAPI schema rather than by walking ``app.routes``.
    FastAPI keeps an included router as an opaque container in that list instead
    of flattening its endpoints onto the application, and the shape of that
    container is a private detail that has already changed once. The schema is
    the supported way to ask what the app serves, and it is what a judge reading
    ``/docs`` sees.
    """
    return set(app.openapi()["paths"])


class TestSourceLayout:
    def test_the_console_source_is_present(self):
        for expected in ("package.json", "index.html", "vite.config.ts", "tsconfig.json"):
            assert (WEB / expected).is_file(), f"web/{expected} is missing"
        for expected in ("main.tsx", "App.tsx", "api.ts", "types.ts", "styles.css"):
            assert (WEB / "src" / expected).is_file(), f"web/src/{expected} is missing"

    def test_build_and_typecheck_scripts_are_declared(self):
        manifest = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
        assert "build" in manifest["scripts"]
        assert "typecheck" in manifest["scripts"]
        # The build must typecheck, or a type error ships as a runtime error.
        assert "tsc" in manifest["scripts"]["build"]

    def test_typescript_runs_in_strict_mode(self):
        config = (WEB / "tsconfig.json").read_text(encoding="utf-8")
        assert '"strict": true' in config

    def test_the_console_declares_no_network_dependencies(self):
        # A CDN import would break a judge running the demo offline, and would
        # put an unaudited third party inside the governance UI.
        manifest = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
        assert set(manifest["dependencies"]) == {"react", "react-dom"}

        for source in (WEB / "src").rglob("*.ts*"):
            text = source.read_text(encoding="utf-8")
            assert "https://" not in text or "http://127.0.0.1" in text, (
                f"{source.name} references an external origin"
            )


class TestApiContract:
    def test_every_path_the_console_calls_exists(self, client):
        registered = _registered_paths()
        for path in sorted(_console_paths()):
            assert path in registered, f"the console calls {path}, which the API does not serve"

    def test_the_console_covers_every_workflow_stage(self):
        # If a stage's endpoint were dropped from the client, the console would
        # silently stop demonstrating that part of the workflow.
        paths = _console_paths()
        for required in (
            "/api/rights-event",
            "/api/graph",
            "/api/plan",
            "/api/approvals",
            "/api/execute",
            "/api/verify",
            "/api/writeback",
            "/api/evidence",
        ):
            assert required in paths, f"the console never calls {required}"

    def test_graph_response_carries_every_field_the_console_renders(self, client):
        node = client.get("/api/graph").json()["nodes"][0]
        for field in (
            "urn",
            "label",
            "is_source",
            "artifact_class",
            "purposes",
            "exposure",
            "criticality",
            "tags",
            "revocation_status",
            "decision",
        ):
            assert field in node, f"graph nodes lost the {field!r} field"

    def test_plan_response_carries_every_field_the_console_renders(self, client):
        body = client.get("/api/plan").json()
        for field in ("plan_hash", "decisions", "steps", "enforcement_scope", "all_clear"):
            assert field in body
        decision = body["decisions"][0]
        for field in (
            "descendant_urn",
            "artifact_class",
            "actions",
            "rule_ids",
            "priority",
            "missing_evidence",
        ):
            assert field in decision

    def test_evidence_response_carries_the_disclaimers_the_footer_shows(self, client):
        body = client.get("/api/evidence").json()
        assert "legal" in body["disclaimers"]
        assert "scope" in body["disclaimers"]

    def test_readiness_carries_the_namespace_the_footer_shows(self, client):
        body = client.get("/api/readiness").json()
        assert body["namespace"]["urn_prefix"] == "license."
        assert "simulated" in body


class TestStaticServing:
    def test_the_api_works_without_a_built_console(self, client):
        # A judge must be able to run the backend and the CLI demo without a
        # Node toolchain present.
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/plan").status_code == 200

    def test_the_console_is_served_when_it_has_been_built(self, client):
        if not WEB_DIST.is_dir():
            pytest.skip("web/dist is not built; run `npm --prefix web run build`")
        response = client.get("/")
        assert response.status_code == 200
        assert '<div id="root">' in response.text

    def test_the_static_mount_never_shadows_the_api(self, client):
        # The mount is added last for exactly this reason.
        assert client.get("/api/health").json()["status"] == "ok"


class TestReleaseSafety:
    def test_no_secrets_are_committed_in_the_console(self):
        suspicious = re.compile(
            r"(DATAHUB_TOKEN\s*=\s*['\"][^'\"]+|Bearer\s+[A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{12,}\.)"
        )
        sources = [*(WEB / "src").rglob("*.ts*"), WEB / "package.json", WEB / "index.html"]
        for source in sources:
            assert not suspicious.search(source.read_text(encoding="utf-8")), (
                f"{source.name} looks like it contains a credential"
            )

    def test_the_page_description_disclaims_legal_advice(self):
        markup = (WEB / "index.html").read_text(encoding="utf-8")
        assert "not legal advice" in markup.lower()

    def test_build_output_is_not_tracked_by_git(self):
        ignored = (WEB.parent / ".gitignore").read_text(encoding="utf-8")
        assert "web/dist" in ignored
        assert "node_modules" in ignored
