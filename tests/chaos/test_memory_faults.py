import asyncio
import pytest
from app.services.memory_service import CustomerMemoryService

class BrokenRedis:
    async def get(self, key): raise ConnectionError('redis unavailable')
    async def set(self, *args, **kwargs): raise ConnectionError('redis unavailable')

def test_memory_service_does_not_silently_swallow_redis_faults():
    async def run():
        service=CustomerMemoryService(database=None, redis_client=BrokenRedis())
        with pytest.raises(ConnectionError):
            await service.load_short_term(customer_id='c', conversation_id='x')
    asyncio.run(run())
