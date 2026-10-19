import asyncio
from pathlib import Path

from sqlalchemy import select

from izin_server.actions import upsert_action
from izin_server.config import settings
from izin_server.db import SessionLocal, engine
from izin_server.models import Principal
from izin_server.principals import create_principal, rotate_token
from izin_server.schemas import ActionIn, PrincipalIn

EXAMPLE_ACTION = Path(__file__).resolve().parent.parent / "examples" / "action_refund_order.json"
ANA = PrincipalIn(name="Ana", email="ana@example.com", roles=["approver"])

async def main() -> None:
  async with SessionLocal() as session:
    action = await upsert_action(
      session, "refund_order", ActionIn.model_validate_json(EXAMPLE_ACTION.read_text())
    )

  async with SessionLocal() as session:
    existing = await session.scalar(select(Principal).where(Principal.email == ANA.email))
    if existing is None:
      principal, token = await create_principal(session, ANA)
    else:
      principal, token = await rotate_token(session, existing.id)

    await engine.dispose()

    print(
      f"""
izin is ready.

  Action    {action.name} ({action.display_name})
  Approver  {principal.name} 
  Inbox     {settings.public_url} (on your phone: use your laptop's LAN IP)
  Token     {token}

Sign in to the inbox with this token. It is shown once; re-run this command for a new one.
"""
    )

if __name__ == "__main__":
  asyncio.run(main())