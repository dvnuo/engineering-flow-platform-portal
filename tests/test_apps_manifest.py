"""efp-app.json: validation as the contract spells it, and who may register a directory."""

import json

import pytest

from app.services.apps.errors import ManifestError, NotListedAsOwner
from app.services.apps.manifest import (
    check_ownership,
    is_listed_owner,
    is_safe_relative_path,
    parse_manifest,
    validate_manifest,
)

FULL = {
    "name": "Mind map",
    "description": "Draw and share mind maps.",
    "icon": "icon.png",
    "type": "fastapi",
    "entry": "main:app",
    "command": None,
    "requirements": "requirements.txt",
    "health_path": "/",
    "startup_timeout": 60,
    "strip_prefix": None,
    "owners": ["alice"],
}


def _problems(data):
    with pytest.raises(ManifestError) as caught:
        validate_manifest(data)
    return " ".join(item["detail"] for item in caught.value.to_payload()["problems"])


def test_the_contracts_example_is_valid():
    manifest = parse_manifest(json.dumps(FULL).encode("utf-8"))
    assert manifest.name == "Mind map"
    assert manifest.type == "fastapi"
    assert manifest.entry == "main:app"
    assert manifest.icon == "icon.png"
    assert manifest.requirements == "requirements.txt"
    assert manifest.health_path == "/"
    assert manifest.startup_timeout == 60
    assert manifest.strip_prefix is None
    assert manifest.owners == ("alice",)


def test_defaults_and_unknown_keys():
    manifest = validate_manifest({"name": " Notes ", "type": "static", "something_else": {"x": 1}})
    assert manifest.name == "Notes"
    assert manifest.description == ""
    assert manifest.icon is None
    assert manifest.entry == "."  # a static app serves its entry directory
    assert manifest.requirements == "requirements.txt"
    assert manifest.health_path == "/"
    assert manifest.startup_timeout == 60
    assert manifest.owners == ()


def test_a_byte_order_mark_is_not_a_syntax_error():
    assert parse_manifest(b"\xef\xbb\xbf" + json.dumps(FULL).encode("utf-8")).name == "Mind map"


@pytest.mark.parametrize(
    "content, expected",
    [
        (b"{not json", "not valid JSON"),
        (b"[1, 2]", "one JSON object"),
        (b"\xff\xfe\x00", "not UTF-8"),
        (b" " * (64 * 1024 + 1), "larger than 64 KB"),
    ],
    # Named: the default id would be the content itself, 64 KB of it.
    ids=["not-json", "not-an-object", "not-utf8", "too-large"],
)
def test_unreadable_files(content, expected):
    with pytest.raises(ManifestError) as caught:
        parse_manifest(content)
    assert expected in caught.value.message
    assert caught.value.code == "manifest_invalid"
    assert caught.value.status_code == 422


def test_name_rules():
    assert '"name" is required' in _problems({"type": "static"})
    assert '"name" is required' in _problems({"name": "   ", "type": "static"})
    assert "at most 80" in _problems({"name": "x" * 81, "type": "static"})
    assert validate_manifest({"name": "x" * 80, "type": "static"}).name == "x" * 80


def test_description_rules():
    assert "at most 500" in _problems({"name": "a", "type": "static", "description": "d" * 501})
    assert '"description" must be text' in _problems({"name": "a", "type": "static", "description": 5})
    assert validate_manifest({"name": "a", "type": "static", "description": None}).description == ""


@pytest.mark.parametrize("value", [None, "django", "FastAPI", 3])
def test_type_must_be_one_of_the_six(value):
    assert '"type" must be one of' in _problems({"name": "a", "type": value})


@pytest.mark.parametrize("app_type", ["fastapi", "flask"])
@pytest.mark.parametrize("entry, ok", [("main:app", True), ("pkg.web:create_app", True), ("main", False), ("main.py", False), ("a b:c", False), (None, False), ("../x:app", False)])
def test_module_attribute_entries(app_type, entry, ok):
    data = {"name": "a", "type": app_type, "entry": entry}
    if ok:
        assert validate_manifest(data).entry == entry
    else:
        assert "module:attribute" in _problems(data)


@pytest.mark.parametrize("app_type", ["streamlit", "gradio"])
@pytest.mark.parametrize("entry, ok", [("app.py", True), ("src/app.py", True), ("/abs/app.py", False), ("../app.py", False), ("C:\\app.py", False), (None, False)])
def test_script_entries(app_type, entry, ok):
    data = {"name": "a", "type": app_type, "entry": entry}
    if ok:
        assert validate_manifest(data).entry == entry
    else:
        assert "path of the script" in _problems(data)


def test_static_entry_is_a_directory_inside_the_entry_directory():
    assert validate_manifest({"name": "a", "type": "static", "entry": "dist"}).entry == "dist"
    assert "directory inside" in _problems({"name": "a", "type": "static", "entry": "../dist"})


def test_command_apps_need_a_command_and_ignore_entry():
    assert '"command" is required' in _problems({"name": "a", "type": "command"})
    assert '"command" is required' in _problems({"name": "a", "type": "command", "command": "  "})
    manifest = validate_manifest({"name": "a", "type": "command", "command": "python serve.py --port $PORT", "entry": "../ignored"})
    assert manifest.command == "python serve.py --port $PORT"


@pytest.mark.parametrize("field", ["icon", "requirements"])
@pytest.mark.parametrize("value", ["/etc/passwd", "../secret.txt", "a/../../b", "..", "C:/x", "\\\\server\\share", 7])
def test_paths_may_not_be_absolute_or_leave_the_directory(field, value):
    assert "relative path" in _problems({"name": "a", "type": "static", field: value})


def test_safe_relative_paths():
    assert is_safe_relative_path("icon.png")
    assert is_safe_relative_path("assets/img/icon.png")
    assert is_safe_relative_path("a..b/file")  # two dots inside a name are not a parent directory
    assert not is_safe_relative_path("")
    assert not is_safe_relative_path("a/\x00b")
    assert not is_safe_relative_path(None)


@pytest.mark.parametrize("value, ok", [("/", True), ("/healthz", True), ("health", True), ("/a/../b", False), ("https://evil.example/", False), ("//evil.example/", False), ("/a b", False), (5, False)])
def test_health_path(value, ok):
    data = {"name": "a", "type": "static", "health_path": value}
    if ok:
        assert validate_manifest(data).health_path == value
    else:
        assert '"health_path"' in _problems(data)


@pytest.mark.parametrize("value, ok", [(1, True), (300, True), (0, False), (301, False), (1.5, False), ("60", False), (True, False)])
def test_startup_timeout_is_whole_seconds_up_to_300(value, ok):
    data = {"name": "a", "type": "static", "startup_timeout": value}
    if ok:
        assert validate_manifest(data).startup_timeout == value
    else:
        assert '"startup_timeout"' in _problems(data)


def test_strip_prefix_and_owners_types():
    assert validate_manifest({"name": "a", "type": "static", "strip_prefix": False}).strip_prefix is False
    assert '"strip_prefix"' in _problems({"name": "a", "type": "static", "strip_prefix": "yes"})
    assert '"owners"' in _problems({"name": "a", "type": "static", "owners": "alice"})
    assert '"owners"' in _problems({"name": "a", "type": "static", "owners": ["alice", 5]})


def test_every_problem_is_reported_at_once_with_its_key_and_parameters():
    with pytest.raises(ManifestError) as caught:
        validate_manifest({"type": "nope", "startup_timeout": 900})
    payload = caught.value.to_payload()
    assert payload["code"] == "manifest_invalid"
    assert len(payload["problems"]) == 3
    assert payload["message_key"] == "efp-app.json is not valid: {problems}"
    timeout = next(item for item in payload["problems"] if "startup_timeout" in item["detail"])
    assert timeout["params"] == {"limit": 300}
    assert "{limit}" in timeout["message_key"]


def test_ownership_is_by_portal_username_case_insensitively():
    manifest = validate_manifest({"name": "a", "type": "static", "owners": ["Alice", " bob "]})
    assert is_listed_owner(manifest, "alice")
    assert is_listed_owner(manifest, "BOB")
    assert not is_listed_owner(manifest, "carol")
    assert not is_listed_owner(manifest, "")
    check_ownership(manifest, "alice")
    with pytest.raises(NotListedAsOwner) as caught:
        check_ownership(manifest, "carol")
    assert caught.value.status_code == 403
    assert caught.value.params == {"username": "carol"}
    assert "carol" in caught.value.message


def test_no_owners_means_nobody_unless_the_app_comes_from_a_template():
    manifest = validate_manifest({"name": "a", "type": "static"})
    with pytest.raises(NotListedAsOwner):
        check_ownership(manifest, "alice")
    check_ownership(manifest, "alice", from_template=True)
