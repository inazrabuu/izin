from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from izin_server.errors import InvalidInput
from izin_server.models import Principal
from izin_server.schemas import PrincipalIn

async def create_principal(session: AsyncSession, body: PrincipalIn) -> Principal:
  principal = Principal(**body.model_dump())
  session.add(principal)

  try:
    await session.commit()
  except IntegrityError as e:
    raise InvalidInput("a principal with this email already exists") from e
  await session.refresh(principal)
  return principal