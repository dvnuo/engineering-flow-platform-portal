"""Picker sources for starter cards (``GET /api/suggestions/{source}``).

The page asks on behalf of the signed-in member; the answer is that member's
own recent work, read with their own connector settings
(app/services/suggestions). Nothing here takes a credential, a URL to call or
another member's id from the request.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.i18n import N_
from app.log_context import get_log_context
from app.repositories.agent_repo import AgentRepository
from app.services.proxy_service import ProxyService, build_portal_agent_headers, build_runtime_trace_headers
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.suggestions import SOURCES, describe_sources, suggest
from app.services.suggestions.base import MAX_QUERY_CHARS, ConnectorHttp, SourceContext
from app.services.work_visibility import can_see

router = APIRouter(tags=["suggestions"])
proxy_service = ProxyService()
settings = get_settings()

# Tests set this so no request leaves the process.
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None

# The assistant's own endpoints answer from local files and git; a runtime that
# does not answer this fast is busy, and the picker goes on without it.
RUNTIME_TIMEOUT_SECONDS = 4.0

MAX_PARAMS = 8
MAX_PARAM_VALUES = 10
MAX_PARAM_CHARS = 200
MAX_RECENT = 5
MAX_RECENT_CHARS = 500

_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _params_from(request: Request) -> dict[str, list[str]]:
    """Source parameters, sent as ``p.<name>=<value>`` (repeated for a list)."""
    params: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        if not key.startswith("p."):
            continue
        name = key[2:]
        text = str(value or "").strip()[:MAX_PARAM_CHARS]
        if not _PARAM_NAME.match(name) or not text:
            continue
        if name not in params and len(params) >= MAX_PARAMS:
            continue
        values = params.setdefault(name, [])
        if len(values) < MAX_PARAM_VALUES:
            values.append(text)
    return params


def _runtime_get(user, agent):
    """GET on the assistant's runtime, for the sources that read its workspace.

    Only for the assistant's owner or an administrator: the proxy guards
    workspace files the same way, and a picker must not be a second door.
    """
    if agent is None or agent.status != "running":
        return None
    if user.role != "admin" and agent.owner_user_id != user.id:
        return None

    async def call(subpath: str, query_items: list[tuple[str, str]]) -> tuple[int, Any]:
        code, body, content_type = await asyncio.wait_for(
            proxy_service.forward(
                agent=agent,
                method="GET",
                subpath=subpath,
                query_items=query_items,
                body=None,
                headers={},
                extra_headers={
                    **build_runtime_trace_headers(get_log_context()),
                    **build_portal_agent_headers(user, agent),
                },
            ),
            timeout=RUNTIME_TIMEOUT_SECONDS,
        )
        if "json" not in (content_type or ""):
            return code, None
        try:
            return code, json.loads(body)
        except ValueError:
            return code, None

    return call


@router.get("/api/suggestions")
def list_suggestion_sources(user=Depends(get_current_user)):
    _ = user
    return {"sources": describe_sources()}


@router.get("/api/suggestions/{source_id}")
async def get_suggestions(
    source_id: str,
    request: Request,
    q: str = Query(default="", max_length=MAX_QUERY_CHARS),
    agent_id: str | None = Query(default=None, max_length=64),
    recent: list[str] = Query(default=[]),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if source_id not in SOURCES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("Unknown source"))

    agent = None
    if agent_id:
        agent = AgentRepository(db).get_by_id(agent_id)
        if agent is not None and not can_see(agent, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("Forbidden"))

    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(user)
    config = service.effective_config_for(profile)
    context = SourceContext(
        user=user,
        config=config,
        http=ConnectorHttp(config, transport=HTTP_TRANSPORT),
        db=db,
        settings=settings,
        revision=int(getattr(profile, "revision", 0) or 0),
        q=q.strip(),
        params=_params_from(request),
        recent=[value.strip()[:MAX_RECENT_CHARS] for value in recent if value and value.strip()][:MAX_RECENT],
        agent=agent,
        runtime_get=_runtime_get(user, agent),
    )
    result = await suggest(source_id, context)
    # A member's recent work is theirs: never stored by a browser or a proxy.
    return JSONResponse(result.to_dict(), headers={"Cache-Control": "no-store"})
