from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_repo import AgentRepository


@dataclass
class RuntimeRoutingDecision:
    matched_agent_id: str | None
    policy_profile_id: str | None
    reason: str
    execution_mode: str = "sync"


class RuntimeRouterService:
    def resolve_agent_runtime(self, agent: Agent) -> dict[str, str | None]:
        return {
            "agent_id": agent.id,
            "namespace": agent.namespace,
            "service_name": agent.service_name,
            "endpoint_path": agent.endpoint_path,
        }

    def find_agent_for_identity_binding(self, system_type: str, external_account_id: str, db: Session) -> Agent | None:
        binding = AgentIdentityBindingRepository(db).find_binding(system_type=system_type, external_account_id=external_account_id)
        if not binding:
            return None
        return AgentRepository(db).get_by_id(binding.agent_id)

    def build_routing_decision(self, agent: Agent | None, reason: str, execution_mode: str = "sync") -> RuntimeRoutingDecision:
        if not agent:
            return RuntimeRoutingDecision(
                matched_agent_id=None,
                policy_profile_id=None,
                reason=reason,
                execution_mode=execution_mode,
            )
        return RuntimeRoutingDecision(
            matched_agent_id=agent.id,
            policy_profile_id=agent.policy_profile_id,
            reason=reason,
            execution_mode=execution_mode,
        )

    def resolve_binding_decision(self, system_type: str, external_account_id: str, db: Session) -> dict:
        agent = self.find_agent_for_identity_binding(system_type=system_type, external_account_id=external_account_id, db=db)
        if not agent:
            return asdict(self.build_routing_decision(None, "no_enabled_binding"))
        return asdict(self.build_routing_decision(agent, "matched_enabled_binding"))
