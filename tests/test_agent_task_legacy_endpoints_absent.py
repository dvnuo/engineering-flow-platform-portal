from fastapi.testclient import TestClient


LEGACY_TASK_TEMPLATES_PATH = "/api/" + "task-" + "templates"
LEGACY_AGENT_TASK_FROM_TEMPLATE_PATH = "/api/" + "agent-tasks/" + "from-" + "template"


def test_legacy_task_template_routes_are_not_registered():
    from app.main import app

    app.openapi_schema = None
    paths = app.openapi()["paths"]
    registered_paths = {getattr(route, "path", None) for route in app.routes}

    assert LEGACY_TASK_TEMPLATES_PATH not in paths
    assert LEGACY_TASK_TEMPLATES_PATH not in registered_paths
    assert LEGACY_AGENT_TASK_FROM_TEMPLATE_PATH not in paths
    assert LEGACY_AGENT_TASK_FROM_TEMPLATE_PATH not in registered_paths


def test_legacy_task_template_endpoints_are_unavailable():
    from app.main import app

    client = TestClient(app)

    assert client.get(LEGACY_TASK_TEMPLATES_PATH).status_code == 404
    response = client.post(
        LEGACY_AGENT_TASK_FROM_TEMPLATE_PATH,
        json={"template_id": "github_pr_review", "assignee_agent_id": "agent-1", "input": {}},
    )
    assert response.status_code in {404, 405}
