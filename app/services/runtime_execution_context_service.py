from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.contracts.opencode_provider import normalize_model_for_runtime, normalize_provider_for_runtime
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
    sanitize_runtime_profile_tool_loop,
)
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_llm_projection import project_llm_for_runtime


class RuntimeExecutionContextService:
    def _build_runtime_profile_context(self, db: Session, agent: Agent | None) -> tuple[str | None, dict | None]:
        runtime_profile_id = getattr(agent, "runtime_profile_id", None) if agent else None
        if not runtime_profile_id:
            return None, None

        profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
        if not profile:
            return runtime_profile_id, None

        try:
            parsed_config = parse_runtime_profile_config_json(
                getattr(profile, "config_json", None),
                fallback_to_empty=True,
            )
        except TypeError:
            parsed_config = parse_runtime_profile_config_json(getattr(profile, "config_json", None))
        except ValueError:
            return runtime_profile_id, None

        materialized_config = RuntimeProfileService.merge_with_managed_defaults(parsed_config)
        llm = materialized_config.get("llm") if isinstance(materialized_config, dict) else {}
        raw_tool_loop = llm.get("tool_loop") if isinstance(llm, dict) else {}

        try:
            tool_loop = sanitize_runtime_profile_tool_loop(raw_tool_loop)
        except ValueError:
            return runtime_profile_id, None

        runtime_type = getattr(agent, "runtime_type", "") if agent else ""
        projected_llm = project_llm_for_runtime(llm, runtime_type) if isinstance(llm, dict) else {}
        provider = projected_llm.get("provider") if isinstance(projected_llm, dict) else None
        model = str(projected_llm.get("model") or "").strip() if isinstance(projected_llm, dict) else ""
        base_url = ""
        if isinstance(projected_llm, dict):
            base_url = str(projected_llm.get("base_url") or projected_llm.get("api_base") or projected_llm.get("baseURL") or projected_llm.get("endpoint") or "").strip()
        runtime_cfg = {"llm": {"tool_loop": dict(tool_loop)}}
        if provider:
            runtime_cfg["llm"]["provider"] = provider
        if model:
            runtime_cfg["llm"]["model"] = model
        if base_url:
            runtime_cfg["llm"]["base_url"] = base_url
        if isinstance(projected_llm, dict):
            api_key = str(projected_llm.get("api_key") or "").strip()
            oauth = projected_llm.get("oauth") if isinstance(projected_llm.get("oauth"), dict) else None
            if api_key:
                runtime_cfg["llm"]["api_key"] = api_key
            if oauth:
                runtime_cfg["llm"]["oauth"] = dict(oauth)
        return runtime_profile_id, {
            "runtime_profile_id": runtime_profile_id,
            "name": getattr(profile, "name", "") or "",
            "revision": getattr(profile, "revision", None),
            "managed_sections": ["llm"],
            "config": runtime_cfg,
            "source": "portal.runtime_profile",
        }

    def build_for_agent(self, db: Session, agent: Agent | None) -> dict:
        runtime_profile_id, runtime_profile_context = self._build_runtime_profile_context(db, agent)

        return {
            "runtime_profile_id": runtime_profile_id,
            "runtime_profile_context": runtime_profile_context,
        }

    def build_runtime_metadata(self, db: Session, agent: Agent | None, base_metadata: dict | None = None) -> dict:
        metadata = dict(base_metadata or {})
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
                runtime_type = getattr(agent, "runtime_type", "") if agent else ""
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

        return metadata
