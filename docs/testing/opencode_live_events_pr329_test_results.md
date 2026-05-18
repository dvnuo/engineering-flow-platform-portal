# OpenCode Live Events PR #329 Test Results

## Commits

- Branch: `fix/opencode-live-events` at `853e98487cb9152efb41f192774cd3d02341173d`
- Master: `master` at `d4eda1730326d67efb2315c505b3637cce605b54`

## Targeted Test Results

- `PYTHONPATH=. python3.11 -m pytest -q tests/test_proxy_chat_stream.py` -> `5 passed, 3 warnings`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_proxy_websocket.py` -> `11 passed, 24 warnings`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_proxy_identity_headers.py` -> `36 passed, 24 warnings`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_streaming_static.py` -> `18 passed`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_streaming_lifecycle_static.py` -> `5 passed`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_sse_parser_node.py` -> `4 passed`
- `PYTHONPATH=. python3.11 -m pytest -q tests/test_thinking_process_panel.py` -> `34 passed, 52 warnings`
- Additional coverage for the new event mapping:
  `PYTHONPATH=. python3.11 -m pytest -q tests/test_thinking_process_view_events.py` -> `5 passed`

## Full Suite Results

- Branch: `PYTHONPATH=. python3.11 -m pytest -q` -> `28 failed, 1284 passed, 140 warnings`
- Master: `PYTHONPATH=. python3.11 -m pytest -q` -> `54 failed, 1236 passed, 139 warnings`

## Failure Diff Summary

The branch introduced no new full-suite failures. Every branch failure is also present on `master`.

The branch failures shared with `master` are:

- `tests/test_agent_delegations.py::test_malformed_runtime_delegation_result_marks_failed_but_keeps_task_result`
- `tests/test_agent_delegations.py::test_task_board_runs_summary_groups_by_coordination_run`
- `tests/test_agent_delegations.py::test_coordination_run_status_updates_to_failed_on_terminal_failure`
- `tests/test_agent_groups.py::test_internal_task_agent_create_delete_preserves_safeguards_for_internal_route`
- `tests/test_api_agents_git_info_proxy.py::test_git_info_injects_trusted_portal_identity_headers`
- `tests/test_chat_assistant_message_actions.py::test_message_mutation_failure_uses_friendly_runtime_error_helper`
- `tests/test_control_plane_phase2.py::test_internal_runtime_context_includes_runtime_profile_context`
- `tests/test_control_plane_phase2.py::test_internal_runtime_context_projects_copilot_oauth_for_native_runtime`
- `tests/test_k8s_noop.py::test_k8s_enabled_create_agent_runtime_returns_creating_after_resource_submission`
- `tests/test_logger.py::test_logger_injects_trace_fields_from_context`
- `tests/test_runtime_profile_service.py` runtime-profile default/sparse/sanitization/oauth tests
- `tests/test_runtime_profile_temperature_ui_regression.py::test_runtime_profile_temperature_input_has_data_hook`
- `tests/test_runtime_profile_toggle_modal_css_regression.py` runtime provider toggle placement tests
- `tests/test_runtime_profiles_api.py` runtime-profile create/sanitize/redaction tests
- `tests/test_web_more.py` Copilot auth tests requiring `copilotCard`

`master` has 26 additional failures that are not present on the branch. They are in existing chat stream static/Node helper behavior and multi-agent chat UI regression tests:

- `tests/test_chat_assistant_message_actions.py` chat stream final/delta/candidate event handling tests
- `tests/test_chat_ui_stream_behavior.py` stream behavior and non-stream fallback tests
- `tests/test_chat_ui_stream_guard.py` incomplete stream and ok=false fallback tests
- `tests/test_multi_agent_chat_ui_regressions.py` background/hidden-agent success and failure rendering tests

## Conclusion

The branch does not add full-suite failures relative to `master`. The remaining branch failures are baseline failures already present on `master`; this PR reduces the full-suite failure count from 54 on `master` to 28 on the branch.

The OpenCode live-events guard coverage also verifies:

- OpenCode long chat uses `/a/${agentIdAtSend}/api/chat/stream`.
- OpenCode long chat does not use `/api/tasks`, task mode, or direct `:4096`/`localhost:4096`/`127.0.0.1:4096` access.
- The user-facing proxy still blocks `api/internal`.
- `continuation.suppressed` is displayed as a warning in Thinking Process and remains a live timeline event until a final/incomplete event arrives.
