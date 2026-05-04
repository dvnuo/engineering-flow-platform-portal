#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-.}"
pytest tests/test_agent_runtime_type_schema.py \
       tests/test_agent_defaults_source_regression.py \
       tests/test_t13_portal_runtime_matrix.py \
       tests/test_k8s_service.py \
       tests/test_proxy_api.py
