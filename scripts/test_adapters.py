import asyncio
from app.core.settings import get_settings
from app.repositories.mysql_profile import MySQLRepository
from app.infrastructure.vector_store.milvus import MilvusVectorStore
from app.ports.vector_store import VectorDocument
async def main():
 s=get_settings(); mysql=MySQLRepository(s.mysql_url.replace("127.0.0.1:3306","127.0.0.1:3307")); row=await mysql.fetch_one("SELECT username FROM users WHERE username=%s",("mysql_probe",)); print({"mysql_probe":row}); await mysql.close()
 v=MilvusVectorStore(s.milvus_uri,"wealth_documents",s.embedding_dimension); print({"milvus_collections":v._get().list_collections()})
 print({"vector_contract":"ready"})
if __name__=='__main__': asyncio.run(main())
