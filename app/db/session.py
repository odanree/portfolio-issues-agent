from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base

async_engine = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Create the engine, the session factory, and ensure tables exist.

    Called once from the FastAPI lifespan startup. Tests override the
    database URL before calling this (see tests/conftest.py).
    """
    global async_engine, AsyncSessionLocal
    settings = get_settings()
    async_engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )
    AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

    # Ensure model module is imported so its tables register on Base.metadata
    import app.db.models  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with AsyncSessionLocal() as session:
        yield session
