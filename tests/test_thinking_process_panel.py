import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _setup_thinking_panel_client(monkeypatch, chatlog_payload=None, *, metadata_record=None, k8s_enabled=True, status_code=200):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="private", status="running")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", k8s_enabled)
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(get_by_agent_and_session=lambda _agent_id, _session_id: metadata_record),
    )

    async def _fake_forward_runtime(**_kwargs):
        payload = chatlog_payload or {}
        return status_code, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    return TestClient(app)


def test_thinking_process_panel_renders_active_skill_from_top_level_skill_session(monkeypatch):
    chatlog = {
        "skill_session": {
            "schema_version": "active_skill_contract.v1",
            "skill_name": "review-pull-request",
            "status": "active",
            "goal": "Review PR #12",
            "turn_count": 2,
            "activation_reason": "continued",
            "skill_hash": "abc123",
            "allowed_tools": ["github_get_pull_request", "github_list_pull_request_files"],
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: review-pull-request" in response.text
    assert "Goal: Review PR #12" in response.text
    assert "Status: active" in response.text
    assert "Turn: 2" in response.text
    assert "Reason: continued" in response.text
    assert "Skill hash: abc123" in response.text
    assert "github_get_pull_request" in response.text


def test_thinking_process_panel_renders_active_skill_from_nested_metadata_session(monkeypatch):
    chatlog = {
        "metadata": {
            "active_skill_session": {
                "schema_version": "active_skill_contract.v1",
                "skill_name": "create-pull-request",
                "status": "active",
                "goal": "Create PR",
                "turn_count": 3,
                "activation_reason": "matched",
                "skill_hash": "def456",
                "allowed_tools": ["github_create_pull_request"],
            }
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: create-pull-request" in response.text
    assert "Goal: Create PR" in response.text
    assert "Turn: 3" in response.text
    assert "Reason: matched" in response.text
    assert "github_create_pull_request" in response.text


def test_thinking_process_panel_renders_active_skill_from_flat_metadata_fields(monkeypatch):
    chatlog = {
        "metadata": {
            "active_skill_name": "triage-incident",
            "active_skill_status": "active",
            "active_skill_goal": "Triage issue #77",
            "active_skill_turn_count": 0,
            "active_skill_activation_reason": "manual",
            "active_skill_hash": "flat999",
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: triage-incident" in response.text
    assert "Goal: Triage issue #77" in response.text
    assert "Turn: 0" in response.text


def test_thinking_process_panel_handles_non_mapping_metadata_and_skill_session(monkeypatch):
    chatlog = {
        "metadata": "bad-metadata",
        "skill_session": "bad-skill-session",
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert ("Thinking Process" in response.text) or ("Active Skill:" not in response.text)


def test_thinking_process_panel_uses_nested_skill_when_top_level_skill_session_is_invalid(monkeypatch):
    chatlog = {
        "skill_session": "bad-skill-session",
        "metadata": {
            "active_skill_session": {
                "schema_version": "active_skill_contract.v1",
                "skill_name": "review-pull-request",
                "status": "active",
                "goal": "Review PR #12",
                "turn_count": 2,
                "activation_reason": "continued",
                "skill_hash": "abc123",
                "allowed_tools": ["github_get_pull_request"],
            }
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: review-pull-request" in response.text
    assert "Goal: Review PR #12" in response.text
    assert "github_get_pull_request" in response.text


def test_thinking_process_panel_handles_non_mapping_llm_debug(monkeypatch):
    chatlog = {
        "llm_debug": "bad-llm-debug",
        "metadata": {
            "active_skill_name": "review-pull-request",
            "active_skill_status": "active",
            "active_skill_goal": "Review PR #12",
            "active_skill_turn_count": 2,
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: review-pull-request" in response.text
    assert "Goal: Review PR #12" in response.text
    assert "Turn: 2" in response.text


def test_thinking_process_panel_renders_context_budget(monkeypatch):
    context_state = {
        "objective": "Generate demo test cases",
        "summary": "User asked for test cases from Jira",
        "next_step": "Fetch Jira issue",
        "compaction_level": "none",
        "budget": {
            "usage_percent": 61.5,
            "prepared_usage_percent": 49.0,
            "estimated_tokens": 123000,
            "prepared_tokens": 98000,
            "context_window_tokens": 200000,
            "soft_threshold_percent": 65.0,
            "hard_threshold_percent": 80.0,
            "tokens_until_soft_threshold": 7000,
            "next_compaction_action": "approaching_micro_compaction",
        },
    }
    chatlog = {
        "session_id": "s-1",
        "timestamp": "2026-04-18T15:10:27Z",
        "context_state": context_state,
        "events": [{"type": "context_snapshot", "data": {"stage": "pre_request", "context_state": context_state}}],
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Context Window" in response.text
    assert "49.0%" in response.text
    assert "98000" in response.text
    assert "200000" in response.text
    assert "approaching_micro_compaction" in response.text
    assert "Context Contents" in response.text
    assert "Generate demo test cases" in response.text
    assert "Request over budget:" not in response.text


def test_thinking_process_panel_renders_new_projection_and_budget_diagnostics(monkeypatch):
    context_state = {
        "objective": "Keep context slim",
        "budget": {
            "request_estimated_tokens": 28000,
            "prompt_budget_tokens": 32000,
            "max_prompt_tokens": 32000,
            "reserved_output_tokens": 4000,
            "max_output_tokens": 64000,
            "projection_chars_saved": 9000,
            "projected_old_assistant_messages": 7,
            "projected_old_tool_messages": 3,
            "context_blob_refs_created": 2,
        },
    }
    chatlog = {"session_id": "s-1", "context_state": context_state}
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Request estimate: 28000 tokens" in response.text
    assert "Prompt budget: 32000 tokens" in response.text
    assert "Max prompt cap: 32000 tokens" in response.text
    assert "Reserved output: 4000 tokens" in response.text
    assert "Max output: 64000 tokens" in response.text
    assert "Projection saved: 9000 chars" in response.text
    assert "Projected assistant/tool messages: 7 / 3" in response.text
    assert "Context refs created: 2" in response.text


def test_thinking_process_panel_renders_optional_projection_output_guard_diagnostics(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "context_state": {
            "budget": {
                "projected_recent_assistant_messages": 4,
                "projected_plain_assistant_messages": 2,
                "assistant_projection_chars_saved": 1200,
                "output_size_guard_applied": True,
                "large_generation_guard_applied": True,
            }
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Recent assistant projected: 4" in response.text
    assert "Plain assistant projected: 2" in response.text
    assert "Assistant projection saved: 1200 chars" in response.text
    assert "Output guard: applied" in response.text
    assert "Large generation guard: applied" in response.text


def test_thinking_process_panel_renders_context_ref_list_as_count(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "context_state": {
            "budget": {
                "context_blob_refs_created": ["ctx://context/abc", "ctx://context/def"],
            }
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Context refs created: 2" in response.text
    assert "ctx://context/abc" not in response.text
    assert "ctx://context/def" not in response.text


def test_thinking_process_panel_renders_request_over_budget_yes_and_no(monkeypatch):
    chatlog_yes = {
        "session_id": "s-1",
        "context_state": {"budget": {"request_over_budget": True}},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog_yes)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Request over budget: yes" in response.text

    chatlog_no = {
        "session_id": "s-1",
        "context_state": {"budget": {"request_over_budget": False}},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog_no)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Request over budget: no" in response.text


def test_thinking_process_panel_renders_budget_stage_only_when_present(monkeypatch):
    chatlog_with_stage = {
        "session_id": "s-1",
        "context_state": {"budget": {"request_budget_stage": "skill_finalizer"}},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog_with_stage)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Budget stage: skill_finalizer" in response.text

    chatlog_without_stage = {
        "session_id": "s-1",
        "context_state": {"budget": {"request_over_budget": True}},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog_without_stage)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Budget stage:" not in response.text


def test_thinking_process_panel_metadata_fallback_when_runtime_disabled(monkeypatch):
    metadata_record = SimpleNamespace(
        session_id="s-1",
        metadata_json=json.dumps(
            {
                "context_summary_preview": "Fallback summary preview",
                "context_usage_percent": 44.2,
                "active_skill_name": "review-pull-request",
            }
        ),
        runtime_events_json="[]",
        latest_event_type="context_snapshot",
        latest_event_state="running",
        last_execution_id="req-1",
    )
    client = _setup_thinking_panel_client(
        monkeypatch,
        None,
        metadata_record=metadata_record,
        k8s_enabled=False,
    )
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Agent not running" not in response.text
    assert "Fallback summary preview" in response.text
    assert "Active Skill: review-pull-request" in response.text


def test_thinking_process_panel_renders_runtime_events(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "runtime_events": [
            {
                "event_type": "context_snapshot",
                "state": "running",
                "request_id": "r-1",
                "detail_payload": {
                    "stage": "pre_request",
                    "context_state": {
                        "summary": "Runtime event summary",
                        "budget": {"usage_percent": 42.0, "context_window_tokens": 1000},
                    },
                },
            }
        ],
        "context_state": {
            "summary": "Runtime event summary",
            "budget": {"usage_percent": 42.0, "context_window_tokens": 1000},
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Context Window" in response.text
    assert "42.0" in response.text
    assert ("context_snapshot" in response.text) or ("Context Snapshot" in response.text)
    assert "Runtime event summary" in response.text


def test_thinking_process_panel_fallback_renders_without_summary_when_budget_or_objective_exists(monkeypatch):
    metadata_record = SimpleNamespace(
        session_id="s-1",
        metadata_json=json.dumps(
            {
                "context_objective_preview": "Ship thinking panel",
                "context_usage_percent": 61.5,
                "context_estimated_tokens": 123000,
                "context_window_tokens": 200000,
                "context_next_compaction_action": "approaching_micro_compaction",
                "context_next_pruning_policy": "Approaching micro-compaction: older turns will be summarized.",
            }
        ),
        runtime_events_json="[]",
        latest_event_type=None,
        latest_event_state=None,
        last_execution_id=None,
    )
    client = _setup_thinking_panel_client(
        monkeypatch,
        None,
        metadata_record=metadata_record,
        k8s_enabled=False,
    )
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Agent not running" not in response.text
    assert "Ship thinking panel" in response.text
    assert "61.5" in response.text
    assert "123000" in response.text
    assert "approaching_micro_compaction" in response.text
    assert "Pruning policy" in response.text


def test_thinking_process_panel_handles_non_mapping_detail_payload(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "runtime_events": [
            {
                "event_type": "context_snapshot",
                "request_id": "r-1",
                "detail_payload": "not-a-dict",
            }
        ],
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "context_snapshot" in response.text


def test_thinking_process_panel_metadata_fallback_renders_budget_preview(monkeypatch):
    metadata_record = SimpleNamespace(
        session_id="s-1",
        metadata_json=json.dumps(
            {
                "context_objective_preview": "Ship thinking panel",
                "context_summary_preview": "Runtime unavailable fallback",
                "context_next_step_preview": "Open persisted panel",
                "context_usage_percent": 61.5,
                "context_estimated_tokens": 123000,
                "context_window_tokens": 200000,
                "context_next_compaction_action": "approaching_micro_compaction",
                "context_tokens_until_soft_threshold": 7000,
                "context_tokens_until_hard_threshold": 37000,
            }
        ),
        runtime_events_json="[]",
        latest_event_type="context_snapshot",
        latest_event_state="running",
        last_execution_id="req-2",
    )
    client = _setup_thinking_panel_client(
        monkeypatch,
        None,
        metadata_record=metadata_record,
        k8s_enabled=False,
    )
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Ship thinking panel" in response.text
    assert "Runtime unavailable fallback" in response.text
    assert "Open persisted panel" in response.text
    assert "61.5" in response.text
    assert "123000" in response.text
    assert "200000" in response.text
    assert "approaching_micro_compaction" in response.text
    assert "7000" in response.text
    assert "37000" in response.text


def test_thinking_process_panel_renders_pruning_policy_and_planned_payload(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "request_id": "r-1",
        "context_state": {
            "summary": "Context summary",
            "budget": {
                "usage_percent": 61.5,
                "context_window_tokens": 200000,
                "next_compaction_action": "approaching_micro_compaction",
                "next_pruning_policy": "Approaching micro-compaction: older turns will be summarized.",
            },
        },
        "runtime_events": [
            {
                "event_type": "context_compaction_planned",
                "request_id": "r-1",
                "detail_payload": {
                    "message": "Context is approaching micro-compaction threshold.",
                    "budget": {
                        "usage_percent": 61.5,
                        "next_pruning_policy": "Approaching micro-compaction: older turns will be summarized.",
                    },
                },
            }
        ],
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Pruning policy" in response.text
    assert "Approaching micro-compaction" in response.text
    assert "context_compaction_planned" in response.text
    assert "61.5" in response.text


def test_thinking_process_panel_ignores_empty_chatlog_context_state_when_event_has_final_context(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "request_id": "req-1",
        "context_state": {},
        "runtime_events": [
            {
                "event_type": "context_snapshot",
                "state": "completed",
                "request_id": "req-1",
                "detail_payload": {
                    "stage": "post_turn",
                    "terminal": True,
                    "context_state": {
                        "summary": "Final event context summary",
                        "next_step": "Open final snapshot",
                        "budget": {"usage_percent": 33.0, "context_window_tokens": 1000},
                    },
                },
            }
        ],
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Final event context summary" in response.text
    assert "Open final snapshot" in response.text
    assert 'data-thinking-has-data="1"' in response.text
    assert 'data-thinking-request-id="req-1"' in response.text
    assert "No context snapshot was captured" not in response.text


def test_thinking_process_panel_shows_empty_context_state_and_has_data_zero_when_truly_empty(monkeypatch):
    chatlog = {"session_id": "s-empty", "request_id": "req-empty", "events": []}
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-empty")
    assert response.status_code == 200
    assert "No context snapshot was captured for this run." in response.text
    assert 'data-thinking-panel-root="1"' in response.text
    assert 'data-thinking-has-data="0"' in response.text


def test_thinking_view_prefers_event_context_contents_over_budget_only_chatlog_context():
    from app.services.thinking_process_view import build_thinking_process_view

    chatlog = {
        "session_id": "s-1",
        "request_id": "req-1",
        "context_state": {
            "budget": {"usage_percent": 11.0}
        },
        "runtime_events": [
            {
                "event_type": "context_snapshot",
                "request_id": "req-1",
                "detail_payload": {
                    "stage": "post_turn",
                    "terminal": True,
                    "context_state": {
                        "summary": "Final event context summary",
                        "next_step": "Open final snapshot",
                        "budget": {"usage_percent": 33.0, "context_window_tokens": 1000},
                    },
                },
            }
        ],
    }

    view = build_thinking_process_view(chatlog)

    assert view["has_context"] is True
    assert view["context"]["summary"] == "Final event context summary"
    assert view["context"]["next_step"] == "Open final snapshot"
    assert view["context_source"] == "event"
    assert view["budget"]["usage_percent"] == 33.0


def test_thinking_view_budget_only_has_data_but_not_context():
    from app.services.thinking_process_view import build_thinking_process_view

    chatlog = {
        "session_id": "s-1",
        "request_id": "req-1",
        "context_state": {
            "budget": {"usage_percent": 11.0, "context_window_tokens": 1000}
        },
        "events": [],
    }

    view = build_thinking_process_view(chatlog)

    assert view["has_data"] is True
    assert view["has_context"] is False
    assert view["budget"]["usage_percent"] == 11.0
    assert "Context window only" in view["context_source_label"]


def test_thinking_view_merges_event_data_and_detail_payload_for_context_state():
    from app.services.thinking_process_view import build_thinking_process_view

    chatlog = {
        "session_id": "s-1",
        "request_id": "req-1",
        "context_state": {},
        "runtime_events": [
            {
                "type": "context_snapshot",
                "event_type": "context_snapshot",
                "request_id": "req-1",
                "data": {
                    "stage": "post_turn",
                    "terminal": True,
                },
                "detail_payload": {
                    "stage": "post_turn",
                    "terminal": True,
                    "context_state": {
                        "summary": "Detail payload summary",
                        "next_step": "Read merged detail payload",
                        "budget": {"usage_percent": 44.0},
                    },
                },
            }
        ],
    }

    view = build_thinking_process_view(chatlog)

    assert view["has_context"] is True
    assert view["context"]["summary"] == "Detail payload summary"
    assert view["context"]["next_step"] == "Read merged detail payload"
    assert view["budget"]["usage_percent"] == 44.0
    assert view["context_source"] == "event"


def test_thinking_view_empty_event_budget_does_not_block_context_state_budget_candidate():
    from app.services.thinking_process_view import build_thinking_process_view

    chatlog = {
        "session_id": "s-1",
        "request_id": "req-1",
        "context_state": {
            "budget": {"usage_percent": 11.0, "context_window_tokens": 1000}
        },
        "runtime_events": [
            {
                "type": "context_snapshot",
                "event_type": "context_snapshot",
                "data": {
                    "stage": "post_turn",
                    "terminal": True,
                    "context_state": {
                        "summary": "Event summary without budget",
                        "next_step": "Still keep chatlog budget",
                    },
                    "budget": {},
                },
            }
        ],
    }

    view = build_thinking_process_view(chatlog)

    assert view["has_context"] is True
    assert view["context"]["summary"] == "Event summary without budget"
    assert view["budget"]["usage_percent"] == 11.0
    assert view["budget"]["context_window_tokens"] == 1000


def test_thinking_process_panel_renders_safe_source_diagnostics_only(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "context_state": {
            "source": {
                "source_complete_for_generation": False,
                "source_complete_including_binary_bodies": True,
                "generation_mode": "staged",
                "output_risk_level": "high",
                "max_context_window_tokens": 400000,
                "max_prompt_tokens": 272000,
                "max_output_tokens": 128000,
                "effective_max_tokens": 128000,
                "legacy_max_tokens_ignored": True,
                "configured_max_tokens": 64000,
                "max_chat_output_tokens": 120000,
                "max_chat_output_chars": 480000,
                "output_boundary_source": "model_limits_derived",
                "legacy_max_chat_output_chars_ignored": True,
                "configured_max_chat_output_chars": 8000,
                "budget_max_chat_output_chars_ignored": True,
                "configured_budget_max_chat_output_chars": 8000,
                "arg_max_chat_output_chars_ignored": True,
                "configured_arg_max_chat_output_chars": 8000,
                "file_context_budget_status": "within_limit",
                "file_context_estimated_tokens": 1536,
                "file_context_threshold_source": "resolved_runtime_profile",
                "chars_per_token_estimate": 4.0,
                "input_context_usage_percent": 44.5,
                "output_controller_applied": True,
                "output_controller_stage": "tool_loop",
                "text_attachment_bodies_complete": True,
                "binary_attachment_bodies_available": False,
                "binary_attachment_bodies_skipped_count": 3,
                "binary_attachment_body_policy": "metadata_only",
                "source_tree_complete": True,
                "descendants_loaded": 3,
                "descendants_total": 9,
                "descendants_complete": False,
                "descendants_pages_complete": True,
                "descendants_comments_complete": False,
                "descendants_attachments_complete": True,
                "comments_bundle_ref_count": 4,
                "children_bundle_ref_count": 2,
                "jira_comments_bundle_ref_count": 6,
                "confluence_children_bundle_ref_count": 3,
                "auxiliary_source_session_valid": True,
                "auxiliary_source_complete": True,
                "source_bundle": {"raw": "SECRET_BUNDLE_CONTENT"},
                "jira_body": "SECRET_JIRA_BODY",
                "ctx_refs": ["ctx://source/abc"],
            },
            "generation": {
                "generated_artifact_ref_count": 2,
                "generation_done": False,
                "current_phase": "step_definitions",
                "next_phase": "finalize",
                "completed_phases_count": 4,
                "completion_criteria_count": 6,
                "source_digest_chunk_coverage_count": 5,
                "completion_criteria_status_count": 8,
                "completion_criteria_satisfied_count": 6,
                "next_incomplete_phase": "publish",
                "generated_artifacts_by_phase_count": 5,
                "current_phase_artifact_count": 2,
                "generation_completion_criteria_met": 4,
                "generation_completion_criteria_total": 7,
            },
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Source complete including binary bodies: yes" in response.text
    assert "Generation mode: staged" in response.text
    assert "Output risk level: high" in response.text
    assert "Context window: 400000 tokens" in response.text
    assert "Prompt limit: 272000 tokens" in response.text
    assert "Output limit: 128000 tokens" in response.text
    assert "Effective provider max tokens: 128000" in response.text
    assert "Chat output boundary: 120000 tokens / 480000 chars" in response.text
    assert "Output boundary source: model_limits_derived" in response.text
    assert "File context budget status: within_limit" in response.text
    assert "File context estimated tokens: 1536" in response.text
    assert "File context threshold source: resolved_runtime_profile" in response.text
    assert "Legacy max_tokens ignored: configured 64000, effective 128000" in response.text
    assert "Legacy low output boundary ignored: configured 8000 chars, effective 480000 chars" in response.text
    assert "Stale session output boundary ignored: configured 8000 chars, effective 480000 chars" in response.text
    assert "Runtime output boundary argument ignored: configured 8000 chars, effective 480000 chars" in response.text
    assert "Chars/token estimate: 4.0" in response.text
    assert "Input context usage: 44.5%" in response.text
    assert "Input context usage and output limit are separate; low input context usage does not guarantee low output risk." in response.text
    assert "Max chat output chars: 480000" in response.text
    assert "Output controller applied: yes" in response.text
    assert "Output controller stage: tool_loop" in response.text
    assert "Text attachment bodies complete: yes" in response.text
    assert "Binary attachment bodies available: no" in response.text
    assert "Binary attachment bodies skipped: 3" in response.text
    assert "Binary attachment body policy: metadata_only" in response.text
    assert "Source tree complete: yes" in response.text
    assert "Descendants loaded: 3/9" in response.text
    assert "Descendants complete: no" in response.text
    assert "Descendant pages complete: yes" in response.text
    assert "Descendant comments complete: no" in response.text
    assert "Descendant attachments complete: yes" in response.text
    assert "Comments bundle refs: 4" in response.text
    assert "Children bundle refs: 2" in response.text
    assert "Jira comment bundles: 6" in response.text
    assert "Confluence child bundles: 3" in response.text
    assert "Auxiliary source session valid: yes" in response.text
    assert "Auxiliary source complete: yes" in response.text
    assert "Generated artifacts: 2" in response.text
    assert "Generation done: no" in response.text
    assert "Completion criteria: 6" in response.text
    assert "Completion criteria satisfied: 6/8" in response.text
    assert "Next incomplete phase: publish" in response.text
    assert "Digest chunk coverage: 5" in response.text
    assert "Current phase / Next phase / Completed phases: step_definitions / finalize / 4" in response.text
    assert "Generated artifact phases: 5" in response.text
    assert "Current phase artifacts: 2" in response.text
    assert "Completion criteria: 4/7" in response.text
    assert "SECRET_BUNDLE_CONTENT" not in response.text
    assert "SECRET_JIRA_BODY" not in response.text
    assert "ctx://source/abc" not in response.text


def test_thinking_process_panel_hides_lines_for_missing_compact_source_diagnostics(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "context_state": {
            "source": {
                "source_complete_for_generation": True,
            },
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Source complete including binary bodies:" not in response.text
    assert "Generated artifacts:" not in response.text
    assert "Current phase / Next phase / Completed phases:" not in response.text


def test_thinking_process_panel_renders_max_chat_output_chars_placeholder_for_none(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "context_state": {
            "source": {"max_chat_output_chars": None}
        },
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)
    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")
    assert response.status_code == 200
    assert "Max chat output chars:" not in response.text


def test_thinking_process_panel_renders_lucide_timeline_icons_in_persisted_panel(monkeypatch):
    chatlog = {
        "session_id": "s-1",
        "runtime_events": [
            {
                "event_type": "execution.started",
                "detail_payload": {"message": "Started run"},
            },
            {
                "event_type": "tool_call",
                "detail_payload": {"tool": "search_docs"},
            },
        ],
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert 'data-lucide="play-circle"' in response.text
    assert 'data-lucide="wrench"' in response.text
    assert '<span class="portal-timeline-event-icon">•</span>' not in response.text


def test_thinking_process_view_uses_whitelisted_icon_mapping_not_payload_icon():
    from app.services.thinking_process_view import build_thinking_process_view

    view = build_thinking_process_view(
        {
            "runtime_events": [
                {
                    "event_type": "tool_call",
                    "detail_payload": {
                        "icon": "skull",
                        "tool": "search_docs",
                    },
                }
            ]
        }
    )

    assert view["events"][0]["icon"] == "wrench"
    assert view["events"][0]["display_title"] == "Tool Call"
