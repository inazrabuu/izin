import hashlib
import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.config import settings
from izin_server.db import get_session
from izin_server.errors import Unauthorized
from izin_server.models import Principal

_bearer = HTTPBearer(auto_error=False)
PRINCIPAL_TOKEN_PREFIX = "izin_p_"
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

def new_principal_token() -> str:
  return PRINCIPAL_TOKEN_PREFIX + secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()

def _check_static(creds: HTTPAuthorizationCredentials | None, expected: str, kind: str) -> None:
  if creds is None or not secrets.compare_digest(creds.credentials.encode(), expected.encode()):
    raise Unauthorized(f"missing or invalid {kind} token")

async def require_agent(creds: Bearer) -> None:
  _check_static(creds, settings.api_token, "agent")

async def require_admin(creds: Bearer) -> None:
  _check_static(creds, settings.admin_token, "admin")

async def current_principal(
  creds: Bearer,
  session: DbSession
) -> Principal:
  if creds is None or not creds.credentials.startswith(PRINCIPAL_TOKEN_PREFIX):
    raise Unauthorized("missing or invalid inbox token")
  principal = await session.scalar(
    select(Principal).where(Principal.token_hash == hash_token(creds.credentials))
  )
  if principal is None:
    raise Unauthorized("missing or invalid inbox token")
  return principal