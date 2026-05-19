# OpenCode long chat detached reconcile test results

Date: 2026-05-19
Branch: `fix/opencode-long-chat-detached-reconcile`
Base: `master` at `d82350935fe0363200710922a031b6bbca769dd9`

## Targeted checks

All targeted checks passed on the branch:

```text
PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_streaming_static.py
23 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_streaming_lifecycle_static.py
7 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_sse_parser_node.py
5 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_thinking_process_panel.py
34 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_thinking_process_view_events.py
5 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_proxy_chat_stream.py
5 passed

PYTHONPATH=. python3.11 -m pytest -q tests/test_proxy_websocket.py
11 passed
```

The combined targeted run also passed:

```text
PYTHONPATH=. python3.11 -m pytest -q tests/test_chat_ui_streaming_static.py tests/test_chat_ui_streaming_lifecycle_static.py tests/test_chat_ui_sse_parser_node.py tests/test_thinking_process_panel.py tests/test_thinking_process_view_events.py tests/test_proxy_chat_stream.py tests/test_proxy_websocket.py
90 passed
```

## Full suite baseline comparison

The branch full suite is not green, but it has no failures beyond the current master baseline.

```text
Branch:
PYTHONPATH=. python3.11 -m pytest -q
28 failed, 1310 passed, 140 warnings

Master baseline:
PYTHONPATH=. python3.11 -m pytest -q
28 failed, 1302 passed, 140 warnings

Failure set diff:
branch_count=28 master_count=28
only_branch=
only_master=
```

The additional branch pass count comes from tests added for this change. The failing test names are identical between branch and master.
