from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.runtime_router import RuntimeRoutingDecisionResponse, RuntimeTargetInfoResponse
from app.services.runtime_execution_context_service import RuntimeExecutionContextService


class RuntimeRouterService:
    def __init__(self) -> None:
        self.runtime_execution_context_service = RuntimeExecutionContextService()

    @staticmethod
    def _normalize_system_type(system_type: str) -> str:
        return (system_type or "").strip().lower()

    def resolve_agent_runtime(self, agent: Agent) -> RuntimeTargetInfoResponse:
        return RuntimeTargetInfoResponse(
            agent_id=agent.id,
            namespace=agent.namespace,
            service_name=agent.service_name,
            endpoint_path=agent.endpoint_path,
        )

    def _derive_execution_mode(self, agent: Agent | None) -> str:
        if not agent:
            return "sync"
        if agent.agent_type in {"specialist", "task"}:
            return "async_task"
        return "sync"

    def find_agent_for_identity_binding(self, system_type: str, external_account_id: str, db: Session) -> Agent | None:
        normalized_system_type = self._normalize_system_type(system_type)
        binding = AgentIdentityBindingRepository(db).find_binding(
            system_type=normalized_system_type,
            external_account_id=external_account_id,
        )
        if not binding:
            return None
        return AgentRepository(db).get_by_id(binding.agent_id)

    def build_routing_decision(
        self,
        agent: Agent | None,
        db: Session,
        reason: str,
        execution_mode: str | None = None,
    ) -> RuntimeRoutingDecisionResponse:
        effective_execution_mode = execution_mode or self._derive_execution_mode(agent)
        execution_context = self.runtime_execution_context_service.build_for_agent(db, agent)
        capability_context = execution_context.get("capability_context")
        policy_context = execution_context.get("policy_context")

        if not agent:
            return RuntimeRoutingDecisionResponse(
                matched_agent_id=None,
                matched_agent_type=None,
                policy_profile_id=execution_context.get("policy_profile_id"),
                capability_profile_id=execution_context.get("capability_profile_id"),
                reason=reason,
                execution_mode=effective_execution_mode,
                runtime_target=None,
                capability_context=capability_context,
                policy_context=policy_context,
            )
        return RuntimeRoutingDecisionResponse(
            matched_agent_id=agent.id,
            matched_agent_type=agent.agent_type,
            policy_profile_id=execution_context.get("policy_profile_id"),
            capability_profile_id=execution_context.get("capability_profile_id"),
            reason=reason,
            execution_mode=effective_execution_mode,
            runtime_target=self.resolve_agent_runtime(agent),
            capability_context=capability_context,
            policy_context=policy_context,
        )

    def resolve_binding_decision(
        self,
        system_type: str,
        external_account_id: str,
        db: Session,
    ) -> RuntimeRoutingDecisionResponse:
        normalized_system_type = self._normalize_system_type(system_type)
        agent = self.find_agent_for_identity_binding(
            system_type=normalized_system_type,
            external_account_id=external_account_id,
            db=db,
        )
        if not agent:
            return self.build_routing_decision(None, db, "no_enabled_binding")
        return self.build_routing_decision(agent, db, "matched_enabled_binding")

    def resolve_binding_decision_for_event(
        self,
        system_type: str,
        external_account_id: str,
        db: Session,
    ) -> RuntimeRoutingDecisionResponse:
        normalized_system_type = self._normalize_system_type(system_type)
        agent = self.find_agent_for_identity_binding(
            system_type=normalized_system_type,
            external_account_id=external_account_id,
            db=db,
        )
        if not agent:
            return self.build_routing_decision(None, db, "no_enabled_binding", execution_mode="async_task")
        return self.build_routing_decision(agent, db, "matched_enabled_binding", execution_mode="async_task")
