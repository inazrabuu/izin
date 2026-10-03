import secrets

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from izin_server.config import settings
from izin_server.errors import Unauthorized

_bearer = HTTPBearer(auto_error=False)

async def require_token(
  creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
  if creds is None or not secrets.compare_digest(
    creds.credentials.encode(), settings.api_token.encode()
  ):
    raise Unauthorized("missing of invalid bearer token")