from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.contracts.provider_projection import normalize_model_for_runtime, normalize_provider_for_runtime
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
)
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_context_projection import (
    build_runtime_profile_context_config,
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

        authorization_metadata = {}
        runtime_type = "native"
        runtime_cfg = build_runtime_profile_context_config(
            parsed_config,
            runtime_type=runtime_type,
            default_llm=RuntimeProfileService.default_profile_config().get("llm"),
            include_portal_sections=False,
        )
        return (
            runtime_profile_id,
            {
                "runtime_profile_id": runtime_profile_id,
                "name": getattr(profile, "name", "") or "",
                "revision": getattr(profile, "revision", None),
                "managed_sections": ["llm", "proxy", "jira", "confluence", "github", "git", "debug"],
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
        runtime_type = "native"
        context = self.build_for_agent(db, agent)

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
        return metadata
