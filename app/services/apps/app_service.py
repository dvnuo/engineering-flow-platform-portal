"""Register, change, run and remove apps.

The rows are the Portal's; the processes are the app host's. Whenever a
change alters what the host must know (the name, the audience, whether the
app is enabled, its environment) and the host already knows the app, the
host is told first and the row is saved only when it accepted: a narrowed
audience or a take-down is never recorded here while the host keeps serving
the old one. An app nobody has deployed yet does not exist on the host and
is changed freely.

Every change writes an ``app_audit_events`` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.i18n import N_
from app.models.app import App
from app.models.user import User
from app.repositories.app_repo import AppReleaseRepository, AppRepository
from app.services.apps import audit
from app.services.apps import settings as apps_settings
from app.services.apps.access import (
    app_enabled,
    can_manage,
    can_open,
    effective_visibility,
    is_member_active,
    openable_clause,
)
from app.services.apps.errors import (
    AppConflict,
    AppError,
    AppForbidden,
    AppHostError,
    AppHostUnavailable,
    AppNotFound,
    AppValidationError,
    DeployInProgress,
)
from app.services.apps.host_client import HOST_APP_NOT_FOUND, AppHostClient
from app.services.apps.host_spec import host_spec
from app.services.apps.host_state import host_state
from app.services.apps.manifest import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, Manifest, check_ownership, parse_manifest
from app.services.apps.names import (
    APP_CONNECTOR_TYPES,
    APP_VISIBILITIES,
    DESIRED_RUNNING,
    DESIRED_STOPPED,
    ENV_MAX_VALUE_LENGTH,
    ENV_MAX_VARIABLES,
    ENV_NAME_RE,
    ENV_RESERVED_NAMES,
    ENV_RESERVED_PREFIX,
    RESERVED_SLUGS,
    SLUG_MAX_LENGTH,
    VISIBILITY_ANYONE,
    check_slug,
    generated_slug,
    is_valid_slug,
    slug_taken_error,
    slugify,
)
from app.services.apps.source import AppSourceService, RepoRef, ResolvedRef, check_ref, normalize_entry_dir, source_for
from app.services.apps.tokens import hash_token, new_token, new_visitor_key
from app.services.authorization import is_system_admin
from app.services.work_visibility import VISIBILITY_PRIVATE

logger = logging.getLogger(__name__)


class _Unset:
    """A field the caller did not send (None is a value: it clears a description)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET: Any = _Unset()


@dataclass(frozen=True)
class Inspection:
    """A repository directory as it would be registered: where it is, the commit, and its manifest."""

    repo: RepoRef
    resolved: ResolvedRef
    entry_dir: str
    manifest: Manifest


def check_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise AppValidationError(N_("Give the app a name."), code="name_required")
    if len(name) > NAME_MAX_LENGTH:
        raise AppValidationError(N_("The name may be at most {limit} characters."), code="name_too_long", limit=NAME_MAX_LENGTH)
    return name


def check_description(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) > DESCRIPTION_MAX_LENGTH:
        raise AppValidationError(
            N_("The description may be at most {limit} characters."), code="description_too_long", limit=DESCRIPTION_MAX_LENGTH
        )
    return text or None


def check_visibility(value: Any) -> str:
    visibility = str(value or "").strip().lower()
    if visibility not in APP_VISIBILITIES:
        raise AppValidationError(N_("Choose who may open the app: only you, your group, every member, or anyone."), code="visibility_invalid")
    return visibility


def check_env(value: Any) -> dict[str, str]:
    """The owner's environment variables (docs/CONTRACTS.md, section 8): names the host does not keep for itself, string values."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AppValidationError(N_("Environment variables must be given as names with values."), code="env_invalid")
    if len(value) > ENV_MAX_VARIABLES:
        raise AppValidationError(N_("An app may have at most {limit} environment variables."), code="env_too_many", limit=ENV_MAX_VARIABLES)
    env: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name or "").strip()
        if not ENV_NAME_RE.match(name):
            raise AppValidationError(
                N_("{name} is not a valid variable name: use letters, digits and underscores, not starting with a digit."),
                code="env_name_invalid",
                name=name or "(empty)",
            )
        if name.upper().startswith(ENV_RESERVED_PREFIX) or name.upper() in ENV_RESERVED_NAMES:
            raise AppValidationError(
                N_("{name} is set by the platform and cannot be used as a variable name."), code="env_name_reserved", name=name
            )
        if raw_value is None or isinstance(raw_value, (dict, list)):
            raise AppValidationError(N_("The value of {name} must be text."), code="env_value_invalid", name=name)
        text = raw_value if isinstance(raw_value, str) else str(raw_value)
        if len(text) > ENV_MAX_VALUE_LENGTH or "\x00" in text:
            raise AppValidationError(
                N_("The value of {name} is too long (at most {limit} characters)."),
                code="env_value_too_long",
                name=name,
                limit=ENV_MAX_VALUE_LENGTH,
            )
        env[name] = text
    return env


def check_connector_types(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AppValidationError(N_("Connectors must be given as a list."), code="connectors_invalid")
    chosen: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in APP_CONNECTOR_TYPES:
            raise AppValidationError(N_("{name} is not a connector an app can use."), code="connector_unknown", name=name or "(empty)")
        if name not in chosen:
            chosen.append(name)
    return chosen


def _check_budget(value: Any, *, whole: bool) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = -1.0
    if isinstance(value, bool) or number < 0 or number != number or number == float("inf"):
        raise AppValidationError(N_("A daily limit must be zero or a positive number."), code="budget_invalid")
    return float(int(number)) if whole else number


class AppService:
    def __init__(
        self,
        db: Session,
        *,
        host: Optional[AppHostClient] = None,
        source: Optional[AppSourceService] = None,
    ) -> None:
        self.db = db
        self.apps = AppRepository(db)
        self.releases = AppReleaseRepository(db)
        self._host = host
        self._source = source

    @property
    def host(self) -> AppHostClient:
        if self._host is None:
            self._host = AppHostClient()
        return self._host

    @property
    def source(self) -> AppSourceService:
        if self._source is None:
            self._source = source_for(self.db)
        return self._source

    # ------------------------------------------------------------------ reading

    def get(self, app_id: str) -> App:
        app = self.apps.get_by_id(app_id)
        if app is None:
            raise AppNotFound()
        return app

    def get_for_view(self, app_id: str, user: Any) -> App:
        """The app when ``user`` may open or manage it; "not found" otherwise, so a private app's id tells nothing."""
        app = self.get(app_id)
        if not (can_manage(user, app) or can_open(user, app, self.db)):
            raise AppNotFound()
        return app

    def get_for_manage(self, app_id: str, user: Any) -> App:
        app = self.get_for_view(app_id, user)
        if not can_manage(user, app):
            raise AppForbidden()
        return app

    def list_mine(self, user: Any, *, q: Optional[str] = None) -> list[App]:
        return self.apps.list_by_owner(user.id, q=q)

    def list_available(self, user: Any, *, q: Optional[str] = None) -> list[App]:
        """Other members' apps ``user`` may open and that can be opened: deployed and enabled.

        By the visibility rules alone, for a system administrator too: what
        every app is and who owns it is the administration's list (``list_all``).
        """
        clause = openable_clause(user, as_member=True)
        rows = self.apps.list_where(clause, q=q)
        return [
            app
            for app in rows
            if app.owner_user_id != user.id and app.current_release_id and app_enabled(self.db, app)[0]
        ]

    def list_all(self, *, q: Optional[str] = None) -> list[App]:
        return self.apps.list_where(None, q=q)

    def host_known(self, app: App) -> bool:
        """Whether the host may hold a record of the app: a deployment was at least attempted."""
        return bool(app.current_release_id) or self.releases.has_any(app.id)

    # --------------------------------------------------------------- inspecting

    def inspect(self, user: Any, *, repo_url: Any, ref: Any, entry_dir: Any, from_template: bool = False) -> Inspection:
        """Read the repository as a registration or a deployment would: the commit, the manifest, the ownership rule.

        ``user`` is the member who registers (or owns) the app; their Portal
        username is what ``owners`` must list.
        """
        repo = self.source.parse(repo_url)
        directory = normalize_entry_dir(entry_dir)
        resolved = self.source.resolve_ref(repo, check_ref(ref))
        manifest = parse_manifest(self.source.read_manifest(repo, resolved.sha, directory, ref=resolved.ref))
        check_ownership(manifest, getattr(user, "username", ""), from_template=from_template)
        return Inspection(repo=repo, resolved=resolved, entry_dir=directory, manifest=manifest)

    # ----------------------------------------------------------------- creating

    def default_slug(self, name: str) -> str:
        base = slugify(name)
        if base in RESERVED_SLUGS:
            base = f"{base}-app"
        if not is_valid_slug(base):
            base = ""
        if base and not self.apps.slug_exists(base):
            return base
        if base:
            for number in range(2, 50):
                suffix = f"-{number}"
                candidate = f"{base[: SLUG_MAX_LENGTH - len(suffix)].rstrip('-')}{suffix}"
                if is_valid_slug(candidate) and not self.apps.slug_exists(candidate):
                    return candidate
        for _attempt in range(20):
            candidate = generated_slug()
            if not self.apps.slug_exists(candidate):
                return candidate
        raise AppConflict(N_("No free address could be found for this app. Choose one yourself."), code="slug_taken")

    def create(
        self,
        owner: Any,
        *,
        repo_url: Any,
        ref: Any,
        entry_dir: Any = "",
        name: Any = None,
        slug: Any = None,
        description: Any = None,
        visibility: Any = None,
        template_app_id: Optional[str] = None,
        template_version_id: Optional[str] = None,
        skip_ownership_check: bool = False,
    ) -> App:
        """Register a repository directory as ``owner``'s app. Nothing is deployed.

        The repository is read first: an unreadable repository, a missing
        branch, a bad manifest or a member the manifest does not list is an
        error here, not later. ``skip_ownership_check`` is for an app created
        from a published template.
        """
        chosen_visibility = check_visibility(visibility) if visibility else VISIBILITY_PRIVATE
        if chosen_visibility == VISIBILITY_ANYONE:
            self._check_anyone_allowed()
        explicit_slug = check_slug(slug) if str(slug or "").strip() else None
        if explicit_slug and self.apps.slug_exists(explicit_slug):
            raise slug_taken_error(explicit_slug)

        inspection = self.inspect(
            owner, repo_url=repo_url, ref=ref, entry_dir=entry_dir, from_template=skip_ownership_check
        )
        app_name = check_name(name if str(name or "").strip() else inspection.manifest.name)
        app_description = check_description(description) if description is not None else (inspection.manifest.description or None)

        token = new_token()
        app = App(
            slug=explicit_slug or self.default_slug(app_name),
            name=app_name,
            description=app_description,
            owner_user_id=owner.id,
            visibility=chosen_visibility,
            type=inspection.manifest.type,
            repo_url=inspection.repo.web_url,
            repo_ref=inspection.resolved.ref,
            entry_dir=inspection.entry_dir,
            desired_state=DESIRED_RUNNING,
            token_hash=hash_token(token),
            token_value=token,
            visitor_key=new_visitor_key(),
            llm_enabled=False,
            connectors_enabled=[],
            connectors_write=[],
            env_json="{}",
            is_template=False,
            template_app_id=template_app_id,
            template_version_id=template_version_id,
            anyone_approved_by=owner.id if chosen_visibility == VISIBILITY_ANYONE and is_system_admin(owner) else None,
        )
        self.db.add(app)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise slug_taken_error(app.slug) from None
        self.db.refresh(app)
        audit.record_app_event(
            self.db,
            app.id,
            audit.ACTION_CREATED,
            actor_user_id=owner.id,
            detail={
                "slug": app.slug,
                "repo": inspection.repo.full_name,
                "ref": app.repo_ref,
                "entry_dir": app.entry_dir,
                "visibility": app.visibility,
                **({"template_app_id": template_app_id} if template_app_id else {}),
            },
        )
        return app

    # ----------------------------------------------------------------- changing

    def _check_anyone_allowed(self) -> None:
        if not apps_settings.anyone_enabled():
            raise AppValidationError(
                N_("Apps that need no sign-in are turned off on this Portal."), code="anyone_disabled", status_code=400
            )

    def _push_spec(self, app: App) -> Optional[dict]:
        """Tell the host what the app is now (not yet saved); roll the session back and raise when it refuses."""
        if not self.host_known(app):
            return None
        try:
            status = self.host.put_app(app.id, host_spec(self.db, app))
        except AppHostError:
            self.db.rollback()
            raise
        host_state.record_status(status)
        return status

    def update(
        self,
        app: App,
        actor: Any,
        *,
        name: Any = UNSET,
        description: Any = UNSET,
        visibility: Any = UNSET,
        env: Any = UNSET,
        repo_ref: Any = UNSET,
        llm_enabled: Any = UNSET,
        connectors_enabled: Any = UNSET,
        connectors_write: Any = UNSET,
        daily_credits: Any = UNSET,
        daily_tokens: Any = UNSET,
    ) -> list[str]:
        """Apply the fields that were sent; returns the names of those that changed.

        Everything is checked before anything is changed. ``daily_credits``
        and ``daily_tokens`` are a system administrator's to set. Asking for
        ``anyone`` needs the platform switch; it takes effect when a system
        administrator approves the app (at once when one sets it).
        """
        # 1. Check: nothing is touched until every value is known to be good.
        new: dict[str, Any] = {}
        if name is not UNSET:
            new["name"] = check_name(name)
        if description is not UNSET:
            new["description"] = check_description(description)
        if visibility is not UNSET:
            new["visibility"] = check_visibility(visibility)
            if new["visibility"] == VISIBILITY_ANYONE and app.visibility != VISIBILITY_ANYONE:
                self._check_anyone_allowed()
        if env is not UNSET:
            new["env"] = check_env(env)
        if repo_ref is not UNSET:
            new["repo_ref"] = check_ref(repo_ref)
        if llm_enabled is not UNSET:
            new["llm_enabled"] = bool(llm_enabled)
        enabled_connectors = list(app.connectors_enabled or [])
        if connectors_enabled is not UNSET:
            enabled_connectors = check_connector_types(connectors_enabled)
        write_connectors = list(app.connectors_write or [])
        if connectors_write is not UNSET:
            write_connectors = check_connector_types(connectors_write)
        # A connector the app may write to is one it may call.
        new["connectors_enabled"] = enabled_connectors
        new["connectors_write"] = [item for item in write_connectors if item in enabled_connectors]
        if daily_credits is not UNSET:
            new["daily_credits"] = _check_budget(daily_credits, whole=False)
        if daily_tokens is not UNSET:
            tokens = _check_budget(daily_tokens, whole=True)
            new["daily_tokens"] = int(tokens) if tokens is not None else None
        for field_name in ("daily_credits", "daily_tokens"):
            if field_name in new and new[field_name] != getattr(app, field_name) and not is_system_admin(actor):
                raise AppForbidden(N_("Only a system administrator can change an app's daily limits."))

        # 2. Apply.
        changed: list[str] = []
        events: list[tuple[str, dict]] = []
        spec_changed = False
        for field_name in (
            "name",
            "description",
            "repo_ref",
            "llm_enabled",
            "connectors_enabled",
            "connectors_write",
            "daily_credits",
            "daily_tokens",
        ):
            if field_name not in new:
                continue
            current = getattr(app, field_name)
            if field_name in ("connectors_enabled", "connectors_write"):
                current = list(current or [])
            if new[field_name] == current:
                continue
            setattr(app, field_name, new[field_name])
            changed.append(field_name)
            spec_changed = spec_changed or field_name == "name"
        if "visibility" in new and new["visibility"] != app.visibility:
            previous = app.visibility
            app.visibility = new["visibility"]
            # An approval is for the request it answered: leaving ``anyone`` ends it.
            app.anyone_approved_by = actor.id if app.visibility == VISIBILITY_ANYONE and is_system_admin(actor) else None
            changed.append("visibility")
            spec_changed = True
            events.append(
                (audit.ACTION_VISIBILITY_CHANGED, {"from": previous, "to": app.visibility, "in_force": effective_visibility(app)})
            )
        if "env" in new:
            before = app.env
            after = new["env"]
            if after != before:
                app.env_json = json.dumps(after, ensure_ascii=True, sort_keys=True)
                changed.append("env")
                spec_changed = True
                # Names only: a value may be a secret.
                events.append(
                    (
                        audit.ACTION_ENV_CHANGED,
                        {
                            "added": sorted(set(after) - set(before)),
                            "removed": sorted(set(before) - set(after)),
                            "changed": sorted(key for key in set(after) & set(before) if after[key] != before[key]),
                        },
                    )
                )

        if not changed:
            return []
        if spec_changed:
            self._push_spec(app)
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        plain = [item for item in changed if item not in {"visibility", "env"}]
        if plain:
            audit.record_app_event(self.db, app.id, audit.ACTION_UPDATED, actor_user_id=actor.id, detail={"fields": plain})
        for action, detail in events:
            audit.record_app_event(self.db, app.id, action, actor_user_id=actor.id, detail=detail)
        return changed

    def delete(self, app: App, actor: Any, *, purge_data: bool = False) -> None:
        """Take the app off the host and mark it deleted; its slug stays taken for good.

        The app's data survives on the host unless ``purge_data`` is set. A
        host that cannot be reached stops the deletion: the app would keep
        running there.
        """
        if self.releases.active_for_app(app.id) is not None:
            raise DeployInProgress()
        if self.host_known(app):
            try:
                self.host.delete_app(app.id, purge_data=purge_data)
            except AppHostError as exc:
                if exc.host_code != HOST_APP_NOT_FOUND:
                    raise
        app.deleted_at = datetime.utcnow()
        app.desired_state = DESIRED_STOPPED
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        host_state.forget(app.id)
        audit.record_app_event(
            self.db, app.id, audit.ACTION_DELETED, actor_user_id=actor.id, detail={"slug": app.slug, "purge_data": bool(purge_data)}
        )

    # ---------------------------------------------------------------- lifecycle

    def _require_deployed(self, app: App) -> None:
        if not app.current_release_id:
            raise AppConflict(N_("This app has not been deployed yet. Deploy it first."), code="not_deployed")

    def _lifecycle(self, app: App, actor: Any, verb: str, desired: str, action: str) -> dict:
        self._require_deployed(app)
        if verb != "stop":
            enabled, reason = app_enabled(self.db, app)
            if not enabled:
                raise AppConflict(N_("This app is disabled and cannot be started."), code="app_disabled", reason=reason)
            # Whatever the host missed (a token reset while it was away, say) goes with a start.
            self.host.put_app(app.id, host_spec(self.db, app))
        status = getattr(self.host, verb)(app.id)
        host_state.record_status(status)
        app.desired_state = desired
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(self.db, app.id, action, actor_user_id=actor.id)
        return status

    def start(self, app: App, actor: Any) -> dict:
        return self._lifecycle(app, actor, "start", DESIRED_RUNNING, audit.ACTION_STARTED)

    def stop(self, app: App, actor: Any) -> dict:
        return self._lifecycle(app, actor, "stop", DESIRED_STOPPED, audit.ACTION_STOPPED)

    def restart(self, app: App, actor: Any) -> dict:
        return self._lifecycle(app, actor, "restart", DESIRED_RUNNING, audit.ACTION_RESTARTED)

    def status(self, app: App) -> dict[str, Any]:
        """The app's state on the host, live when the host answers and the last report when it does not.

        ``{"host_reachable", "live", "status", "reported_at", "error"}``:
        ``status`` is the host's status object (docs/CONTRACTS.md, section
        4.6) or None when the host has no record of the app.
        """
        error: Optional[AppError] = None
        try:
            live = self.host.get_app(app.id)
        except AppHostUnavailable as exc:
            error = exc
        except AppHostError as exc:
            if exc.host_code == HOST_APP_NOT_FOUND:
                host_state.forget(app.id)
                return {"host_reachable": True, "live": True, "status": None, "reported_at": None, "error": None}
            error = exc
        else:
            host_state.record_status(live)
            return {"host_reachable": True, "live": True, "status": live, "reported_at": datetime.utcnow(), "error": None}
        view = host_state.status_of(app.id)
        return {
            "host_reachable": not isinstance(error, AppHostUnavailable),
            "live": False,
            "status": view.status,
            "reported_at": view.reported_at,
            "error": error,
        }

    def clear_data(self, app: App, actor: Any) -> dict:
        """Empty the app's data on the host."""
        status = self.host.delete_data(app.id)
        host_state.record_status(status)
        audit.record_app_event(self.db, app.id, audit.ACTION_DATA_CLEARED, actor_user_id=actor.id)
        return status

    # ------------------------------------------------------------ administrators

    def disable(self, app: App, actor: Any, reason: Any) -> None:
        """Take the app down with a reason its visitors read. A system administrator's call."""
        text = str(reason or "").strip()
        if not text:
            raise AppValidationError(N_("Say why the app is being disabled."), code="reason_required")
        app.disabled_reason = text[:255]
        self._push_spec(app)
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(self.db, app.id, audit.ACTION_DISABLED, actor_user_id=actor.id, detail={"reason": app.disabled_reason})

    def enable(self, app: App, actor: Any) -> None:
        app.disabled_reason = None
        self._push_spec(app)
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(self.db, app.id, audit.ACTION_ENABLED, actor_user_id=actor.id)

    def set_anyone_approval(self, app: App, admin: Any, approved: bool) -> None:
        """Approve (or withdraw the approval of) an app that asks to be open without sign-in."""
        if approved and app.visibility != VISIBILITY_ANYONE:
            raise AppConflict(N_("This app does not ask to be open without sign-in."), code="anyone_not_requested")
        app.anyone_approved_by = admin.id if approved else None
        self._push_spec(app)
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(
            self.db,
            app.id,
            audit.ACTION_ANYONE_APPROVED if approved else audit.ACTION_ANYONE_REVOKED,
            actor_user_id=admin.id,
            detail={"in_force": effective_visibility(app)},
        )

    def transfer_owner(self, app: App, admin: Any, new_owner: Any) -> None:
        """Give the app to another member.

        The app spends its owner's model provider and reads its owner's
        connectors, so the new owner starts with both switched off and with
        a new token: nothing the previous owner knew or enabled reaches the
        new owner's accounts.
        """
        if new_owner is None or not is_member_active(self.db, new_owner):
            raise AppValidationError(N_("The new owner must be a member with access to the Portal."), code="owner_invalid")
        if new_owner.id == app.owner_user_id:
            return
        previous = self.db.get(User, app.owner_user_id)
        token = new_token()
        app.owner_user_id = new_owner.id
        app.token_value = token
        app.token_hash = hash_token(token)
        app.token_last_used_at = None
        app.token_last_used_source = None
        app.llm_enabled = False
        app.connectors_enabled = []
        app.connectors_write = []
        self._push_spec(app)
        self.db.add(app)
        self.db.commit()
        self.db.refresh(app)
        audit.record_app_event(
            self.db,
            app.id,
            audit.ACTION_OWNER_CHANGED,
            actor_user_id=admin.id,
            detail={"from": previous.username if previous is not None else None, "to": new_owner.username},
        )
