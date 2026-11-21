"""Templates: publishing versions of an app, creating an app from one, staying pinned, upgrading.

The rule the tests keep coming back to: a version is a commit. The tag is
resolved once, when it is published; an app created from the version runs
that commit whatever happens to the tag, and moves only when its owner
upgrades it.
"""

import re

import pytest

from _apps_helpers import (
    MANIFEST,
    REPO_URL,
    SHA_1,
    SHA_2,
    SHA_3,
    FakeSource,
    add_user,
    failed_job,
    owner_api_harness,
    remove_from_allowlist,
)
from app.config import get_settings
from app.models import AppRelease, AppTemplateVersion
from app.services.apps.errors import RefNotFound
from app.services.apps.source import ResolvedRef

SHA_4 = "4" * 40


class TemplateSource(FakeSource):
    """GitHub with tags that can be moved, commits that can be asked for by id, and a manifest per commit."""

    def __init__(self) -> None:
        super().__init__(refs={"main": SHA_1})
        self.tags = {"v1.1.0": SHA_2, "v1.0.0": SHA_1}
        # Tags GitHub knows that are not on the first page of its list.
        self.far_tags: dict[str, str] = {}
        self.manifests: dict[str, object] = {}

    def list_tags(self, repo, limit=100):
        self.calls.append(("list_tags", repo.full_name))
        self._maybe_fail("list_tags")
        return [{"name": name, "sha": sha} for name, sha in self.tags.items()]

    def resolve_ref(self, repo, ref):
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            self.calls.append(("resolve_ref", repo.full_name, ref))
            self._maybe_fail("resolve_ref")
            return ResolvedRef(sha=ref, ref=ref, message="a commit by its id")
        if ref.startswith("tags/"):
            self.calls.append(("resolve_ref", repo.full_name, ref))
            name = ref[len("tags/"):]
            if name not in self.far_tags:
                raise RefNotFound(ref=ref, repo=repo.full_name)
            return ResolvedRef(sha=self.far_tags[name], ref=ref, message="a tag by its full name")
        return super().resolve_ref(repo, ref)

    def read_manifest(self, repo, sha, entry_dir, *, ref=None):
        if sha in self.manifests:
            self.manifest = self.manifests[sha]
        elif self.manifests:
            self.manifest = dict(MANIFEST)
        return super().read_manifest(repo, sha, entry_dir, ref=ref)


@pytest.fixture
def harness(monkeypatch):
    yield from owner_api_harness(monkeypatch, source=TemplateSource())


def template(h, *, visibility="public", versions=("v1.0.0",), **fields):
    """An app of ``owner`` offered as a template, with the given tags published; ``(app id, {tag: version})``."""
    app_id = h.create(visibility=visibility, **fields)["app"]["id"]
    assert h.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True}).status_code == 200
    published = {}
    for tag in versions:
        response = h.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": tag, "notes": f"Notes of {tag}"})
        assert response.status_code == 201, response.text
        published[tag] = response.json()
    return app_id, published


def derive(h, template_id, version_id, *, as_user="mate", deploy=True, **fields):
    body = {"template_app_id": template_id, "version_id": version_id, "name": "My map", "deploy": deploy}
    body.update(fields)
    response = h.as_user(as_user).post("/api/apps/from-template", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def app_of(h, app_id, as_user="mate"):
    response = h.as_user(as_user).get(f"/api/apps/{app_id}")
    assert response.status_code == 200, response.text
    return response.json()


def deployed_shas(h):
    return [spec["sha"] for spec in h.host.deploy_specs]


# --- off -------------------------------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/apps/templates"),
    ("POST", "/api/apps/from-template"),
    ("PUT", "/api/apps/{id}/template"),
    ("GET", "/api/apps/{id}/template/tags"),
    ("GET", "/api/apps/{id}/template/versions"),
    ("POST", "/api/apps/{id}/template/versions"),
    ("DELETE", "/api/apps/{id}/template/versions/x"),
    ("POST", "/api/apps/{id}/upgrade"),
]


@pytest.mark.parametrize("method, path", ROUTES)
def test_every_route_answers_404_while_apps_are_off(harness, monkeypatch, method, path):
    app_id = harness.create()["app"]["id"]
    monkeypatch.setattr(get_settings(), "apps_enabled", False)

    response = harness.client.request(method, path.replace("{id}", app_id), json={})

    assert response.status_code == 404


# --- offering an app as a template -------------------------------------------------------------------


def test_an_ordinary_app_has_no_template_and_names_where_to_fork_it(harness):
    created = harness.create()["app"]

    assert created["template"] is None and created["is_template"] is False
    assert created["manage"]["repo_url"] == REPO_URL
    assert created["manage"]["repo_web_url"] == "https://github.com/acme/tools"
    assert created["manage"]["fork_url"] == "https://github.com/acme/tools/fork"


def test_the_owner_marks_the_app_as_a_template_and_back(harness):
    app_id = harness.create(visibility="public")["app"]["id"]

    marked = harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True})
    assert marked.status_code == 200 and marked.json()["is_template"] is True and marked.json()["id"] == app_id
    assert harness.row(app_id).is_template is True
    # Saying it again changes nothing and writes nothing down.
    assert harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True}).status_code == 200
    assert harness.actions(app_id).count("template.changed") == 1

    # Whoever may only open the app may not, nor may a group administrator.
    assert harness.as_user("mate").put(f"/api/apps/{app_id}/template", json={"is_template": False}).status_code == 403
    assert harness.as_user("gadmin").put(f"/api/apps/{app_id}/template", json={"is_template": False}).status_code == 403
    assert harness.as_user("other").put(f"/api/apps/{app_id}/template", json={"is_template": False}).status_code == 404
    assert harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={}).status_code == 422

    unmarked = harness.as_user("admin").put(f"/api/apps/{app_id}/template", json={"is_template": False})
    assert unmarked.status_code == 200 and unmarked.json()["is_template"] is False
    assert harness.actions(app_id).count("template.changed") == 2


def test_the_repositorys_tags_are_listed_with_what_is_published(harness):
    app_id, _versions = template(harness)

    response = harness.as_user("owner").get(f"/api/apps/{app_id}/template/tags")

    assert response.status_code == 200
    assert response.json() == {
        "tags": [
            {"tag": "v1.1.0", "commit_sha": SHA_2, "published": False},
            {"tag": "v1.0.0", "commit_sha": SHA_1, "published": True},
        ]
    }
    assert harness.as_user("mate").get(f"/api/apps/{app_id}/template/tags").status_code == 403
    assert harness.as_user("other").get(f"/api/apps/{app_id}/template/tags").status_code == 404
    assert harness.as_user("admin").get(f"/api/apps/{app_id}/template/tags").status_code == 200


# --- publishing a version ------------------------------------------------------------------------------


def test_publishing_a_tag_stores_its_commit_and_who_published_it(harness):
    app_id = harness.create(visibility="public")["app"]["id"]
    not_yet = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"})
    assert not_yet.status_code == 409 and not_yet.json()["code"] == "not_a_template"
    harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True})
    harness.source.calls.clear()

    response = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": " v1.1.0 ", "notes": "  Second cut.  "})

    assert response.status_code == 201
    version = response.json()
    assert list(version) == ["id", "tag", "commit_sha", "notes", "published_at", "published_by"]
    assert (version["tag"], version["commit_sha"], version["notes"]) == ("v1.1.0", SHA_2, "Second cut.")
    assert version["published_by"] == {"id": harness.users["owner"].id, "username": "owner", "display_name": "Olive"}
    # The manifest was read at the tag's commit, in the app's directory.
    assert ("read_manifest", SHA_2, "apps/mindmap") in harness.source.calls
    stored = harness.db.get(AppTemplateVersion, version["id"])
    assert (stored.app_id, stored.tag, stored.commit_sha, stored.published_by_user_id) == (app_id, "v1.1.0", SHA_2, harness.users["owner"].id)
    assert harness.actions(app_id)[-1] == "template.version_published"

    # A tag is published once, whoever asks and wherever it points by then.
    harness.source.tags["v1.1.0"] = SHA_3
    again = harness.as_user("admin").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.1.0"})
    assert again.status_code == 409 and again.json()["code"] == "version_exists" and again.json()["params"] == {"tag": "v1.1.0"}
    assert harness.db.query(AppTemplateVersion).count() == 1

    # Not for those who may only open the app.
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"}).status_code == 403
    assert harness.as_user("other").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"}).status_code == 404


def test_a_tag_beyond_the_first_page_is_found_by_its_full_name_and_a_branch_is_not_a_tag(harness):
    app_id, _versions = template(harness, versions=())
    harness.source.far_tags["v0.1.0"] = SHA_4

    far = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v0.1.0"})
    assert far.status_code == 201 and far.json()["commit_sha"] == SHA_4
    assert ("resolve_ref", "acme/tools", "tags/v0.1.0") in harness.source.calls

    branch = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "main"})
    assert branch.status_code == 422 and branch.json()["code"] == "tag_not_found"
    assert branch.json()["params"] == {"tag": "main", "repo": "acme/tools"}


@pytest.mark.parametrize(
    "tag, notes, status, code",
    [
        ("", None, 422, "tag_invalid"),
        ("   ", None, 422, "tag_invalid"),
        ("../v1", None, 422, "tag_invalid"),
        ("v1 0", None, 422, "tag_invalid"),
        ("no-such-tag", None, 422, "tag_not_found"),
        ("v1.0.0", "n" * 4001, 422, "notes_too_long"),
    ],
)
def test_a_version_that_cannot_be_published_says_why(harness, tag, notes, status, code):
    app_id, _versions = template(harness, versions=())

    response = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": tag, "notes": notes})

    assert response.status_code == status and response.json()["code"] == code
    assert harness.db.query(AppTemplateVersion).count() == 0


@pytest.mark.parametrize(
    "manifest, status, code",
    [
        # No efp-app.json at the tag's commit.
        (None, 422, "manifest_missing"),
        (b"{not json", 422, "manifest_invalid"),
        ({"name": "Mind map", "type": "teapot", "owners": ["owner"]}, 422, "manifest_invalid"),
        # What the owner could not deploy themselves they cannot hand out either.
        ({**MANIFEST, "owners": ["someone-else"]}, 403, "not_listed_as_owner"),
    ],
)
def test_the_manifest_at_the_tags_commit_is_checked_before_the_version_is_saved(harness, manifest, status, code):
    app_id, _versions = template(harness, versions=())
    harness.source.manifests[SHA_2] = manifest

    response = harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.1.0"})

    assert response.status_code == status and response.json()["code"] == code
    assert harness.db.query(AppTemplateVersion).count() == 0
    # The other tag's commit is fine.
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"}).status_code == 201


def test_versions_are_listed_newest_first_for_whoever_may_open_the_app(harness):
    app_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))

    for name in ("owner", "mate", "gadmin", "admin"):
        response = harness.as_user(name).get(f"/api/apps/{app_id}/template/versions")
        assert response.status_code == 200, name
        listed = response.json()["versions"]
        assert [item["tag"] for item in listed] == ["v1.1.0", "v1.0.0"]
        assert listed[0] == versions["v1.1.0"] and listed[1]["notes"] == "Notes of v1.0.0"
    assert harness.as_user("other").get(f"/api/apps/{app_id}/template/versions").status_code == 404

    # No longer offered: the versions are the owner's business alone.
    harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": False})
    assert harness.as_user("mate").get(f"/api/apps/{app_id}/template/versions").json() == {"versions": []}
    assert len(harness.as_user("owner").get(f"/api/apps/{app_id}/template/versions").json()["versions"]) == 2
    # Offered again, they are all back.
    harness.as_user("owner").put(f"/api/apps/{app_id}/template", json={"is_template": True})
    assert len(harness.as_user("mate").get(f"/api/apps/{app_id}/template/versions").json()["versions"]) == 2


def test_a_version_is_withdrawn_by_the_owner_only(harness):
    app_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))
    other_id, others = template(harness, name="Planner", slug="planner", versions=("v1.0.0",))
    first = versions["v1.0.0"]["id"]

    assert harness.as_user("mate").delete(f"/api/apps/{app_id}/template/versions/{first}").status_code == 403
    assert harness.as_user("other").delete(f"/api/apps/{app_id}/template/versions/{first}").status_code == 404
    unknown = harness.as_user("owner").delete(f"/api/apps/{app_id}/template/versions/nope")
    assert unknown.status_code == 404 and unknown.json()["code"] == "version_not_found"
    # Another template's version is not this app's to remove.
    foreign = harness.as_user("owner").delete(f"/api/apps/{app_id}/template/versions/{others['v1.0.0']['id']}")
    assert foreign.status_code == 404 and foreign.json()["code"] == "version_not_found"

    removed = harness.as_user("owner").delete(f"/api/apps/{app_id}/template/versions/{first}")
    assert removed.status_code == 200 and removed.json() == {"ok": True}
    assert [item["tag"] for item in harness.as_user("owner").get(f"/api/apps/{app_id}/template/versions").json()["versions"]] == ["v1.1.0"]
    assert harness.actions(app_id)[-1] == "template.version_removed"
    assert harness.db.query(AppTemplateVersion).filter(AppTemplateVersion.app_id == other_id).count() == 1
    # The tag may be published again afterwards.
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.0.0"}).status_code == 201


# --- the list members create from ---------------------------------------------------------------------


def test_templates_lists_what_the_member_may_open_and_that_has_a_version(harness):
    public_id, versions = template(harness, name="Mind map", slug="mindmap", versions=("v1.0.0", "v1.1.0"))
    private_id, _ = template(harness, name="Secret notes", slug="secret-notes", visibility="private")
    platform_id, _ = template(harness, name="Planner", slug="planner", visibility="platform")
    empty_id, _ = template(harness, name="Unpublished", slug="unpublished", versions=())
    withdrawn_id, _ = template(harness, name="Withdrawn", slug="withdrawn")
    harness.as_user("owner").put(f"/api/apps/{withdrawn_id}/template", json={"is_template": False})
    plain_id = harness.create(name="Plain", slug="plain", visibility="public")["app"]["id"]

    def listed(name, q=None):
        response = harness.as_user(name).get("/api/apps/templates", params={"q": q} if q else None)
        assert response.status_code == 200, response.text
        return response.json()["templates"]

    # By name; the owner sees their private one too.
    assert [item["id"] for item in listed("owner")] == [public_id, platform_id, private_id]
    assert [item["id"] for item in listed("mate")] == [public_id, platform_id]
    assert [item["id"] for item in listed("other")] == [platform_id]
    # A system administrator is a member here: what is shared with them, not everything there is.
    assert [item["id"] for item in listed("admin")] == [platform_id]
    for hidden in (empty_id, withdrawn_id, plain_id):
        assert hidden not in [item["id"] for item in listed("owner")]

    card = listed("mate")[0]
    assert (card["name"], card["slug"], card["is_template"], card["versions_count"]) == ("Mind map", "mindmap", True, 2)
    assert card["owner"]["username"] == "owner" and card["can_manage"] is False and card["manage"] is None
    assert card["latest_version"] == versions["v1.1.0"]
    assert listed("owner")[0]["manage"]["repo_url"] == REPO_URL

    assert [item["id"] for item in listed("mate", q="plan")] == [platform_id]
    assert listed("mate", q="nothing like it") == []

    # Taken down by an administrator, or its owner gone: not offered.
    harness.as_user("admin").post(f"/api/apps/{platform_id}/disable", json={"reason": "Broken"})
    assert [item["id"] for item in listed("other")] == []
    remove_from_allowlist(harness.db, "owner")
    assert listed("mate") == []


# --- creating an app from a version ------------------------------------------------------------------


def test_an_app_created_from_a_version_is_the_callers_and_runs_the_versions_commit(harness):
    template_id, versions = template(harness)
    version = versions["v1.0.0"]
    harness.source.calls.clear()

    created = derive(harness, template_id, version["id"])

    app, release = created["app"], created["release"]
    assert created["deploy_error"] is None
    assert (app["name"], app["slug"], app["visibility"], app["is_owner"], app["owner"]["username"]) == ("My map", "my-map", "private", True, "mate")
    assert (app["template_app_id"], app["template_version_id"], app["is_template"]) == (template_id, version["id"], False)
    assert app["template"] == {
        "app_id": template_id,
        "app_name": "Mind map",
        "app_slug": "mind-map",
        "owner": {
            "id": harness.users["owner"].id, "username": "owner", "display_name": "Olive",
            "group_id": harness.users["owner"].group_id, "group_name": "QA",
        },
        "version": {"id": version["id"], "tag": "v1.0.0"},
        "update_available": None,
    }
    manage = app["manage"]
    # The template's repository and directory, pinned to the commit.
    assert (manage["repo_url"], manage["entry_dir"], manage["repo_ref"]) == (REPO_URL, "apps/mindmap", SHA_1)
    assert (manage["repo_web_url"], manage["fork_url"]) == ("https://github.com/acme/tools", "https://github.com/acme/tools/fork")
    # Nothing of the owner's is spent or read until they say so.
    assert (manage["llm_enabled"], manage["connectors_enabled"], manage["connectors_write"], manage["env"]) == (False, [], [], {})

    row = harness.row(app["id"])
    assert (row.owner_user_id, row.repo_ref, row.token_value != harness.row(template_id).token_value) == (harness.users["mate"].id, SHA_1, True)
    # The manifest lists only "owner": creating from a published version needs no entry for "mate",
    # at registration or at deployment.
    assert MANIFEST["owners"] == ["owner"]
    assert (release["status"], release["commit_sha"], release["ref"]) == ("succeeded", SHA_1, SHA_1)
    assert deployed_shas(harness) == [SHA_1]
    # The commit was asked for by its id; the tag was never resolved again.
    resolved = [call[2] for call in harness.source.calls if call[0] == "resolve_ref"]
    assert resolved and set(resolved) == {SHA_1}
    assert "app.created" in harness.actions(app["id"])
    assert app_of(harness, app["id"])["deployed"] is True


def test_the_name_and_description_default_to_the_versions_manifest(harness):
    template_id, versions = template(harness)

    created = derive(harness, template_id, versions["v1.0.0"]["id"], name="", deploy=False, slug="maps-of-mine", visibility="public")["app"]

    assert (created["name"], created["slug"], created["description"], created["visibility"]) == (
        "Mind map", "maps-of-mine", "Draw and share mind maps.", "public"
    )
    assert created["deployed"] is False
    described = derive(harness, template_id, versions["v1.0.0"]["id"], name="Third", description="Mine.", deploy=False)["app"]
    assert described["description"] == "Mine."
    taken = harness.as_user("mate").post(
        "/api/apps/from-template",
        json={"template_app_id": template_id, "version_id": versions["v1.0.0"]["id"], "name": "X", "slug": "maps-of-mine"},
    )
    assert taken.status_code == 409 and taken.json()["code"] == "slug_taken"


def test_only_a_template_the_member_may_open_and_a_version_of_it_can_be_created_from(harness):
    template_id, versions = template(harness)
    other_id, others = template(harness, name="Planner", slug="planner")
    plain_id = harness.create(name="Plain", slug="plain", visibility="public")["app"]["id"]
    version_id = versions["v1.0.0"]["id"]

    def attempt(who, **body):
        payload = {"template_app_id": template_id, "version_id": version_id, "name": "Copy", "deploy": False}
        payload.update(body)
        return harness.as_user(who).post("/api/apps/from-template", json=payload)

    # Not in the owner's group: the template does not exist for them.
    outsider = attempt("other")
    assert outsider.status_code == 404 and outsider.json()["code"] == "template_not_found"
    assert attempt("mate", template_app_id="no-such-app").json()["code"] == "template_not_found"
    # An app that is not offered as a template, even one the member may open.
    assert attempt("mate", template_app_id=plain_id).json()["code"] == "template_not_found"
    # A version of another template, or none.
    foreign = attempt("mate", version_id=others["v1.0.0"]["id"])
    assert foreign.status_code == 404 and foreign.json()["code"] == "version_not_found"
    assert attempt("mate", version_id="nope").json()["code"] == "version_not_found"
    assert attempt("mate", version_id=None).status_code == 422

    # Withdrawn, taken down or deleted: no longer something to create from.
    harness.as_user("owner").put(f"/api/apps/{template_id}/template", json={"is_template": False})
    assert attempt("mate").json()["code"] == "template_not_found"
    harness.as_user("owner").put(f"/api/apps/{template_id}/template", json={"is_template": True})
    assert attempt("mate").status_code == 201
    harness.as_user("admin").post(f"/api/apps/{template_id}/disable", json={"reason": "Broken"})
    assert attempt("mate", name="Again").json()["code"] == "template_not_found"
    harness.as_user("admin").post(f"/api/apps/{template_id}/enable")
    harness.as_user("owner").delete(f"/api/apps/{template_id}")
    assert attempt("mate", name="Again").json()["code"] == "template_not_found"
    assert harness.db.query(AppRelease).count() == 0


def test_an_app_stays_on_its_commit_when_the_tag_is_moved(harness):
    template_id, versions = template(harness)
    app_id = derive(harness, template_id, versions["v1.0.0"]["id"])["app"]["id"]
    assert deployed_shas(harness) == [SHA_1]

    # The author moves the tag and the branch to other code.
    harness.source.tags["v1.0.0"] = SHA_3
    harness.source.refs["main"] = SHA_3
    harness.source.calls.clear()

    latest = harness.as_user("mate").post(f"/api/apps/{app_id}/deploy-latest")
    assert latest.status_code == 202 and latest.json()["commit_sha"] == SHA_1
    plain = harness.as_user("mate").post(f"/api/apps/{app_id}/deploy")
    assert plain.status_code == 202 and plain.json()["commit_sha"] == SHA_1
    same = harness.as_user("mate").post(f"/api/apps/{app_id}/deploy", json={"ref": SHA_1})
    assert same.status_code == 202 and same.json()["commit_sha"] == SHA_1
    assert deployed_shas(harness) == [SHA_1] * 4
    assert {call[2] for call in harness.source.calls if call[0] == "resolve_ref"} == {SHA_1}
    assert not [call for call in harness.source.calls if call[0] == "list_tags"]

    # Another branch, tag or commit of the author's repository is not this owner's to deploy.
    for ref in ("main", "v1.0.0", SHA_3):
        moved = harness.as_user("mate").post(f"/api/apps/{app_id}/deploy", json={"ref": ref})
        assert moved.status_code == 409 and moved.json()["code"] == "template_pinned", ref
        changed = harness.as_user("mate").patch(f"/api/apps/{app_id}", json={"repo_ref": ref})
        assert changed.status_code == 409 and changed.json()["code"] == "template_pinned", ref
    assert harness.as_user("admin").patch(f"/api/apps/{app_id}", json={"repo_ref": "main"}).status_code == 409
    # Saying the ref it has, or changing something else, is fine.
    assert harness.as_user("mate").patch(f"/api/apps/{app_id}", json={"repo_ref": SHA_1, "name": "Renamed"}).status_code == 200
    assert harness.row(app_id).repo_ref == SHA_1 and deployed_shas(harness) == [SHA_1] * 4

    status = harness.as_user("mate").get(f"/api/apps/{app_id}/status").json()
    assert status["current_sha"] == SHA_1 and status["repo_ref"] == SHA_1
    # It follows no branch: what it can move to is the template's next version.
    assert status["commits_behind"] is None
    assert not [call for call in harness.source.calls if call[0] == "commits_ahead"]
    assert app_of(harness, app_id)["template"]["version"] == {"id": versions["v1.0.0"]["id"], "tag": "v1.0.0"}


def test_an_app_created_from_a_template_cannot_be_a_template(harness):
    template_id, versions = template(harness)
    app_id = derive(harness, template_id, versions["v1.0.0"]["id"], deploy=False)["app"]["id"]

    for name in ("mate", "admin"):
        response = harness.as_user(name).put(f"/api/apps/{app_id}/template", json={"is_template": True})
        assert response.status_code == 409 and response.json()["code"] == "template_derived"
    # So it cannot publish the author's tags as its own versions.
    publish = harness.as_user("mate").post(f"/api/apps/{app_id}/template/versions", json={"tag": "v1.1.0"})
    assert publish.status_code == 409 and publish.json()["code"] == "not_a_template"
    assert harness.as_user("mate").put(f"/api/apps/{app_id}/template", json={"is_template": False}).status_code == 200


# --- upgrading -------------------------------------------------------------------------------------------


def test_a_newer_version_is_offered_and_the_owner_upgrades_when_they_choose(harness):
    template_id, versions = template(harness)
    first = versions["v1.0.0"]
    app_id = derive(harness, template_id, first["id"])["app"]["id"]
    assert app_of(harness, app_id)["template"]["update_available"] is None

    second = harness.as_user("owner").post(f"/api/apps/{template_id}/template/versions", json={"tag": "v1.1.0"}).json()

    # Offered, to whoever looks at the app; nothing happens by itself.
    assert app_of(harness, app_id)["template"]["update_available"] == {"id": second["id"], "tag": "v1.1.0"}
    assert app_of(harness, app_id, "admin")["template"]["update_available"] == {"id": second["id"], "tag": "v1.1.0"}
    assert app_of(harness, app_id)["template"]["version"] == {"id": first["id"], "tag": "v1.0.0"}
    assert deployed_shas(harness) == [SHA_1] and harness.row(app_id).repo_ref == SHA_1

    # The tag moves after it was published: the upgrade goes to the commit that was published.
    harness.source.tags["v1.1.0"] = SHA_3
    harness.source.calls.clear()
    response = harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={"version_id": second["id"]})

    assert response.status_code == 202
    release = response.json()
    assert (release["kind"], release["status"], release["commit_sha"], release["app_id"]) == ("deploy", "succeeded", SHA_2, app_id)
    assert deployed_shas(harness) == [SHA_1, SHA_2]
    assert {call[2] for call in harness.source.calls if call[0] == "resolve_ref"} == {SHA_2}
    row = harness.row(app_id)
    assert (row.repo_ref, row.template_version_id, row.current_release_id) == (SHA_2, second["id"], release["id"])
    upgraded = app_of(harness, app_id)
    assert upgraded["template"]["version"] == {"id": second["id"], "tag": "v1.1.0"}
    assert upgraded["template"]["update_available"] is None
    assert upgraded["manage"]["repo_ref"] == SHA_2
    # "Deploy latest" now runs the new commit again, and a rollback returns to the old one.
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/deploy-latest").json()["commit_sha"] == SHA_2
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/rollback").json()["commit_sha"] == SHA_1

    # Going back to the older version is the owner's call as well.
    back = harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={"version_id": first["id"]})
    assert back.status_code == 202 and back.json()["commit_sha"] == SHA_1
    assert harness.row(app_id).template_version_id == first["id"]
    assert app_of(harness, app_id)["template"]["update_available"] == {"id": second["id"], "tag": "v1.1.0"}


def test_a_failed_upgrade_leaves_the_app_on_its_version(harness):
    template_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))
    first, second = versions["v1.0.0"], versions["v1.1.0"]
    app_id = derive(harness, template_id, first["id"])["app"]["id"]
    harness.host.job_states = [failed_job()]

    response = harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={"version_id": second["id"]})

    assert response.status_code == 202 and response.json()["status"] == "failed"
    row = harness.row(app_id)
    assert (row.repo_ref, row.template_version_id) == (SHA_1, first["id"])
    shown = app_of(harness, app_id)["template"]
    assert shown["version"] == {"id": first["id"], "tag": "v1.0.0"}
    assert shown["update_available"] == {"id": second["id"], "tag": "v1.1.0"}
    # And "deploy latest" is still the commit it runs.
    harness.host.job_states = None
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/deploy-latest").json()["commit_sha"] == SHA_1


def test_an_upgrade_needs_an_app_from_a_template_a_version_of_that_template_and_the_owners_word(harness):
    template_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))
    other_id, others = template(harness, name="Planner", slug="planner")
    app_id = derive(harness, template_id, versions["v1.0.0"]["id"], deploy=False)["app"]["id"]
    target = {"version_id": versions["v1.1.0"]["id"]}

    # The template itself, and any ordinary app, was not created from a template.
    plain = harness.as_user("owner").post(f"/api/apps/{template_id}/upgrade", json=target)
    assert plain.status_code == 409 and plain.json()["code"] == "not_from_template"
    # A version of another template, or none.
    foreign = harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={"version_id": others["v1.0.0"]["id"]})
    assert foreign.status_code == 404 and foreign.json()["code"] == "version_not_found"
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={"version_id": "nope"}).status_code == 404
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json={}).status_code == 422
    # Not the owner of the app: the template's author included.
    assert harness.as_user("owner").post(f"/api/apps/{app_id}/upgrade", json=target).status_code == 404
    assert harness.as_user("other").post(f"/api/apps/{app_id}/upgrade", json=target).status_code == 404
    assert harness.db.query(AppRelease).count() == 0

    assert harness.as_user("admin").post(f"/api/apps/{app_id}/upgrade", json=target).status_code == 202
    assert harness.row(app_id).template_version_id == versions["v1.1.0"]["id"]


def test_no_upgrade_is_offered_once_the_template_is_no_longer_published_to_the_owner(harness):
    template_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))
    app_id = derive(harness, template_id, versions["v1.0.0"]["id"])["app"]["id"]
    target = {"version_id": versions["v1.1.0"]["id"]}
    assert app_of(harness, app_id)["template"]["update_available"] is not None

    def offered():
        return app_of(harness, app_id)["template"]["update_available"] is not None

    def unavailable():
        refused = harness.as_user("mate").post(f"/api/apps/{app_id}/upgrade", json=target)
        return not offered() and refused.status_code == 409 and refused.json()["code"] == "template_unavailable"

    # The author makes it private again.
    harness.as_user("owner").patch(f"/api/apps/{template_id}", json={"visibility": "private"})
    assert unavailable()
    assert app_of(harness, app_id)["template"]["app_name"] == "Mind map", "where it came from is still told"
    harness.as_user("owner").patch(f"/api/apps/{template_id}", json={"visibility": "public"})
    assert offered()

    # The author stops offering it.
    harness.as_user("owner").put(f"/api/apps/{template_id}/template", json={"is_template": False})
    assert unavailable()
    harness.as_user("owner").put(f"/api/apps/{template_id}/template", json={"is_template": True})

    # An administrator takes the template down.
    harness.as_user("admin").post(f"/api/apps/{template_id}/disable", json={"reason": "Broken"})
    assert unavailable()
    harness.as_user("admin").post(f"/api/apps/{template_id}/enable")
    assert offered()

    # The app's owner leaves the author's group (the template is the group's).
    outsider = harness.db.get(type(harness.users["mate"]), harness.users["mate"].id)
    outsider.group_id = harness.users["other"].group_id
    harness.db.commit()
    assert unavailable()
    outsider.group_id = harness.users["owner"].group_id
    harness.db.commit()
    assert offered()

    # All along the app kept running the commit it is on.
    assert harness.as_user("mate").post(f"/api/apps/{app_id}/deploy-latest").json()["commit_sha"] == SHA_1


def test_removing_the_version_or_the_template_leaves_the_app_running_its_commit(harness):
    template_id, versions = template(harness, versions=("v1.0.0", "v1.1.0"))
    first, second = versions["v1.0.0"], versions["v1.1.0"]
    newest_id = derive(harness, template_id, second["id"])["app"]["id"]
    oldest_id = derive(harness, template_id, first["id"], name="Old map")["app"]["id"]

    # The newest version is withdrawn: the app on it is not offered the older one as an "update".
    harness.as_user("owner").delete(f"/api/apps/{template_id}/template/versions/{second['id']}")
    shown = app_of(harness, newest_id)["template"]
    assert (shown["app_id"], shown["app_name"], shown["version"]) == (template_id, "Mind map", None)
    assert shown["update_available"] == {"id": first["id"], "tag": "v1.0.0"}, "the version it was on is gone: the one that is published is named"
    assert app_of(harness, oldest_id)["template"]["update_available"] is None
    assert harness.row(newest_id).template_version_id == second["id"]
    assert harness.as_user("mate").post(f"/api/apps/{newest_id}/deploy-latest").json()["commit_sha"] == SHA_2
    assert harness.as_user("mate").post(f"/api/apps/{newest_id}/upgrade", json={"version_id": second["id"]}).status_code == 404

    # The template is deleted: the names go, the app stays what it is and still deploys.
    harness.as_user("owner").delete(f"/api/apps/{template_id}")
    gone = app_of(harness, oldest_id)["template"]
    assert gone == {
        "app_id": template_id,
        "app_name": None,
        "app_slug": None,
        "owner": None,
        "version": {"id": first["id"], "tag": "v1.0.0"},
        "update_available": None,
    }
    assert app_of(harness, newest_id)["template"]["version"] is None
    assert harness.as_user("mate").post(f"/api/apps/{oldest_id}/deploy-latest").json()["commit_sha"] == SHA_1
    refused = harness.as_user("mate").post(f"/api/apps/{oldest_id}/upgrade", json={"version_id": first["id"]})
    assert refused.status_code == 409 and refused.json()["code"] == "template_unavailable"
    listed = harness.as_user("mate").get("/api/apps?scope=mine").json()["apps"]
    assert sorted(item["id"] for item in listed) == sorted([newest_id, oldest_id])


def test_a_new_member_in_the_group_creates_from_the_same_version_later(harness):
    template_id, versions = template(harness)
    harness.source.tags["v1.0.0"] = SHA_3
    # A member of no group in particular (the default one).
    harness.users["late"] = add_user(harness.db, "late")
    # Not in the owner's group: nothing to create from.
    refused = harness.as_user("late").post(
        "/api/apps/from-template", json={"template_app_id": template_id, "version_id": versions["v1.0.0"]["id"], "name": "Late"}
    )
    assert refused.status_code == 404

    # Open to every member: the commit that was published is what they get, not where the tag points now.
    harness.as_user("admin").patch(f"/api/apps/{template_id}", json={"visibility": "platform"})
    created = derive(harness, template_id, versions["v1.0.0"]["id"], as_user="late", name="Late")
    assert created["release"]["commit_sha"] == SHA_1 and created["app"]["manage"]["repo_ref"] == SHA_1
    assert deployed_shas(harness) == [SHA_1]
