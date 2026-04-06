from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.group_shared_context_snapshot import GroupSharedContextSnapshot


class GroupSharedContextSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> GroupSharedContextSnapshot:
        snapshot = GroupSharedContextSnapshot(**kwargs)
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def get_by_group_and_ref(self, group_id: str, context_ref: str) -> GroupSharedContextSnapshot | None:
        stmt = select(GroupSharedContextSnapshot).where(
            and_(
                GroupSharedContextSnapshot.group_id == group_id,
                GroupSharedContextSnapshot.context_ref == context_ref,
            )
        )
        return self.db.scalars(stmt).first()

    def list_by_group_id(self, group_id: str) -> list[GroupSharedContextSnapshot]:
        stmt = (
            select(GroupSharedContextSnapshot)
            .where(GroupSharedContextSnapshot.group_id == group_id)
            .order_by(GroupSharedContextSnapshot.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def save(self, snapshot: GroupSharedContextSnapshot) -> GroupSharedContextSnapshot:
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot


    def upsert_by_group_and_ref(
        self,
        *,
        group_id: str,
        context_ref: str,
        scope_kind: str,
        payload_json: str,
        created_by_user_id: int | None,
        source_delegation_id: str | None,
    ) -> GroupSharedContextSnapshot:
        existing = self.get_by_group_and_ref(group_id, context_ref)
        if not existing:
            return self.create(
                group_id=group_id,
                context_ref=context_ref,
                scope_kind=scope_kind,
                payload_json=payload_json,
                created_by_user_id=created_by_user_id,
                source_delegation_id=source_delegation_id,
            )

        existing.payload_json = payload_json
        existing.scope_kind = scope_kind
        existing.created_by_user_id = created_by_user_id
        existing.source_delegation_id = source_delegation_id
        return self.save(existing)

    def delete(self, snapshot: GroupSharedContextSnapshot) -> None:
        self.db.delete(snapshot)
        self.db.commit()
