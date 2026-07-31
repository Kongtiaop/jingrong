from app.core.settings import Settings
from app.infrastructure.vector_store.pgvector import PgVectorStore
from app.infrastructure.vector_store.milvus import MilvusVectorStore

def create_vector_store(settings: Settings, database):
    backend = settings.vector_store_backend.lower()
    if backend == "pgvector": return PgVectorStore(database)
    if backend == "milvus": return MilvusVectorStore(uri=settings.milvus_uri, collection_name=settings.milvus_collection, dimension=settings.embedding_dimension)
    raise ValueError(f"unsupported vector store backend: {backend}")
