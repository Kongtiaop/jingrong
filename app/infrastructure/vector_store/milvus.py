from __future__ import annotations
from app.ports.vector_store import VectorDocument, VectorHit

class MilvusVectorStore:
    def __init__(self, uri: str = "http://127.0.0.1:19530", collection_name: str = "wealth_documents", dimension: int = 1024):
        self.uri=uri; self.collection_name=collection_name; self.dimension=dimension; self._client=None
    def _get(self):
        if self._client is None:
            from pymilvus import MilvusClient
            self._client=MilvusClient(uri=self.uri)
        return self._client
    def ensure_collection(self):
        from pymilvus import DataType
        c=self._get()
        if c.has_collection(collection_name=self.collection_name): return
        schema=c.create_schema(auto_id=False,enable_dynamic_field=True)
        schema.add_field(field_name="id",datatype=DataType.VARCHAR,is_primary=True,max_length=64)
        schema.add_field(field_name="vector",datatype=DataType.FLOAT_VECTOR,dim=self.dimension)
        index=c.prepare_index_params(); index.add_index(field_name="vector",index_type="AUTOINDEX",metric_type="COSINE")
        c.create_collection(collection_name=self.collection_name,schema=schema,index_params=index)
    async def upsert(self, documents: list[VectorDocument]) -> None:
        self.ensure_collection(); rows=[{"id":d.id,"vector":d.vector,"text":d.text,**d.metadata} for d in documents];
        if rows: self._get().upsert(collection_name=self.collection_name,data=rows); self._get().flush(collection_name=self.collection_name)
    async def search(self, vector: list[float], top_k: int = 5, filters: dict | None = None) -> list[VectorHit]:
        self.ensure_collection(); self._get().load_collection(collection_name=self.collection_name); expr=None
        if filters:
            allowed = {"knowledge_base_id", "category", "document_id", "version_id", "status"}
            expr = " and ".join(f'{k} == "{str(v)}"' for k, v in filters.items() if k in allowed) or None
        result=self._get().search(collection_name=self.collection_name,data=[vector],limit=top_k,filter=expr,output_fields=["text","document_id","version_id","title_path","content_hash","chunk_index","metadata_json"]); hits=[]
        for item in result[0]: hits.append(VectorHit(id=str(item["id"]),score=float(item["distance"]),text=item.get("entity",{}).get("text", ""),metadata=item.get("entity",{})))
        return hits
    async def delete(self, ids: list[str]) -> None:
        self.ensure_collection();
        if ids: self._get().delete(collection_name=self.collection_name,ids=ids)
