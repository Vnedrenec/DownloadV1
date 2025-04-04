class CancellationService:
    def __init__(self, storage):
        self.storage = storage

    async def cancel_operation(self, operation_id):
        # Логика отмены операции
        return {'status': 'cancelled'}
