"""Library models, forms, and database operations.

The Library is a per-user store of downloadable artifacts (exported reports
and uploaded documents), grouped by chat session in the frontend.
"""

from __future__ import annotations

import logging
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text, and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


class LibraryItem(Base):
    __tablename__ = 'library_item'

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String, index=True)

    chat_id = Column(String, nullable=True, index=True)
    message_id = Column(String, nullable=True)
    chat_title = Column(Text, nullable=True)

    filename = Column(Text)
    content_type = Column(Text, nullable=True)
    size = Column(BigInteger, nullable=True)
    format = Column(String, nullable=True)
    source = Column(String)  # 'report_export' | 'file_upload'

    path = Column(Text, nullable=True)

    created_at = Column(BigInteger, index=True)  # epoch ms


class LibraryItemModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    chat_id: str | None = None
    message_id: str | None = None
    chat_title: str | None = None

    filename: str
    content_type: str | None = None
    size: int | None = None
    format: str | None = None
    source: str

    path: str | None = None

    created_at: int


class LibraryItemForm(BaseModel):
    chat_id: str | None = None
    message_id: str | None = None
    chat_title: str | None = None

    filename: str
    content_type: str | None = None
    size: int | None = None
    format: str | None = None
    source: str

    path: str | None = None


class LibraryTable:
    async def insert_new_item(
        self, user_id: str, form_data: LibraryItemForm, db: AsyncSession | None = None
    ) -> LibraryItemModel | None:
        async with get_async_db_context(db) as db:
            item = LibraryItemModel(
                **{
                    'id': str(uuid.uuid4()),
                    'user_id': user_id,
                    **form_data.model_dump(),
                    'created_at': int(time.time() * 1000),
                }
            )

            try:
                result = LibraryItem(**item.model_dump())
                db.add(result)
                await db.commit()
                return LibraryItemModel.model_validate(result)
            except Exception as e:
                log.exception(f'Error inserting a new library item: {e}')
                return None

    async def get_items_by_user_id(
        self,
        user_id: str,
        chat_id: str | None = None,
        query: str | None = None,
        type: str | None = None,
        db: AsyncSession | None = None,
    ) -> list[LibraryItemModel]:
        async with get_async_db_context(db) as db:
            stmt = select(LibraryItem).where(LibraryItem.user_id == user_id)

            if chat_id is not None:
                stmt = stmt.where(LibraryItem.chat_id == chat_id)

            if query:
                stmt = stmt.where(LibraryItem.filename.ilike(f'%{query}%'))

            if type == 'report':
                stmt = stmt.where(LibraryItem.source == 'report_export')
            elif type == 'file':
                stmt = stmt.where(LibraryItem.source == 'file_upload')

            stmt = stmt.order_by(LibraryItem.created_at.desc())

            result = await db.execute(stmt)
            items = result.scalars().all()
            return [LibraryItemModel.model_validate(item) for item in items]

    async def get_item_by_id(self, id: str, db: AsyncSession | None = None) -> LibraryItemModel | None:
        async with get_async_db_context(db) as db:
            item = await db.get(LibraryItem, id)
            if not item:
                return None
            return LibraryItemModel.model_validate(item)

    async def delete_item_by_id(self, id: str, user_id: str, db: AsyncSession | None = None) -> bool:
        try:
            async with get_async_db_context(db) as db:
                result = await db.execute(
                    delete(LibraryItem).where(and_(LibraryItem.id == id, LibraryItem.user_id == user_id))
                )
                await db.commit()
                return result.rowcount > 0
        except Exception as e:
            log.exception(f'Error deleting library item {id}: {e}')
            return False


Library = LibraryTable()
