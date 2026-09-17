# Portal Contract Smoke Tests

The script in this directory runs a focused set of Portal regression tests. The historical name is "T13 smoke". It checks Portal-side behavior with test doubles and test databases; it does not start a real Kubernetes cluster, run an assistant image, or contact a live model provider.

## Prerequisites

- Run from the Portal repository root.
- Use Python 3.11, matching the repository's CI and Docker image.
- Install the application requirements and `pytest` in a virtual environment.
- Use a development checkout and test configuration. The tests import Portal modules; do not point the test process at a production database or Kubernetes deployment.

For Bash on Linux, macOS, or WSL:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install pytest
export K8S_ENABLED=false
export PYTHONPATH=.
bash integration/scripts/smoke_portal.sh
```

The script exits nonzero if a selected test fails. Read the pytest failure summary and the corresponding assertion; a completed script with exit code `0` means the selected tests passed.

## Windows PowerShell

The shell script requires Bash. You can use WSL, or run its same selected tests with Python in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pytest
$env:K8S_ENABLED = "false"
$env:PYTHONPATH = "."
$tests = @(
    "tests/test_config.py"
    "tests/test_logger.py"
    "tests/test_trace_context.py"
    "tests/test_agent_runtime_type_schema.py"
    "tests/test_agent_defaults_source_regression.py"
    "tests/test_k8s_service.py"
    "tests/test_proxy_api.py"
    "tests/test_proxy_service.py"
    "tests/test_proxy_identity_headers.py"
    "tests/test_proxy_websocket.py"
    "tests/test_web_runtime_proxy_headers.py"
    "tests/test_web_chat_send.py"
    "tests/test_runtime_capability_contract.py"
    "tests/test_alembic_runtime_type_server_default.py"
    "tests/test_legacy_master_db_upgrade.py"
    "tests/test_portal_runtime_contract_docs.py"
    "tests/test_t13_portal_runtime_matrix.py"
)
.\.venv\Scripts\python.exe -m pytest -q @tests
```

Some Kubernetes provisioning tests exercise Linux shell commands. Linux/WSL is the closest match to CI if a native Windows run fails because Bash or Unix utilities are unavailable.

## What the smoke suite covers

- Runtime-marker validation, legacy missing-marker compatibility, and the supported `native`/`opencode` defaults matrix.
- Trace and trusted Portal identity headers for generic, multipart, streaming, WebSocket, and chat proxy paths.
- Rejection of browser-supplied identity/trace spoofing and sanitization of outbound headers.
- Runtime capability compatibility metadata and the Portal/runtime documentation contract.
- Kubernetes runtime image, workspace, skills, agent-settings, and runtime-specific provisioning behavior.
- Alembic runtime-marker default cleanup and upgrades from legacy database schemas.
- Configuration defaults and structured logging/trace context.

The exact selected files are maintained in [scripts/smoke_portal.sh](scripts/smoke_portal.sh). Some test names retain older "single runtime" terminology; the current application supports two runtime markers, with `native` enabled by default.

## Full validation and live testing

The smoke script is a subset of the repository test suite. The current [CI workflow](../.github/workflows/ci.yml) runs the full suite and JavaScript syntax checks, then builds the Docker image. For a full local Python test run:

```bash
python -m pytest tests/
```

Live validation belongs in the runtime repository or a multi-repository integration environment. After deploying Portal, separately verify sign-in, assistant creation/readiness, profile synchronization, a real chat response, session behavior, and any enabled connectors. A passing Portal smoke suite alone does not establish end-to-end runtime compatibility. See the [runtime contract](../docs/PORTAL_RUNTIME_CONTRACT.md) and [Kubernetes troubleshooting guide](../docs/K8S_TROUBLESHOOTING.md) for the relevant boundaries and diagnostics.
