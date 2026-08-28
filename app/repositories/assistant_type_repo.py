from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assistant_type import AssistantType


class AssistantTypeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AssistantType:
        assistant_type = AssistantType(**kwargs)
        self.db.add(assistant_type)
        self.db.commit()
        self.db.refresh(assistant_type)
        return assistant_type

    def get_by_id(self, type_id: str) -> AssistantType | None:
        return self.db.get(AssistantType, type_id)

    def list_all(self) -> list[AssistantType]:
        query = select(AssistantType).order_by(AssistantType.sort_order.asc(), AssistantType.name.asc())
        return list(self.db.scalars(query).all())

    def list_active(self) -> list[AssistantType]:
        query = (
            select(AssistantType)
            .where(AssistantType.is_active.is_(True))
            .order_by(AssistantType.sort_order.asc(), AssistantType.name.asc())
        )
        return list(self.db.scalars(query).all())

    def save(self, assistant_type: AssistantType) -> AssistantType:
        self.db.add(assistant_type)
        self.db.commit()
        self.db.refresh(assistant_type)
        return assistant_type

    def delete(self, assistant_type: AssistantType) -> None:
        self.db.delete(assistant_type)
        self.db.commit()
