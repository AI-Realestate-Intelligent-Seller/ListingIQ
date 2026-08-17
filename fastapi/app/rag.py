from .vector_store import vector_store
from module.prompt import prompt

class RagService:
    def __init__(self):
        self.client = vector_store
        self.collection = 'bobbie'

    @property
    def available(self) -> bool:
        return self.client.available

    def index_document(self, doc_id: str, text: str, metadata: dict | None = None):
        vector = self.client.embed_text(text)
        return self.client.upsert_embedding(self.collection, doc_id, vector=vector, metadata=metadata or {}, text=text)

    def search(self, query: str, k: int = 5):
        vector = self.client.embed_text(query)
        return self.client.search(self.collection, vector, k=k)

    def prompt_context(self, query: str):
        results = self.search(query)
        if not results:
            return []
        return [item.get('metadata') or item for item in results]

rag_service = RagService()
