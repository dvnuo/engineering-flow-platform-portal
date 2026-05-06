#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-.}"
pytest -q \
  tests/test_config.py \
  tests/test_logger.py \
  tests/test_trace_context.py \
  tests/test_agent_runtime_type_schema.py \
  tests/test_agent_defaults_source_regression.py \
  tests/test_k8s_service.py \
  tests/test_proxy_api.py \
  tests/test_proxy_service.py \
  tests/test_proxy_identity_headers.py \
  tests/test_proxy_websocket.py \
  tests/test_web_runtime_proxy_headers.py \
  tests/test_web_chat_send.py \
  tests/test_runtime_profile_sync_service.py \
  tests/test_runtime_capability_contract.py \
  tests/test_alembic_runtime_type_server_default.py \
  tests/test_legacy_master_db_upgrade.py \
  tests/test_portal_runtime_contract_docs.py \
  tests/test_t13_portal_runtime_matrix.py
