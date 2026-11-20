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


@pytest.mark.parametrize(
    "value, ok",
    [
        ("/", True),
        ("/healthz", True),
        ("/api/health?deep=1", True),
        # A path of the app's own port, whatever it looks like: the host asks 127.0.0.1 for it.
        ("/a/../b", True),
        ("//evil.example/", True),
        ("health", False),
        ("https://evil.example/", False),
        ("/a b", False),
        ("/a#b", False),
        (5, False),
    ],
)
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


@pytest.mark.parametrize("fields", [{}, {"owners": []}, {"owners": [" "]}])
def test_a_manifest_without_owners_limits_nobody(fields):
    manifest = validate_manifest({"name": "a", "type": "static", **fields})
    assert manifest.owners == ()
    check_ownership(manifest, "alice")
    check_ownership(manifest, "carol")


def test_an_app_from_a_template_skips_the_owners_of_the_manifest():
    manifest = validate_manifest({"name": "a", "type": "static", "owners": ["bob"]})
    with pytest.raises(NotListedAsOwner):
        check_ownership(manifest, "alice")
    check_ownership(manifest, "alice", from_template=True)


# --- the same rules as the app host -----------------------------------------------------------------
#
# The host validates the manifest again when it installs the app
# (efp_app_host/manifest.py in the app repository, the reference). What passes
# here must pass there and the reverse, so nothing is trimmed before it is
# checked and each limit is the host's. One line per case: the keys added to a
# minimal manifest of that type, whether the manifest is valid, and (when it
# is) what the Portal reads from it.

STATIC = {"name": "a", "type": "static"}
FASTAPI = {"name": "a", "type": "fastapi", "entry": "main:app"}
STREAMLIT = {"name": "a", "type": "streamlit", "entry": "app.py"}
COMMAND = {"name": "a", "type": "command", "command": "python serve.py"}

HOST_RULES = [
    # entry of a fastapi / flask app: exactly module:attribute, spaces included in the judgement
    ("entry with a space in front", FASTAPI, {"entry": " main:app"}, False, None),
    ("entry with a space behind", FASTAPI, {"entry": "main:app "}, False, None),
    ("entry as written", FASTAPI, {"entry": "pkg.web:create_app"}, True, {"entry": "pkg.web:create_app"}),
    # entry of a script app: a relative path, tidied
    ("script entry is tidied", STREAMLIT, {"entry": "./src//app.py"}, True, {"entry": "src/app.py"}),
    ("script entry keeps its spaces", STREAMLIT, {"entry": " app.py"}, True, {"entry": " app.py"}),
    ("script entry that is the directory itself", STREAMLIT, {"entry": "."}, False, None),
    ("script entry with a backslash", STREAMLIT, {"entry": "src\\app.py"}, False, None),
    ("script entry left empty", STREAMLIT, {"entry": ""}, False, None),
    # entry of a static app: a directory, the entry directory itself by default
    ("static entry left empty is the directory itself", STATIC, {"entry": ""}, True, {"entry": "."}),
    ("static entry left out", STATIC, {}, True, {"entry": "."}),
    ("static entry ./", STATIC, {"entry": "./"}, True, {"entry": "."}),
    ("static entry with a trailing slash", STATIC, {"entry": "dist/"}, True, {"entry": "dist"}),
    ("static entry outside", STATIC, {"entry": "../dist"}, False, None),
    # health_path: a leading slash, URL characters, at most 200 of them
    ("health_path left empty is /", STATIC, {"health_path": ""}, True, {"health_path": "/"}),
    ("health_path //x", STATIC, {"health_path": "//x"}, True, {"health_path": "//x"}),
    ("health_path /../x", STATIC, {"health_path": "/../x"}, True, {"health_path": "/../x"}),
    ("health_path with a query", STATIC, {"health_path": "/health?deep=1&x=%20"}, True, {"health_path": "/health?deep=1&x=%20"}),
    ("health_path of 200 characters", STATIC, {"health_path": "/" + "a" * 199}, True, {"health_path": "/" + "a" * 199}),
    ("health_path of 201 characters", STATIC, {"health_path": "/" + "a" * 200}, False, None),
    ("health_path without its slash", STATIC, {"health_path": "health"}, False, None),
    ("health_path with a fragment", STATIC, {"health_path": "/health#top"}, False, None),
    ("health_path with a space in front", STATIC, {"health_path": " /health"}, False, None),
    ("health_path with a space behind", STATIC, {"health_path": "/health "}, False, None),
    ("health_path with a space inside", STATIC, {"health_path": "/a b"}, False, None),
    ("health_path with a backslash", STATIC, {"health_path": "/a\\b"}, False, None),
    ("health_path with a letter that is not ASCII", STATIC, {"health_path": "/caf\u00e9"}, False, None),
    ("health_path that is a URL", STATIC, {"health_path": "http://127.0.0.1/"}, False, None),
    # command: one line, at most 2000 characters, kept as written
    ("command kept as written", COMMAND, {"command": " python serve.py --port $PORT "}, True, {"command": " python serve.py --port $PORT ", "entry": None}),
    ("command of 2000 characters", COMMAND, {"command": "x" * 2000}, True, {"command": "x" * 2000}),
    ("command of 2001 characters", COMMAND, {"command": "x" * 2001}, False, None),
    ("command of two lines", COMMAND, {"command": "python a.py\npython b.py"}, False, None),
    ("command with a carriage return", COMMAND, {"command": "python a.py\r"}, False, None),
    ("command with a NUL", COMMAND, {"command": "python a.py\x00"}, False, None),
    ("command of spaces only", COMMAND, {"command": "   "}, False, None),
    ("command of another type is ignored, whatever it is", STATIC, {"command": "a\nb" * 2000}, True, {"command": None}),
    ("entry of a command app is ignored, whatever it is", COMMAND, {"entry": "../x\\y"}, True, {"entry": None}),
    # requirements and icon: relative paths with forward slashes
    ("requirements with a backslash", STATIC, {"requirements": "deps\\requirements.txt"}, False, None),
    ("requirements left empty is the default", STATIC, {"requirements": ""}, True, {"requirements": "requirements.txt"}),
    ("requirements tidied", STATIC, {"requirements": "./deps//prod.txt"}, True, {"requirements": "deps/prod.txt"}),
    ("requirements that is the directory itself", STATIC, {"requirements": "."}, False, None),
    ("requirements that is not text", STATIC, {"requirements": 7}, False, None),
    ("icon with a backslash", STATIC, {"icon": "img\\icon.png"}, False, None),
    ("icon left empty", STATIC, {"icon": ""}, False, None),
    ("icon left out", STATIC, {"icon": None}, True, {"icon": None}),
    ("icon tidied", STATIC, {"icon": "./img//icon.png"}, True, {"icon": "img/icon.png"}),
    ("icon with a NUL", STATIC, {"icon": "icon\x00.png"}, False, None),
    ("icon with a drive letter", STATIC, {"icon": "C:icon.png"}, False, None),
    ("icon above the directory", STATIC, {"icon": "img/../../icon.png"}, False, None),
    ("icon with two dots inside a name", STATIC, {"icon": "img..old/icon.png"}, True, {"icon": "img..old/icon.png"}),
    # owners: usernames, none of them empty
    ("an empty string among the owners", STATIC, {"owners": ["alice", ""]}, False, None),
    ("owners with spaces around a name", STATIC, {"owners": [" alice ", "bob"]}, True, {"owners": ("alice", "bob")}),
    ("no owners", STATIC, {"owners": []}, True, {"owners": ()}),
    ("an owner that is not text", STATIC, {"owners": ["alice", None]}, False, None),
    # description: at most 500 characters as written
    ("description of 500 characters", STATIC, {"description": "d" * 500}, True, {"description": "d" * 500}),
    ("description of 500 characters and a space", STATIC, {"description": "d" * 500 + " "}, False, None),
    ("description of 498 characters and two spaces", STATIC, {"description": "d" * 498 + "  "}, True, {"description": "d" * 498}),
    ("description of 501 spaces", STATIC, {"description": " " * 501}, False, None),
    # name: 1 to 80 characters without the spaces around it
    ("name of 80 characters and spaces around it", STATIC, {"name": "  " + "x" * 80 + "  "}, True, {"name": "x" * 80}),
    ("name of 81 characters", STATIC, {"name": "x" * 81}, False, None),
    ("name of spaces", STATIC, {"name": "   "}, False, None),
]


@pytest.mark.parametrize("case, base, changes, valid, reads", HOST_RULES, ids=[case[0] for case in HOST_RULES])
def test_the_manifest_rules_are_the_hosts(case, base, changes, valid, reads):
    data = {**base, **changes}

    if not valid:
        with pytest.raises(ManifestError):
            validate_manifest(data)
        # The same through the file, which is how a deployment reads it.
        with pytest.raises(ManifestError):
            parse_manifest(json.dumps(data).encode("utf-8"))
        return

    manifest = validate_manifest(data)
    for field, value in (reads or {}).items():
        assert getattr(manifest, field) == value, field
    assert parse_manifest(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8")) == manifest, "a byte-order mark changes nothing"


def test_a_manifest_that_is_wrong_in_the_ways_the_host_refuses_names_each_of_them():
    problems = _problems(
        {
            "name": "a",
            "type": "command",
            "command": "a\nb",
            "icon": "",
            "requirements": "deps\\prod.txt",
            "health_path": "health",
            "owners": [""],
            "description": "d" * 501,
        }
    )

    assert '"command" must be a single line of at most 2000 characters.' in problems
    assert '"icon" must be a relative path' in problems and '"requirements" must be a relative path' in problems
    assert '"health_path" must be a path of the app' in problems
    assert '"owners" must be a list of Portal usernames.' in problems
    assert '"description" may be at most 500 characters.' in problems
