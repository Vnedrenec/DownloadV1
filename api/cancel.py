from fastapi import APIRouter, Depends
from fastapi import Request
from services.cancellation_service import CancellationService

router = APIRouter(prefix='/api', tags=['Cancel'])

def get_storage(request: Request):
    return request.app.state.storage

@router.post('/cancel/{operation_id}')
async def cancel_operation(operation_id: str, storage=Depends(get_storage)):
    cancellation_service = CancellationService(storage)
    return await cancellation_service.cancel_operation(operation_id)
