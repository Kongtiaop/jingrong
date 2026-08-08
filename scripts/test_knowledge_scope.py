import asyncio
from app.services.knowledge_scope import KnowledgeScope

async def main():
    customer = KnowledgeScope.public_customer()
    assert customer.allows(knowledge_base_id='base', category='company_info', permission_level='public')
    assert not customer.allows(knowledge_base_id='base', category='risk', permission_level='internal')
    advisor = KnowledgeScope.internal_advisor()
    assert advisor.allows(knowledge_base_id='base', category='suitability', permission_level='advisor')
    assert not advisor.allows(knowledge_base_id='base', category='company_info', permission_level='customer_visible')
    print({'customer_scope_isolated': True, 'advisor_scope_isolated': True, 'pass': True})

if __name__ == '__main__': asyncio.run(main())
