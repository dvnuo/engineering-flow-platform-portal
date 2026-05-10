# PR #313 Full Suite Failure Inventory

命令：
- `PYTHONPATH=. pytest -q`

失败清单：

| test file | test name | failure summary | touches PR files? | action |
|---|---|---|---|---|
| tests/test_agent_delegations.py | test_malformed_runtime_delegation_result_marks_failed_but_keeps_task_result | delegation status is `done` not expected `failed` | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_agent_delegations.py | test_task_board_runs_summary_groups_by_coordination_run | no failed delegation row (`NoneType` when setting blocked status) | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_agent_delegations.py | test_coordination_run_status_updates_to_failed_on_terminal_failure | coordination run status `done` not `failed` | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_api_agents_git_info_proxy.py | test_git_info_injects_trusted_portal_identity_headers | extra trace headers (`X-Trace-Id`/`X-Span-Id`) appear vs strict expected dict | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_control_plane_phase2.py | test_internal_runtime_context_includes_runtime_profile_context | llm config contains `tools:[*]` instead of expected `temperature` | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_control_plane_phase2.py | test_internal_runtime_context_projects_copilot_oauth_for_native_runtime | oauth projection uses `OPENCODE_SECRET` instead of expected `NATIVE_SECRET` | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_k8s_noop.py | test_k8s_enabled_create_agent_runtime_returns_creating_after_resource_submission | returned status `running` not `creating` | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_logger.py | test_logger_injects_trace_fields_from_context | logger output missing expected trace field text | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_phase_a_ui_cleanup.py | test_templates_portalized_for_panel_visual_consistency | template/UI class contract assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_ensure_user_has_default_profile_creates_default | runtime profile default creation assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_create_for_user_with_empty_config_stays_sparse | runtime profile sparse config assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_materialize_create_config_json_normalizes_raw_without_default_expansion | materialized config normalization assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_create_for_user_persists_raw_snapshot_without_hidden_default_injection | persisted raw snapshot assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_normalize_persisted_config_json_prunes_unmanaged_nested_fields_but_keeps_managed_endpoint_fields | managed/unmanaged field pruning assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_normalize_persisted_config_json_strips_legacy_provider_automation_fields | legacy provider automation stripping assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_update_for_user_sanitizes_runtime_profile_config | runtime profile sanitize assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_service.py | test_parse_runtime_profile_config_json_keeps_llm_oauth | llm oauth parsing assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_temperature_ui_regression.py | test_runtime_profile_temperature_input_has_data_hook | temperature data-hook/UI regression assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_toggle_modal_css_regression.py | test_top_level_runtime_provider_enabled_toggles_are_left_of_titles[app/templates/partials/runtime_profile_panel.html] | CSS/layout ordering assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profile_toggle_modal_css_regression.py | test_top_level_runtime_provider_enabled_toggles_are_left_of_titles[app/templates/partials/settings_panel.html] | CSS/layout ordering assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profiles_api.py | test_runtime_profile_create_materializes_creation_seed_defaults | runtime profile create/default materialization assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profiles_api.py | test_runtime_profile_get_sanitizes_legacy_provider_automation_fields | runtime profile read sanitization assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profiles_api.py | test_runtime_profile_list_sanitizes_legacy_provider_automation_fields | runtime profile list sanitization assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_runtime_profiles_api.py | test_runtime_profile_api_redacts_llm_oauth_secrets_in_response | oauth redaction assertion mismatch | No | Kept as baseline failure; outside PR #313 files/scope. |
| tests/test_web_more.py | test_start_copilot_auth_uses_portal_endpoints_and_stops_on_declined | JS helper cannot extract `copilotCard` function from current `chat_ui.js` | Partial (test file touched in PR) | Investigated: failure is in unrelated copilot auth extraction path; PR #313 changed only display-block placeholder assertion in this file. Kept as baseline for this branch. |
| tests/test_web_more.py | test_start_copilot_auth_stops_on_check_http_error_or_missing_status | JS helper cannot extract `copilotCard` function from current `chat_ui.js` | Partial (test file touched in PR) | Investigated: same root cause as above; unrelated to PR #313 functional changes. Kept as baseline for this branch. |
| tests/test_web_more.py | test_start_copilot_auth_authorized_updates_hidden_fields_and_masked_summary | JS helper cannot extract `copilotCard` function from current `chat_ui.js` | Partial (test file touched in PR) | Investigated: same root cause as above; unrelated to PR #313 functional changes. Kept as baseline for this branch. |
