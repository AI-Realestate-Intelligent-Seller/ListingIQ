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
        return self.client.upsert_text(
            self.collection, doc_id, text, metadata=metadata or {})

    def search(self, query: str, k: int = 5):
        return self.client.search_text(self.collection, query, k=k)

    def prompt_context(self, query: str):
        results = self.search(query)
        if not results:
            return []
        return [item.get('metadata') or item for item in results]

rag_service = RagService()
