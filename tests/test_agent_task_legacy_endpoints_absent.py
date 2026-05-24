from fastapi.testclient import TestClient


def test_legacy_task_template_routes_are_not_registered():
    from app.main import app

    app.openapi_schema = None
    paths = app.openapi()["paths"]
    registered_paths = {getattr(route, "path", None) for route in app.routes}

    assert "/api/task-templates" not in paths
    assert "/api/task-templates" not in registered_paths
    assert "/api/agent-tasks/from-template" not in paths
    assert "/api/agent-tasks/from-template" not in registered_paths


def test_legacy_task_template_endpoints_are_unavailable():
    from app.main import app

    client = TestClient(app)

    assert client.get("/api/task-templates").status_code == 404
    response = client.post(
        "/api/agent-tasks/from-template",
        json={"template_id": "github_pr_review", "assignee_agent_id": "agent-1", "input": {}},
    )
    assert response.status_code in {404, 405}
