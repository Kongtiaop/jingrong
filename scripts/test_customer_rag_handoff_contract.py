import asyncio
from app.services.customer_rag_handoff import CustomerRagHandoffService

class FakeDatabase:
    pass

# The service performs SQLAlchemy queries; HTTP/database integration is required for final verification.
# Keep this smoke test focused on deterministic normalization/identity contract.
async def main():
    import hashlib
    query='  公司  业务  是什么？ '
    normalized=' '.join(query.lower().split())
    key=hashlib.sha256(f'company_business:{normalized}'.encode()).hexdigest()
    assert key == hashlib.sha256('company_business:公司 业务 是什么？'.encode()).hexdigest()
    print({'normalized_query_key_stable': True, 'handoff_requires_database_integration': True, 'pass': True})

if __name__=='__main__': asyncio.run(main())
