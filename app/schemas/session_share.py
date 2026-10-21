from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SessionShareUpsertRequest(BaseModel):
    scope: str = "members"
    # The session's name as the member sees it when sharing; shown while the
    # assistant's pod is stopped and the live name cannot be read. Longer
    # names are cut to the column, never refused.
    title: Optional[str] = None


class SessionShareResponse(BaseModel):
    token: str
    url: str
    scope: str
    agent_id: str
    session_id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Whether this deployment offers the "anyone with the link" audience.
    anyone_enabled: bool = True
