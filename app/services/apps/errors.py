"""What can go wrong with an app, in words a member can act on.

Every error carries a stable ``code``, the HTTP status the owner API answers
with, and a message. The message is an English catalogue key (declared with
``N_()``) with ``{placeholders}`` filled from ``params``: the API sends the
filled English text as ``detail`` together with ``message_key`` and
``params``, so the page can show it in the member's language.
"""

from __future__ import annotations

from typing import Any

from app.i18n import N_, interpolate


class AppError(Exception):
    code = "app_error"
    status_code = 400
    message_key = N_("The request could not be completed.")

    def __init__(self, message_key: str | None = None, *, code: str | None = None, status_code: int | None = None, **params: Any) -> None:
        if message_key is not None:
            self.message_key = message_key
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.params = {key: value for key, value in params.items() if value is not None}
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """The English text with its placeholders filled."""
        return interpolate(self.message_key, self.params)

    def to_payload(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "code": self.code,
            "message_key": self.message_key,
            "params": {key: value for key, value in self.params.items() if isinstance(value, (str, int, float, bool))},
        }


class AppNotFound(AppError):
    code = "app_not_found"
    status_code = 404
    message_key = N_("App not found")


class AppForbidden(AppError):
    code = "forbidden"
    status_code = 403
    message_key = N_("Only the app's owner or an administrator can do this")


class AppValidationError(AppError):
    code = "invalid_request"
    status_code = 422


class AppConflict(AppError):
    code = "conflict"
    status_code = 409


class DeployInProgress(AppConflict):
    code = "deploy_in_progress"
    message_key = N_("A deployment of this app is already running. Wait for it to finish.")


# --- repository source --------------------------------------------------------


class SourceError(AppError):
    code = "source_error"
    status_code = 502
    message_key = N_("The repository could not be read.")


class RepoUrlInvalid(SourceError):
    code = "repo_url_invalid"
    status_code = 422
    message_key = N_("Enter the repository's address, for example https://github.com/owner/repository.")


class RepoNotReadable(SourceError):
    code = "repo_not_readable"
    status_code = 422
    message_key = N_("The platform's service account cannot read {repo}. Add it to the repository as a read-only collaborator, then try again.")


class RefNotFound(SourceError):
    code = "ref_not_found"
    status_code = 422
    message_key = N_("No branch or tag named {ref} was found in {repo}.")


class SourceFileMissing(SourceError):
    code = "file_not_found"
    status_code = 422
    message_key = N_("{path} was not found in {repo} at {sha}.")


class SourceTooLarge(SourceError):
    code = "bundle_too_large"
    status_code = 422
    message_key = N_("The repository's code is larger than {limit_mb} MB, the most an app may be.")


class SourceUnavailable(SourceError):
    code = "github_unreachable"
    status_code = 502
    message_key = N_("GitHub could not be reached: {reason}")


class SourceAuthFailed(SourceError):
    code = "github_auth_failed"
    status_code = 502
    message_key = N_("GitHub refused the platform's service-account token. Ask an administrator to check it.")


# --- manifest -------------------------------------------------------------------


class ManifestError(AppError):
    code = "manifest_invalid"
    status_code = 422
    message_key = N_("efp-app.json is not valid: {problems}")

    def __init__(self, problems: list[tuple[str, dict]] | None = None, **kwargs: Any) -> None:
        self.problems = list(problems or [])
        text = " ".join(interpolate(key, params) for key, params in self.problems)
        super().__init__(problems=text, **kwargs)

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["problems"] = [
            {"detail": interpolate(key, params), "message_key": key, "params": params} for key, params in self.problems
        ]
        return payload


class ManifestMissing(AppError):
    code = "manifest_missing"
    status_code = 422
    message_key = N_("No efp-app.json was found in {path} of {repo} at {ref}. Add one to the app's entry directory.")


class NotListedAsOwner(AppError):
    code = "not_listed_as_owner"
    status_code = 403
    message_key = N_("Your username {username} is not listed under owners in efp-app.json. Add it there and push the change, then try again.")


# --- app host ---------------------------------------------------------------------


class AppHostError(AppError):
    """The app host answered with an error."""

    code = "host_error"
    status_code = 502
    message_key = N_("The app host answered with an error: {reason}")

    @property
    def host_code(self) -> str:
        """The host's own error code (``app_not_found``, ``slug_taken`` ...); empty when it gave none."""
        return str(self.params.get("host_code") or "")

    @property
    def host_status(self) -> int:
        """The HTTP status the host answered with; 0 when it did not answer at all."""
        try:
            return int(self.params.get("host_status") or 0)
        except (TypeError, ValueError):
            return 0


class AppHostUnavailable(AppHostError):
    """The app host could not be reached at all: nothing was changed there."""

    code = "host_unreachable"
    status_code = 503
    message_key = N_("The app host cannot be reached right now. Try again in a moment.")


class AppHostNotConfigured(AppHostUnavailable):
    code = "host_not_configured"
    message_key = N_("The app host is not configured on this Portal. Ask an administrator to set it up.")
