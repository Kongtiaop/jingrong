import asyncio
from types import SimpleNamespace
from app.services.customer_rag_answer import CustomerRagAnswerService
from app.services.customer_rag_service import CustomerRagResult

class Provider:
    _client = None
    settings = SimpleNamespace(qwen_chat_model='test')

async def main():
    service=CustomerRagAnswerService(Provider())
    empty=CustomerRagResult('company_info','handoff','low_confidence',[],True,'转人工')
    r=await service.generate(query='?',result=empty)
    assert r.generation_status=='not_generated'
    sufficient=CustomerRagResult('company_info','rag_answer','sufficient',[SimpleNamespace(content='公司成立于测试资料',score=.8)],False,'')
    r2=await service.generate(query='公司信息',result=sufficient)
    assert r2.generation_status=='provider_unavailable'
    print({'low_confidence_not_generated':True,'provider_safe_fallback':True,'pass':True})
if __name__=='__main__': asyncio.run(main())
