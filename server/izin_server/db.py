from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from izin_server.config import settings

engine = create_async_engine(
  settings.database_url,
  pool_pre_ping=True,
  pool_size=10,
  max_overflow=10
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncIterator[AsyncSession]:
  async with SessionLocal() as session:
    yield session