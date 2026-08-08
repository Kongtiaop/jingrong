import asyncio
import pytest
from app.services.customer_tools import CustomerReadTools

class BrokenDatabase:
    class Factory:
        def __call__(self): raise ConnectionError('database unavailable')
    session_factory=Factory()

def test_tool_surfaces_database_fault():
    async def run():
        with pytest.raises(ConnectionError):
            await CustomerReadTools(BrokenDatabase()).execute(name='get_my_order_status', customer_id='c', query='我的订单')
    asyncio.run(run())
