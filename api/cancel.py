from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request
from services.cancellation_service import CancellationService
import logging

router = APIRouter(prefix='/api', tags=['Cancel'])

def get_storage(request: Request):
    return request.app.state.storage

@router.post('/cancel/{operation_id}')
async def cancel_operation(operation_id: str, request: Request, storage=Depends(get_storage)):
    """Отменяет загрузку видео"""
    cancellation_service = CancellationService(storage)
    return await cancellation_service.cancel_operation(operation_id)
