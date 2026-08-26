"""Email verification token model and data-access layer."""

from __future__ import annotations

import logging
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Tokens expire after 24 hours.
VERIFY_TOKEN_TTL_SECONDS = 24 * 60 * 60


class EmailVerification(Base):
    """One-time email verification token bound to a user."""

    __tablename__ = 'email_verification'

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String)  # mirrors User.id / Auth.id
    expires_at = Column(BigInteger)  # epoch seconds
    used = Column(Boolean)  # one-time use
    created_at = Column(BigInteger)


class EmailVerificationModel(BaseModel):
    id: str
    user_id: str
    expires_at: int
    used: bool
    created_at: int

    model_config = {'from_attributes': True}


class EmailVerifications:
    """CRUD for one-time email verification tokens."""

    @staticmethod
    async def create_token(
        user_id: str,
        db: AsyncSession | None = None,
    ) -> str:
        """Insert a fresh token for *user_id* (invalidating previous ones)."""
        now = int(time.time())
        token = uuid.uuid4().hex
        async with get_async_db_context(db) as session:
            # Invalidate any prior unused tokens for this user.
            await session.execute(
                delete(EmailVerification).where(
                    EmailVerification.user_id == user_id,
                    EmailVerification.used.is_(False),
                )
            )
            row = EmailVerification(
                id=token,
                user_id=user_id,
                expires_at=now + VERIFY_TOKEN_TTL_SECONDS,
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
            row = await session.get(EmailVerification, token)
            if row is None:
                return None
            now = int(time.time())
            if row.used or row.expires_at < now:
                return None
            row.used = True
            await session.commit()
            return row.user_id
