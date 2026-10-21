from datetime import datetime
import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.session_share import SessionShare


def new_share_token() -> str:
    """32 url-safe characters from the OS random source: the only secret of an ``anyone`` share."""

    return secrets.token_urlsafe(24)


class SessionShareRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_token(self, token: str) -> Optional[SessionShare]:
        normalized = str(token or "").strip()
        if not normalized:
            return None
        return self.db.scalars(select(SessionShare).where(SessionShare.token == normalized)).first()

    def get_by_agent_and_session(self, agent_id: str, session_id: str) -> Optional[SessionShare]:
        return self.db.scalars(
            select(SessionShare).where(
                SessionShare.agent_id == agent_id,
                SessionShare.session_id == session_id,
            )
        ).first()

    def list_by_agent(self, agent_id: str) -> list[SessionShare]:
        return list(self.db.scalars(select(SessionShare).where(SessionShare.agent_id == agent_id)).all())

    def shared_session_ids(self, agent_id: str) -> set[str]:
        return {share.session_id for share in self.list_by_agent(agent_id)}

    def create(
        self,
        *,
        agent_id: str,
        session_id: str,
        owner_user_id: int,
        scope: str,
        title: Optional[str] = None,
    ) -> SessionShare:
        share = SessionShare(
            token=new_share_token(),
            agent_id=agent_id,
            session_id=session_id,
            owner_user_id=owner_user_id,
            scope=scope,
            title=title,
        )
        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)
        return share

    def update(self, share: SessionShare, *, scope: Optional[str] = None, title: Optional[str] = None) -> SessionShare:
        if scope is not None:
            share.scope = scope
        if title is not None:
            share.title = title
        share.updated_at = datetime.utcnow()
        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)
        return share

    def delete(self, share: SessionShare) -> None:
        self.db.delete(share)
        self.db.commit()

    def delete_for_session(self, agent_id: str, session_id: str) -> Optional[str]:
        """Drop the session's share; the revoked token, or None when there was none."""

        share = self.get_by_agent_and_session(agent_id, session_id)
        if share is None:
            return None
        token = share.token
        self.delete(share)
        return token

    def delete_for_agent(self, agent_id: str) -> list[str]:
        """Drop every share of an assistant (it is being deleted); the revoked tokens."""

        shares = self.list_by_agent(agent_id)
        tokens = [share.token for share in shares]
        for share in shares:
            self.db.delete(share)
        if shares:
            self.db.commit()
        return tokens
