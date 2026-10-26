import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, Query
from fastapi import Request as HTTPRequest
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.actions import upsert_action
from izin_server.auth import current_principal, require_admin, require_agent
from izin_server.creation import create_request
from izin_server.db import get_session
from izin_server.schemas import (
  ActionIn, ActionOut, RequestIn, RequestOut, DecisionStatusOut, 
  InboxDecisionIn, InboxDetail, InboxItem, MeOut, PrincipalCreated, PrincipalIn, PrincipalOut,
  Availability
)
from izin_server.decisions import decide, wait_for_decision, consume
from izin_server.inbox import list_inbox, load_detail
from izin_server.models import Principal
from izin_server.principals import create_principal, rotate_token, set_availability

# router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
admin_router = APIRouter(prefix="/v1", dependencies=[Depends(require_admin)])
agent_router = APIRouter(prefix="/v1", dependencies=[Depends(require_agent)])
inbox_router = APIRouter(prefix="/v1/inbox")

Session = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[Principal, Depends(current_principal)]
ActionName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,99}$")]

@admin_router.put("/actions/{name}", response_model=ActionOut)
async def put_action(name: ActionName, body: ActionIn, session: Session):
  return await upsert_action(session, name, body)

@admin_router.post("/principals", response_model=PrincipalCreated, status_code=201)
async def post_principal(body: PrincipalIn, session: Session):
  principal, token = await create_principal(session, body)
  return PrincipalCreated(**PrincipalOut.model_validate(principal).model_dump(), token=token)

@admin_router.post("/principals/{principal_id}/token", response_model=PrincipalCreated)
async def post_principal_token(principal_id: uuid.UUID, session: Session):
  principal, token = await rotate_token(session, principal_id)
  return PrincipalCreated(**PrincipalOut.model_validate(principal).model_dump(), token=token)

@admin_router.put("/principals/{principal_id}/availability", response_model=Availability)
async def put_principal_availablity(
  principal_id: uuid.UUID, body: Availability, session: Session
):
  return Availability(
    available=await set_availability(session, principal_id, body.available, by="admin")
  )

@agent_router.post("/requests", response_model=RequestOut, status_code=201)
async def post_request(body: RequestIn, response: Response, session: Session):
  request, created = await create_request(session, body)
  if not created:
    response.status_code = 200
  return request

@agent_router.get("/requests/{request_id}/decision", response_model=DecisionStatusOut)
async def get_decision(
  request_id: uuid.UUID, http: HTTPRequest, wait: Annotated[float, Query(ge=0, le=30)] = 0
):
  return await wait_for_decision(request_id, wait, http.is_disconnected)

@agent_router.post("/requests/{request_id}/consume", response_model=DecisionStatusOut)
async def post_consume(request_id: uuid.UUID, session: Session):
  return await consume(session, request_id)

@inbox_router.get("/me", response_model=MeOut)
async def get_me(me: Me):
  return me

@inbox_router.get("/requests", response_model=list[InboxItem])
async def get_inbox(me: Me, session: Session):
  return await list_inbox(session, me)

@inbox_router.get("/requests/{request_id}", response_model=InboxDetail)
async def get_inbox_request(request_id: uuid.UUID, me: Me, session: Session):
  return await load_detail(session, request_id, me)

@inbox_router.post("/requests/{request_id}/decision", response_model=InboxDetail)
async def post_inbox_decision(
  request_id: uuid.UUID, body: InboxDecisionIn, me: Me, session: Session
):
  await decide(session, request_id, me, body)
  return await load_detail(session, request_id, me)

@inbox_router.put("/me/availability", response_model=Availability)
async def put_my_availability(body: Availability, me: Me, session: Session):
  return Availability(
    available=await set_availability(session, me.id, body.available, by="self")
  )