import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from open_webui.models.library import Library, LibraryItemForm, LibraryItemModel
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


############################
# List Library Items
############################


@router.get('/', response_model=list[LibraryItemModel])
async def list_library_items(
    chat_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    type: Optional[str] = Query(None, description="Filter by 'report' or 'file'"),
    user=Depends(get_verified_user),
):
    return await Library.get_items_by_user_id(user.id, chat_id=chat_id, query=q, type=type)


############################
# Create Library Item
############################


@router.post('/', response_model=Optional[LibraryItemModel])
async def create_library_item(form_data: LibraryItemForm, user=Depends(get_verified_user)):
    item = await Library.insert_new_item(user.id, form_data)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Error creating library item',
        )
    return item


############################
# Download Library Item
############################


@router.get('/{id}/download')
async def download_library_item(id: str, user=Depends(get_verified_user)):
    item = await Library.get_item_by_id(id)

    if not item or item.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Library item not found',
        )

    if not item.path or not os.path.isfile(item.path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Library item file not found',
        )

    encoded_filename = quote(item.filename)
    return FileResponse(
        Path(item.path),
        media_type=item.content_type or 'application/octet-stream',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


############################
# Delete Library Item
############################


@router.delete('/{id}')
async def delete_library_item(id: str, user=Depends(get_verified_user)):
    item = await Library.get_item_by_id(id)

    if not item or item.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Library item not found',
        )

    deleted = await Library.delete_item_by_id(id, user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Error deleting library item',
        )

    if item.path and os.path.isfile(item.path):
        try:
            os.remove(item.path)
        except OSError as e:
            log.warning(f'Failed to remove library file {item.path}: {e}')

    return {'message': 'Library item deleted successfully'}
