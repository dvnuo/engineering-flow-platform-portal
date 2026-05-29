import json
from types import SimpleNamespace

from app.api import proxy


def _proxy_source() -> str:
    from pathlib import Path

    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def test_stream_chat_paths_are_classified_correctly():
    assert proxy._is_direct_chat_execution_path('POST', 'api/chat/stream') is True
    assert proxy._is_direct_chat_execution_path('POST', '/api/chat/stream') is True
    assert proxy._is_streaming_runtime_path('POST', 'api/chat/stream') is True
    assert proxy._is_streaming_runtime_path('POST', '/api/chat/stream') is True


def test_streaming_headers_whitelist():
    selected = proxy._select_streaming_response_headers({
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        'x-accel-buffering': 'no',
        'x-extra': 'ignored',
    })
    assert selected['content-type'] == 'text/event-stream'
    assert selected['cache-control'] == 'no-cache'
    assert selected['x-accel-buffering'] == 'no'
    assert 'x-extra' not in selected


def test_chat_stream_proxy_does_not_buffer():
    source = _proxy_source()
    streaming_start = source.index("if _is_streaming_runtime_path(request.method, subpath):")
    streaming_end = source.index("filtered_query_items = _filter_proxy_query_items", streaming_start)
    streaming_block = source[streaming_start:streaming_end]

    assert "httpx.AsyncClient(timeout=None)" in streaming_block
    assert "client.stream(" in streaming_block
    assert "async for chunk in upstream_response.aiter_raw():" in streaming_block
    assert "yield chunk" in streaming_block
    assert "StreamingResponse(" in streaming_block
    assert ".content" not in streaming_block
    assert "api/tasks" not in streaming_block


def test_internal_runtime_paths_stay_control_plane_only():
    assert proxy._is_control_plane_only_runtime_path('api/internal/sessions') is True
    assert proxy._is_control_plane_only_runtime_path('api/internal/anything') is True


def test_chat_payload_enrichment_applies_metadata_and_model_override():
    payload = {
        'message': 'hi',
        'model_override': 'gpt-4.1',
        'request_id': 'req_123',
        'metadata': {
            'provider': 'browser-forged',
            'runtime_profile': {'config': {'enabled_tools': ['browser-forged']}},
        },
        'portal_user_id': 'browser-forged',
    }
    metadata = {'provider': 'openai'}
    class _User: pass
    enriched = proxy._enrich_chat_payload_with_runtime_metadata(payload, metadata, _User(), runtime_type='opencode')
    assert enriched['metadata']['provider'] == 'openai'
    assert enriched['metadata']['provider'] != 'browser-forged'
    assert 'model' in enriched['metadata']
    assert enriched['request_id'] == 'req_123'
    assert 'portal_user_id' not in enriched


def test_runtime_metadata_carries_trusted_runtime_v2_config_surface(monkeypatch):
    import app.services.runtime_execution_context_service as module
    from app.services.runtime_execution_context_service import RuntimeExecutionContextService

    profile = SimpleNamespace(
        id="rp-1",
        name="Runtime v2",
        revision=5,
        config_json=json.dumps(
            {
                "llm": {
                    "provider": "github_copilot",
                    "model": "gpt-5-mini",
                    "api_key": "OA",
                },
                "enabled_tools": ["bash", "read"],
                "disabled_tools": ["webfetch"],
                "tool_permissions": {"bash": "ask"},
                "max_iterations": 6,
                "doom_loop_threshold": None,
                "active_skills": ["review"],
                "skill_directories": ["/app/skills"],
                "command_directories": ["/workspace/.efp/commands"],
                "compaction_auto": True,
                "compaction_preserve_recent_tokens": 4800,
                "compaction_prune_min_chars": 20000,
                "compaction_prune_protect_chars": 40000,
                "enable_session_revert_snapshots": True,
                "include_default_system_prompt": True,
                "include_environment_context": False,
                "include_runtime_reminders": True,
                "system_prompt_paths": ["/workspace/SYSTEM.md"],
                "max_system_prompt_chars": 30000,
                "include_default_instructions": True,
                "attach_read_instructions": False,
                "instruction_paths": ["/workspace/AGENTS.md"],
                "max_instruction_chars": 28000,
                "include_skill_sidecar_content": True,
                "max_skill_sidecar_chars": 7000,
                "max_command_chars": 25000,
                "resolve_prompt_references": True,
                "max_prompt_reference_chars": 18000,
                "max_prompt_directory_entries": 300,
                "inject_background_task_results": False,
                "emit_llm_stream_events": True,
                "track_usage": False,
                "tool_output_truncation_direction": "tail",
                "runtime_mode": "plan",
                "enable_plan_tool": None,
            }
        ),
    )

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    metadata = RuntimeExecutionContextService().build_runtime_metadata(
        db=object(),
        agent=SimpleNamespace(id="agent-1", runtime_profile_id="rp-1", runtime_type="opencode"),
    )
    cfg = metadata["runtime_profile"]["config"]

    assert metadata["runtime_profile_id"] == "rp-1"
    assert metadata["provider"] == "github-copilot"
    assert metadata["model"] == "github-copilot/gpt-5-mini"
    assert cfg["llm"]["provider"] == "github-copilot"
    assert cfg["llm"]["model"] == "github-copilot/gpt-5-mini"
    assert cfg["llm"]["api_key"] == "OA"
    assert "tools" not in cfg["llm"]
    assert cfg["enabled_tools"] == ["bash", "read"]
    assert cfg["disabled_tools"] == ["webfetch"]
    assert cfg["tool_permissions"] == {"bash": "ask"}
    assert cfg["max_iterations"] == 6
    assert cfg["doom_loop_threshold"] is None
    assert cfg["active_skills"] == ["review"]
    assert cfg["skill_directories"] == ["/app/skills"]
    assert cfg["command_directories"] == ["/workspace/.efp/commands"]
    assert cfg["compaction_auto"] is True
    assert cfg["compaction_preserve_recent_tokens"] == 4800
    assert cfg["compaction_prune_min_chars"] == 20000
    assert cfg["compaction_prune_protect_chars"] == 40000
    assert cfg["enable_session_revert_snapshots"] is True
    assert cfg["include_default_system_prompt"] is True
    assert cfg["include_environment_context"] is False
    assert cfg["include_runtime_reminders"] is True
    assert cfg["system_prompt_paths"] == ["/workspace/SYSTEM.md"]
    assert cfg["max_system_prompt_chars"] == 30000
    assert cfg["include_default_instructions"] is True
    assert cfg["attach_read_instructions"] is False
    assert cfg["instruction_paths"] == ["/workspace/AGENTS.md"]
    assert cfg["max_instruction_chars"] == 28000
    assert cfg["include_skill_sidecar_content"] is True
    assert cfg["max_skill_sidecar_chars"] == 7000
    assert cfg["max_command_chars"] == 25000
    assert cfg["resolve_prompt_references"] is True
    assert cfg["max_prompt_reference_chars"] == 18000
    assert cfg["max_prompt_directory_entries"] == 300
    assert cfg["inject_background_task_results"] is False
    assert cfg["emit_llm_stream_events"] is True
    assert cfg["track_usage"] is False
    assert cfg["tool_output_truncation_direction"] == "tail"
    assert cfg["runtime_mode"] == "plan"
    assert cfg["enable_plan_tool"] is None
    assert "github" not in cfg


def test_runtime_metadata_keeps_default_llm_when_profile_only_has_runtime_v2_tools(monkeypatch):
    import app.services.runtime_execution_context_service as module
    from app.services.runtime_execution_context_service import RuntimeExecutionContextService

    profile = SimpleNamespace(
        id="rp-tools-only",
        name="Runtime v2 tools",
        revision=2,
        config_json=json.dumps({"enabled_tools": ["bash"]}),
    )

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    metadata = RuntimeExecutionContextService().build_runtime_metadata(
        db=object(),
        agent=SimpleNamespace(id="agent-1", runtime_profile_id="rp-tools-only", runtime_type="opencode"),
    )
    cfg = metadata["runtime_profile"]["config"]

    assert cfg["enabled_tools"] == ["bash"]
    assert cfg["llm"]["provider"] == "github-copilot"
    assert cfg["llm"]["model"] == "github-copilot/gpt-5.4-mini"
    assert "tools" not in cfg["llm"]
