import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


def _chat_ui_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _run_normalize(item):
    js = _chat_ui_source()
    fn = _extract_js_function(js, "normalizeServerFileItem")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping normalizeServerFileItem runtime checks")
    payload = json.dumps(item)
    script = f"""
{fn}
const input = {payload};
const output = normalizeServerFileItem(input);
console.log(JSON.stringify(output));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout.strip())


def test_normalize_server_file_item_helper_exists():
    source = _chat_ui_source()
    assert "function normalizeServerFileItem(item)" in source
    assert ".map(normalizeServerFileItem)" in source
    assert "const items = data.items || [];" not in source


def test_normalize_server_file_item_supports_native_shape():
    output = _run_normalize({"name": ".opencode", "path": "/workspace/.opencode", "is_dir": True, "is_file": False})
    assert output["is_dir"] is True
    assert output["is_file"] is False
    assert output["path"] == "/workspace/.opencode"


def test_normalize_server_file_item_supports_opencode_type_directory_shape():
    output = _run_normalize({"name": ".opencode", "path": ".opencode", "type": "directory"})
    assert output["is_dir"] is True
    assert output["is_file"] is False


def test_normalize_server_file_item_supports_type_dir_alias():
    output = _run_normalize({"name": "agents", "relative_path": ".opencode/agents", "type": "dir"})
    assert output["is_dir"] is True
    assert output["path"] == ".opencode/agents"


def test_normalize_server_file_item_supports_file_shape():
    output = _run_normalize({"name": "opencode.json", "path": ".opencode/opencode.json", "type": "file"})
    assert output["is_dir"] is False
    assert output["is_file"] is True


def test_normalize_server_file_item_prefers_explicit_boolean_over_type():
    output = _run_normalize({"name": "weird", "path": "weird", "is_dir": False, "type": "directory"})
    assert output["is_dir"] is False


def test_normalize_server_file_item_handles_missing_path_without_throwing():
    with_name = _run_normalize({"name": "README.md", "type": "file"})
    assert with_name["path"] == "README.md"

    output = _run_normalize(None)
    assert output["is_dir"] is False
    assert output["is_file"] is True
    assert output["path"] == ""


def test_server_files_download_delete_contract_not_regressed():
    source = _chat_ui_source()
    assert "url.searchParams.append('paths'" in source or 'url.searchParams.append("paths"' in source
    assert "JSON.stringify({ paths })" in source
    assert "append('path'" not in _extract_js_function(source, "downloadSelectedFiles")
    assert "paths.join(',')" not in source
