"""Password reset token model and data-access layer."""

from __future__ import annotations

import logging
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, String, delete
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Tokens expire after 24 hours.
RESET_TOKEN_TTL_SECONDS = 24 * 60 * 60


class PasswordReset(Base):
    """One-time password reset token bound to a user."""

    __tablename__ = 'password_reset'

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String)  # mirrors User.id / Auth.id
    expires_at = Column(BigInteger)  # epoch seconds
    used = Column(Boolean)  # one-time use
    created_at = Column(BigInteger)


class PasswordResetModel(BaseModel):
    id: str
    user_id: str
    expires_at: int
    used: bool
    created_at: int

    model_config = {'from_attributes': True}


class PasswordResets:
    """CRUD for one-time password reset tokens."""

    @staticmethod
    async def create_token(
        user_id: str,
        db: AsyncSession | None = None,
    ) -> str:
        """Insert a fresh token for *user_id* (invalidating previous ones)."""
        now = int(time.time())
        token = uuid.uuid4().hex
        async with get_async_db_context(db) as session:
            # A new request invalidates every prior unused token for this user,
            # so only the most recent link in the user's inbox works.
            await session.execute(
                delete(PasswordReset).where(
                    PasswordReset.user_id == user_id,
                    PasswordReset.used.is_(False),
                )
            )
            row = PasswordReset(
                id=token,
                user_id=user_id,
                expires_at=now + RESET_TOKEN_TTL_SECONDS,
                used=False,
                created_at=now,
            )
            session.add(row)
            await session.commit()
        return token

    @staticmethod
    async def consume_token(
        token: str,
        db: AsyncSession | None = None,
    ) -> str | None:
        """Atomically consume a token and return its user_id, or None.

        Returns None for unknown, expired, or already-used tokens.
        """
        async with get_async_db_context(db) as session:
            row = await session.get(PasswordReset, token)
            if row is None:
                return None
            now = int(time.time())
            if row.used or row.expires_at < now:
                return None
            row.used = True
            await session.commit()
            return row.user_id
