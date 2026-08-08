import asyncio
from types import SimpleNamespace
from app.services.customer_rag_service import CustomerRagService, normalize_customer_query

class FakeRetriever:
    def __init__(self): self.calls=0; self.last_query=''
    async def search(self, **kwargs):
        self.calls+=1; self.last_query=kwargs['query']
        return [SimpleNamespace(score=0.8), SimpleNamespace(score=0.3)]

async def main():
    retriever=FakeRetriever(); service=CustomerRagService(retriever, min_score=0.45)
    greeting=await service.answer(query='你好', intent='faq')
    assert greeting.answer_mode=='greeting' and greeting.evidence_status=='not_required' and not greeting.handoff_required
    assert retriever.calls==0
    company=await service.answer(query='公司做什么', intent='company_business')
    assert company.evidence_status=='sufficient' and len(company.citations)==1 and retriever.calls==1
    company_name=normalize_customer_query('公司名字是什么', 'company_info')
    customer_scale=normalize_customer_query('客户规模多大', 'company_info')
    assert '公司名称' in company_name and '公司叫什么' in company_name
    assert '客户数量' in customer_scale and '服务客户数量' in customer_scale
    product=await service.answer(query='推荐产品', intent='product_recommendation')
    assert product.answer_mode=='database_recommendation' and not product.citations
    print({'greeting_bypasses_retrieval':True,'query_expansion':True,'company_rag':True,'product_uses_database':True,'pass':True})

if __name__=='__main__': asyncio.run(main())
