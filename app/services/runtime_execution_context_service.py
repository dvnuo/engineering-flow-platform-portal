from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.contracts.opencode_provider import normalize_model_for_runtime, normalize_provider_for_runtime
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
)
from app.services.runtime_profile_authorization import (
    AUTHORIZATION_ALLOWLIST_KEYS,
    RUNTIME_AUTHORIZATION_EMPTY_LIST_KEYS,
    raw_runtime_profile_config,
    runtime_profile_authorization_metadata,
)
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_runtime_v2_projection import (
    build_trusted_runtime_v2_config,
    is_opencode_runtime_type,
    strip_opencode_runtime_restriction_keys,
)


class RuntimeExecutionContextService:
    def _build_runtime_profile_context_and_authorization(
        self,
        db: Session,
        agent: Agent | None,
    ) -> tuple[str | None, dict | None, dict]:
        runtime_profile_id = getattr(agent, "runtime_profile_id", None) if agent else None
        if not runtime_profile_id:
            return None, None, {}

        profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
        if not profile:
            return runtime_profile_id, None, {}

        try:
            parsed_config = parse_runtime_profile_config_json(
                getattr(profile, "config_json", None),
                fallback_to_empty=True,
            )
        except TypeError:
            parsed_config = parse_runtime_profile_config_json(getattr(profile, "config_json", None))
        except ValueError:
            return runtime_profile_id, None, {}

        materialized_config = RuntimeProfileService.merge_with_managed_defaults(parsed_config)
        runtime_type = getattr(agent, "runtime_type", "") if agent else ""
        authorization_metadata = {}
        if not is_opencode_runtime_type(runtime_type):
            authorization_metadata = runtime_profile_authorization_metadata(
                materialized_config,
                raw_runtime_profile_config(profile),
            )
        runtime_cfg = build_trusted_runtime_v2_config(
            parsed_config,
            runtime_type=runtime_type,
            default_llm=RuntimeProfileService.default_profile_config().get("llm"),
            include_portal_sections=False,
        )
        if is_opencode_runtime_type(runtime_type):
            runtime_cfg["runtime_type"] = "opencode"
        return (
            runtime_profile_id,
            {
                "runtime_profile_id": runtime_profile_id,
                "name": getattr(profile, "name", "") or "",
                "revision": getattr(profile, "revision", None),
                "managed_sections": ["llm", "runtime_v2"],
                "config": runtime_cfg,
                "source": "portal.runtime_profile",
            },
            authorization_metadata,
        )

    def _build_runtime_profile_context(self, db: Session, agent: Agent | None) -> tuple[str | None, dict | None]:
        runtime_profile_id, runtime_profile_context, _authorization_metadata = (
            self._build_runtime_profile_context_and_authorization(db, agent)
        )
        return runtime_profile_id, runtime_profile_context

    def build_for_agent(self, db: Session, agent: Agent | None) -> dict:
        runtime_profile_id, runtime_profile_context, authorization_metadata = (
            self._build_runtime_profile_context_and_authorization(db, agent)
        )

        return {
            "runtime_profile_id": runtime_profile_id,
            "runtime_profile_context": runtime_profile_context,
            "runtime_profile_authorization": authorization_metadata,
        }

    def build_runtime_metadata(self, db: Session, agent: Agent | None, base_metadata: dict | None = None) -> dict:
        metadata = dict(base_metadata or {})
        runtime_type = getattr(agent, "runtime_type", "") if agent else ""
        context = self.build_for_agent(db, agent)
        for key in (
            "authorization_source",
            *AUTHORIZATION_ALLOWLIST_KEYS,
            *RUNTIME_AUTHORIZATION_EMPTY_LIST_KEYS,
        ):
            metadata.pop(key, None)

        metadata["runtime_profile_id"] = context.get("runtime_profile_id")
        runtime_profile_context = context.get("runtime_profile_context") or {}
        if isinstance(runtime_profile_context, dict) and runtime_profile_context:
            runtime_profile_metadata = dict(runtime_profile_context)
            metadata["runtime_profile"] = runtime_profile_metadata
            llm_cfg = ((runtime_profile_context.get("config") or {}).get("llm") or {})
            if isinstance(llm_cfg, dict):
                provider = llm_cfg.get("provider")
                model = llm_cfg.get("model")
                runtime_provider = normalize_provider_for_runtime(runtime_type, provider) if provider else None
                if provider:
                    metadata["provider"] = provider
                full_model = normalize_model_for_runtime(runtime_type, provider, model)
                if runtime_provider:
                    runtime_profile_metadata["provider"] = runtime_provider
                if full_model:
                    metadata["model"] = full_model
                    runtime_profile_metadata["model"] = full_model
                tool_loop = llm_cfg.get("tool_loop")
                if isinstance(tool_loop, dict) and tool_loop:
                    metadata["llm_tool_loop"] = tool_loop
        authorization_metadata = context.get("runtime_profile_authorization")
        if isinstance(authorization_metadata, dict) and authorization_metadata:
            metadata.update(authorization_metadata)

        return strip_opencode_runtime_restriction_keys(metadata, runtime_type)
