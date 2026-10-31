from .sms.knowledge import search_agent_knowledge
from .sms.runtime_context import AgentRuntimeContext

class RagService:
    """Compatibility facade over the authorized generic knowledge service.

    Index writes belong to the durable Persona indexing worker. Keeping them
    out of this request-time service prevents arbitrary callers from creating
    ownerless points.
    """

    def search(self, query: str, context: AgentRuntimeContext, k: int = 5):
        return search_agent_knowledge(query, context, limit=k).get('matches') or []

    def prompt_context(self, query: str, context: AgentRuntimeContext):
        return self.search(query, context)

rag_service = RagService()
