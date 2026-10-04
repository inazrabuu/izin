from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.actions import upsert_action
from izin_server.auth import require_token
from izin_server.creation import create_request
from izin_server.db import get_session
from izin_server.schemas import ActionIn, ActionOut, RequestIn, RequestOut

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