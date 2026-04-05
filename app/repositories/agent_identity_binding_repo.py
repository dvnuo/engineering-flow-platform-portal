from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.agent_identity_binding import AgentIdentityBinding
from typing import Optional


class AgentIdentityBindingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentIdentityBinding:
        binding = AgentIdentityBinding(**kwargs)
        self.db.add(binding)
        self.db.commit()
        self.db.refresh(binding)
        return binding

    def get_by_id(self, binding_id: str) -> Optional[AgentIdentityBinding]:
        return self.db.get(AgentIdentityBinding, binding_id)

    def list_by_agent(self, agent_id: str) -> list[AgentIdentityBinding]:
        return list(
            self.db.scalars(
                select(AgentIdentityBinding)
                .where(AgentIdentityBinding.agent_id == agent_id)
                .order_by(AgentIdentityBinding.created_at.desc())
            ).all()
        )

    def list_enabled_bindings_for_agent(self, agent_id: str) -> list[AgentIdentityBinding]:
        return list(
            self.db.scalars(
                select(AgentIdentityBinding)
                .where(and_(AgentIdentityBinding.agent_id == agent_id, AgentIdentityBinding.enabled.is_(True)))
                .order_by(AgentIdentityBinding.created_at.desc())
            ).all()
        )

    def find_binding(self, system_type: str, external_account_id: str) -> Optional[AgentIdentityBinding]:
        stmt = (
            select(AgentIdentityBinding)
            .where(
                and_(
                    AgentIdentityBinding.system_type == system_type,
                    AgentIdentityBinding.external_account_id == external_account_id,
                    AgentIdentityBinding.enabled.is_(True),
                )
            )
            .order_by(AgentIdentityBinding.created_at.desc())
        )
        return self.db.scalars(stmt).first()

    def save(self, binding: AgentIdentityBinding) -> AgentIdentityBinding:
        self.db.add(binding)
        self.db.commit()
        self.db.refresh(binding)
        return binding

    def delete(self, binding: AgentIdentityBinding) -> None:
        self.db.delete(binding)
        self.db.commit()
