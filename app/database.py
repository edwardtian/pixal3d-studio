from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from passlib.context import CryptContext
import asyncio

from app.config import settings
from app.models import Base, User

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def _auto_migrate():
    """Add columns/tables introduced in later versions to an existing DB.

    SQLAlchemy's create_all only creates missing tables — it won't add columns
    to an existing table. We do that here with idempotent ALTER TABLEs.
    """
    new_task_columns = {
        "subtask_index": "INTEGER DEFAULT 0",
        "subtask_total": "INTEGER DEFAULT 6",
        "subtask_name": "VARCHAR(128) DEFAULT ''",
        "subtask_step": "INTEGER DEFAULT 0",
        "subtask_total_steps": "INTEGER DEFAULT 0",
        "overall_progress": "INTEGER DEFAULT 0",
        "assigned_gpu": "INTEGER",
    }
    new_preset_columns = {
        "is_public": "BOOLEAN DEFAULT 0",
    }
    async with engine.begin() as conn:
        # Add missing columns to tasks table
        result = await conn.execute(text("PRAGMA table_info(tasks)"))
        existing_cols = {row[1] for row in result.fetchall()}
        for col, typedef in new_task_columns.items():
            if col not in existing_cols:
                print(f"[DB] Adding column '{col}' to tasks table...")
                await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}"))

        # Add missing columns to presets table
        result = await conn.execute(text("PRAGMA table_info(presets)"))
        existing_preset_cols = {row[1] for row in result.fetchall()}
        for col, typedef in new_preset_columns.items():
            if col not in existing_preset_cols:
                print(f"[DB] Adding column '{col}' to presets table...")
                await conn.execute(text(f"ALTER TABLE presets ADD COLUMN {col} {typedef}"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _auto_migrate()
    await ensure_admin_user()


async def ensure_admin_user():
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
        if result.scalars().first() is None:
            admin = User(
                username=settings.ADMIN_USERNAME,
                hashed_password=pwd_context.hash(settings.ADMIN_PASSWORD),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            await db.commit()


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


async def get_db():
    async with async_session() as db:
        yield db
