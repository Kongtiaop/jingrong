import asyncio
import pytest
from app.services.memory_service import CustomerMemoryService

class FlakyRedis:
    def __init__(self): self.calls=0; self.state={}
    async def get(self,key):
        self.calls+=1
        if self.calls==1: raise ConnectionError('transient redis outage')
        return self.state.get(key)
    async def set(self,key,value,ex=None): self.state[key]=value

def test_short_memory_recovers_after_transient_redis_fault():
    async def run():
        redis=FlakyRedis(); service=CustomerMemoryService(database=None,redis_client=redis)
        with pytest.raises(ConnectionError): await service.load_short_term(customer_id='c',conversation_id='x')
        await service.save_short_term(customer_id='c',conversation_id='x',state={'ok':True})
        assert (await service.load_short_term(customer_id='c',conversation_id='x'))
    asyncio.run(run())
