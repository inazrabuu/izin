import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.auth import hash_token, new_principal_token
from izin_server.errors import InvalidInput, NotFound, Forbidden
from izin_server.models import Principal
from izin_server.schemas import PrincipalIn
from izin_server.system import SYSTEM_PRINCIPAL_IDS

async def create_principal(session: AsyncSession, body: PrincipalIn) -> tuple[Principal, str]:
  token = new_principal_token()
  principal = Principal(**body.model_dump(), token_hash=hash_token(token))
  session.add(principal)
  try:
    await session.commit()
  except IntegrityError as e:
    raise InvalidInput("a principal with this email already exists") from e
  await session.refresh(principal)
  return principal, token

async def rotate_token(session: AsyncSession, principal_id: uuid.UUID) -> tuple[Principal, str]:
  if principal_id in SYSTEM_PRINCIPAL_IDS:
    raise Forbidden("system principals cannot hold token")
  
  principal = await session.get(Principal, principal_id)
  if principal is None:
    raise NotFound("principal not found")
  token = new_principal_token()
  principal.token_hash = hash_token(token)
  await session.commit()
  return principal, token