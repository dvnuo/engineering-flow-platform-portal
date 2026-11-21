"""Templates: an app whose owner publishes tags of its repository as versions others create apps from.

- The owner marks the app as a template and publishes a tag as a version.
  The tag is resolved to its commit once, here, and the manifest at that
  commit is checked the way a deployment checks it; the version keeps the
  commit. A tag can be moved afterwards: nothing that came from the version
  follows it.
- Another member creates an app from a version. The new app is theirs, reads
  the template's repository and entry directory, and is pinned to the
  version's commit (its ``repo_ref`` is that commit id, so every deployment of
  it, "deploy latest" included, runs that commit). The manifest need not list
  them under ``owners``: publishing a version is the author's consent.
- When the template gets a newer version, the owner of an app created from it
  is offered the upgrade and decides.

An app created from a template cannot be a template itself (it would hand out
tags of a repository that is not its owner's), cannot be pointed at another
branch, and is offered versions only while the template is still published
to its owner.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.i18n import N_
from app.models.app import App, AppTemplateVersion
from app.models.user import User
from app.repositories.app_repo import AppRepository
from app.services.apps import audit
from app.services.apps.access import app_enabled, can_manage, can_open, openable_clause
from app.services.apps.errors import AppConflict, AppNotFound, AppValidationError, RefNotFound
from app.services.apps.manifest import check_ownership, parse_manifest
from app.services.apps.source import AppSourceService, check_ref, normalize_entry_dir, source_for

NOTES_MAX_LENGTH = 4000
TAG_REF_PREFIX = "tags/"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def template_pinned_error() -> AppConflict:
    return AppConflict(
        N_("This app was created from a template and runs the version it is on. Upgrade it to another version instead."),
        code="template_pinned",
    )


def is_from_template(app: Any) -> bool:
    return bool(getattr(app, "template_app_id", None))


def check_tag(value: Any) -> str:
    try:
        return check_ref(value)
    except AppValidationError:
        raise AppValidationError(N_("Enter the name of a tag."), code="tag_invalid") from None


def check_notes(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) > NOTES_MAX_LENGTH:
        raise AppValidationError(N_("The notes may be at most {limit} characters."), code="notes_too_long", limit=NOTES_MAX_LENGTH)
    return text or None


def versions_of(db: Session, app_id: str) -> list[AppTemplateVersion]:
    """The app's published versions, newest first."""
    return list(
        db.scalars(
            select(AppTemplateVersion)
            .where(AppTemplateVersion.app_id == app_id)
            .order_by(AppTemplateVersion.published_at.desc(), AppTemplateVersion.id.desc())
        ).all()
    )


def is_offered(db: Session, template: Optional[App], member: Any) -> bool:
    """Whether ``template`` is (still) a published template ``member`` may create from and upgrade to."""
    if template is None or template.deleted_at is not None or not template.is_template:
        return False
    if not app_enabled(db, template)[0]:
        return False
    return member is not None and (can_manage(member, template) or can_open(member, template, db))


def template_info(db: Session, app: App) -> Optional[dict[str, Any]]:
    """The ``template`` object of an app's payload: None for an app that was not created from a template.

    The names are None when the template is gone, the version when it was
    removed. ``update_available`` is the template's newest version when the
    app is not on it and the template is still published to the app's owner.
    """
    if not is_from_template(app):
        return None
    template = db.get(App, app.template_app_id)
    if template is not None and template.deleted_at is not None:
        template = None
    current = db.get(AppTemplateVersion, app.template_version_id) if app.template_version_id else None
    if current is not None and current.app_id != app.template_app_id:
        current = None
    update = None
    if template is not None:
        owner = db.get(User, app.owner_user_id)
        if is_offered(db, template, owner):
            versions = versions_of(db, template.id)
            newest = versions[0] if versions else None
            if newest is not None and newest.id != app.template_version_id:
                if current is None or newest.published_at > current.published_at:
                    update = newest
    return {
        "app_id": app.template_app_id,
        "app_name": template.name if template is not None else None,
        "app_slug": template.slug if template is not None else None,
        "owner_user_id": template.owner_user_id if template is not None else None,
        "version": {"id": current.id, "tag": current.tag} if current is not None else None,
        "update_available": {"id": update.id, "tag": update.tag} if update is not None else None,
    }


class TemplateService:
    def __init__(self, db: Session, *, source: Optional[AppSourceService] = None) -> None:
        self.db = db
        self._source = source

    @property
    def source(self) -> AppSourceService:
        if self._source is None:
            self._source = source_for(self.db)
        return self._source

    # ------------------------------------------------------------------ the owner

    def set_template(self, app: App, actor: Any, is_template: Any) -> App:
        """Offer the app as a template, or stop. Its versions stay recorded either way."""
        wanted = bool(is_template)
        if wanted and is_from_template(app):
            raise AppConflict(N_("An app created from a template cannot be a template itself."), code="template_derived")
        if wanted == bool(app.is_template):
            return app
        app.is_template = wanted
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(
            self.db, app.id, audit.ACTION_TEMPLATE_CHANGED, actor_user_id=getattr(actor, "id", None), detail={"is_template": wanted}
        )
        return app

    def repository_tags(self, app: App) -> list[dict[str, Any]]:
        """The repository's tags (one page, newest first) and whether each is published already."""
        repo = self.source.parse(app.repo_url)
        published = {version.tag for version in versions_of(self.db, app.id)}
        return [
            {"tag": item["name"], "commit_sha": item.get("sha") or "", "published": item["name"] in published}
            for item in self.source.list_tags(repo)
        ]

    def _tag_commit(self, repo: Any, tag: str) -> str:
        """The commit a tag names: from the repository's list of tags, else asked for by its full name."""
        for item in self.source.list_tags(repo):
            if item.get("name") == tag and _SHA_RE.match(str(item.get("sha") or "")):
                return str(item["sha"])
        try:
            # "tags/<name>" names a tag only; the plain name could be a branch.
            return self.source.resolve_ref(repo, TAG_REF_PREFIX + tag).sha
        except RefNotFound:
            raise AppValidationError(
                N_("No tag named {tag} was found in {repo}."), code="tag_not_found", tag=tag, repo=repo.full_name
            ) from None

    def publish(self, app: App, actor: Any, tag: Any, notes: Any = None) -> AppTemplateVersion:
        """Publish a tag of the template's repository as a version: resolved to its commit and checked, once."""
        if not app.is_template:
            raise AppConflict(N_("Mark the app as a template before publishing a version."), code="not_a_template")
        name = check_tag(tag)
        text = check_notes(notes)
        if self._version_by_tag(app.id, name) is not None:
            raise self._exists(name)
        repo = self.source.parse(app.repo_url)
        sha = self._tag_commit(repo, name)
        manifest = parse_manifest(self.source.read_manifest(repo, sha, normalize_entry_dir(app.entry_dir), ref=name))
        # What the owner could not deploy themselves they cannot hand to others either.
        owner = self.db.get(User, app.owner_user_id)
        check_ownership(manifest, owner.username if owner is not None else "")
        version = AppTemplateVersion(
            app_id=app.id,
            tag=name,
            commit_sha=sha,
            notes=text,
            published_by_user_id=getattr(actor, "id", None),
            published_at=datetime.utcnow(),
        )
        self.db.add(version)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise self._exists(name) from None
        self.db.refresh(version)
        audit.record_app_event(
            self.db,
            app.id,
            audit.ACTION_TEMPLATE_VERSION_PUBLISHED,
            actor_user_id=getattr(actor, "id", None),
            detail={"version_id": version.id, "tag": version.tag, "sha": version.commit_sha},
        )
        return version

    def remove_version(self, app: App, actor: Any, version_id: str) -> None:
        """Withdraw a version. Apps created from it keep running its commit; nobody new can pick it."""
        version = self.version_of(app.id, version_id)
        detail = {"version_id": version.id, "tag": version.tag, "sha": version.commit_sha}
        self.db.delete(version)
        self.db.commit()
        audit.record_app_event(
            self.db, app.id, audit.ACTION_TEMPLATE_VERSION_REMOVED, actor_user_id=getattr(actor, "id", None), detail=detail
        )

    # ------------------------------------------------------------------ reading

    @staticmethod
    def _exists(tag: str) -> AppConflict:
        return AppConflict(N_("The tag {tag} is already published as a version."), code="version_exists", tag=tag)

    def _version_by_tag(self, app_id: str, tag: str) -> Optional[AppTemplateVersion]:
        return self.db.scalar(select(AppTemplateVersion).where(AppTemplateVersion.app_id == app_id, AppTemplateVersion.tag == tag))

    def version_of(self, app_id: str, version_id: Any) -> AppTemplateVersion:
        version = self.db.get(AppTemplateVersion, str(version_id or ""))
        if version is None or version.app_id != app_id:
            raise AppNotFound(N_("Version not found"), code="version_not_found")
        return version

    def versions(self, app: App) -> list[AppTemplateVersion]:
        return versions_of(self.db, app.id)

    def list_templates(self, user: Any, *, q: Optional[str] = None) -> list[tuple[App, AppTemplateVersion, int]]:
        """``(template, its newest version, how many versions)`` for every template ``user`` may create from, by name."""
        counts = dict(
            self.db.execute(select(AppTemplateVersion.app_id, func.count(AppTemplateVersion.id)).group_by(AppTemplateVersion.app_id)).all()
        )
        if not counts:
            return []
        clause = App.is_template.is_(True) & App.id.in_(list(counts))
        visible = openable_clause(user, as_member=True)
        if visible is not None:
            clause = clause & visible
        found = []
        for app in AppRepository(self.db).list_where(clause, q=q):
            if not app_enabled(self.db, app)[0]:
                continue
            versions = versions_of(self.db, app.id)
            if versions:
                found.append((app, versions[0], len(versions)))
        return found

    # ------------------------------------------------------- creating and upgrading

    def for_create(self, user: Any, template_app_id: Any, version_id: Any) -> tuple[App, AppTemplateVersion]:
        """The template and the version a member asks to create an app from, when both are theirs to use."""
        template = AppRepository(self.db).get_by_id(str(template_app_id or ""))
        if not is_offered(self.db, template, user):
            # The same answer for "no such template" and "not one you may open".
            raise AppNotFound(N_("Template not found"), code="template_not_found")
        return template, self.version_of(template.id, version_id)

    def for_upgrade(self, app: App, version_id: Any) -> AppTemplateVersion:
        """The version an app created from a template asks to move to."""
        if not is_from_template(app):
            raise AppConflict(N_("This app was not created from a template."), code="not_from_template")
        template = AppRepository(self.db).get_by_id(app.template_app_id)
        owner = self.db.get(User, app.owner_user_id)
        if not is_offered(self.db, template, owner):
            raise AppConflict(
                N_("The template this app was created from is no longer available, so there is nothing to upgrade to."),
                code="template_unavailable",
            )
        return self.version_of(template.id, version_id)
