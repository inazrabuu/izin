import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, Query
from fastapi import Request as HTTPRequest
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.actions import upsert_action
from izin_server.auth import require_token
from izin_server.creation import create_request
from izin_server.db import get_session
from izin_server.schemas import ActionIn, ActionOut, RequestIn, RequestOut, DecicionIn, DecisionStatusOut, PrincipalIn, PrincipalOut
from izin_server.principals import create_principal
from izin_server.decisions import decide, wait_for_decision, consume

router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])

Session = Annotated[AsyncSession, Depends(get_session)]
ActionName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,99}$")]

@router.put("/actions/{name}", response_model=ActionOut)
async def put_action(name: ActionName, body: ActionIn, session: Session):
  return await upsert_action(session, name, body)

@router.post("/requests", response_model=RequestOut, status_code=201)
async def post_request(body: RequestIn, response: Response, session: Session):
  request, created = await create_request(session, body)
  if not created:
    response.status_code = 200
  return request

@router.post("/requests/{request_id}/decision", response_model=DecisionStatusOut)
async def post_decision(request_id: uuid.UUID, body: DecicionIn, session: Session):
  return await decide(session, request_id, body)

@router.get("/requests/{request_id}/decision", response_model=DecisionStatusOut)
async def get_decision(
  request_id: uuid.UUID, http: HTTPRequest, wait: Annotated[float, Query(ge=0, le=30)] = 0
):
  return await wait_for_decision(request_id, wait, http.is_disconnected)

@router.post("/principals", response_model=PrincipalOut, status_code=201)
async def post_principal(body: PrincipalIn, session: Session):
  return await create_principal(session, body)

@router.post("/requests/{request_id}/consume", response_model=DecisionStatusOut)
async def post_consume(request_id: uuid.UUID, session: Session):
  return await consume(session, request_id)