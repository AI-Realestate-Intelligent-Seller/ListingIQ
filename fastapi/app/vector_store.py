"""ChromaDB vector store.

Replaces the previous LanceDB client, which could not be used here: lancedb>=0.37
requires pydantic v2 while this app runs FastAPI 0.99 on pydantic v1.

Data is persisted under settings['vector_store']['path'] (data/chroma by
default). The store is optional — if the package or directory is unavailable the
client degrades to a no-op and logs why, rather than failing a request.

Embeddings are always supplied by this module, never by Chroma's default
embedding function, so no model is downloaded at runtime.
"""

import hashlib
import logging
import os

# Must be set before chromadb is imported. Chroma's bundled telemetry client is
# incompatible with current posthog releases and logs an error on every start;
# the product does not need the telemetry either way.
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')
os.environ.setdefault('CHROMA_TELEMETRY_IMPL', 'chromadb.telemetry.product.posthog.Posthog')

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except Exception:  # pragma: no cover - optional dependency
    chromadb = None
    ChromaSettings = None

logging.getLogger('chromadb.telemetry.product.posthog').setLevel(logging.CRITICAL)

from .core.config import settings
from .logger import get_logger

logger = get_logger(__name__)

VECTOR_SIZE = 128


class VectorStore:
    """Thin wrapper over a persistent Chroma client."""

    def __init__(self, path: str | None = None):
        # None means "use the configured path"; an explicit empty string
        # disables the store (used by tests and by opt-out deployments).
        self.path = settings.get('vector_store', {}).get('path') if path is None else path
        self.client = None
        if chromadb is None:
            logger.warning('[vector-store] chromadb not installed; vector store disabled')
            return
        if not self.path:
            logger.warning('[vector-store] no path configured; vector store disabled')
            return
        try:
            self.client = chromadb.PersistentClient(
                path=self.path,
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            logger.info('[vector-store] connected at %s', self.path)
        except Exception as error:
            logger.error('[vector-store] could not open %s: %s', self.path, error)

    @property
    def available(self) -> bool:
        return self.client is not None

    # -- embedding ---------------------------------------------------------
    def _vector_from_text(self, text: str, size: int = VECTOR_SIZE) -> list:
        """Deterministic stand-in embedding. Swap in a real model when one is
        wired up; the storage layer does not care what produced the vector."""
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        vector = [float(byte) / 255.0 for byte in digest]
        while len(vector) < size:
            vector.extend(vector)
        return vector[:size]

    def embed_text(self, text: str) -> list:
        return self._vector_from_text(text)

    # -- storage -----------------------------------------------------------
    def _collection(self, name: str, create: bool = True):
        """Fetch a collection, creating it only when it does not exist.

        Deliberately not get_or_create_collection: passing metadata there
        overwrites what the collection already carries, which silently wiped the
        ingestion fingerprint on every write.
        """
        if self.client is None:
            return None
        try:
            return self.client.get_collection(name=name)
        except Exception:
            pass
        if not create:
            return None
        try:
            # Cosine matches the normalized vectors used here.
            return self.client.create_collection(name=name, metadata={'hnsw:space': 'cosine'})
        except Exception as error:
            logger.error('[vector-store] could not create collection %s: %s', name, error)
            return None

    @staticmethod
    def _clean_metadata(metadata: dict | None) -> dict:
        """Chroma rejects empty metadata dicts and non-scalar values."""
        return {
            key: value for key, value in (metadata or {}).items()
            if isinstance(value, (str, int, float, bool))
        } or {'source': 'unspecified'}

    def upsert_embedding(self, collection: str, id: str, vector: list | None = None,
                         metadata: dict | None = None, text: str | None = None) -> bool:
        if self.client is None:
            return False
        if vector is None:
            if not text:
                return False
            vector = self._vector_from_text(text)
        try:
            handle = self._collection(collection)
            if handle is None:
                return False
            handle.upsert(
                ids=[str(id)],
                embeddings=[vector],
                documents=[text or ''],
                metadatas=[self._clean_metadata(metadata)],
            )
            return True
        except Exception as error:
            logger.error('[vector-store] upsert failed for %s/%s: %s', collection, id, error)
            return False

    def search(self, collection: str, vector: list, k: int = 5) -> list:
        if self.client is None:
            return []
        try:
            handle = self._collection(collection, create=False)
            if handle is None:
                return []
            found = handle.query(
                query_embeddings=[vector],
                n_results=max(1, k),
                include=['documents', 'metadatas', 'distances'],
            )
        except Exception as error:
            logger.error('[vector-store] search failed for %s: %s', collection, error)
            return []

        ids = (found.get('ids') or [[]])[0]
        documents = (found.get('documents') or [[]])[0]
        metadatas = (found.get('metadatas') or [[]])[0]
        distances = (found.get('distances') or [[]])[0]
        return [
            {
                'id': ids[index],
                'text': documents[index] if index < len(documents) else '',
                'metadata': metadatas[index] if index < len(metadatas) else {},
                'score': distances[index] if index < len(distances) else None,
            }
            for index in range(len(ids))
        ]

    def add_many(self, collection: str, ids: list, vectors: list,
                 documents: list | None = None, metadatas: list | None = None) -> bool:
        """Batch insert. Used by document ingestion."""
        if self.client is None or not ids:
            return False
        try:
            handle = self._collection(collection)
            if handle is None:
                return False
            handle.upsert(
                ids=[str(item) for item in ids],
                embeddings=vectors,
                documents=documents or ['' for _ in ids],
                metadatas=[self._clean_metadata(item) for item in (metadatas or [{} for _ in ids])],
            )
            return True
        except Exception as error:
            logger.error('[vector-store] batch add failed for %s: %s', collection, error)
            return False

    def collection_metadata(self, collection: str) -> dict | None:
        handle = self._collection(collection, create=False)
        if handle is None:
            return None
        return dict(handle.metadata or {})

    def reset_collection(self, collection: str, metadata: dict | None = None) -> bool:
        """Drop and recreate a collection — used when an ingest must start clean
        (the source document or the embedding model changed)."""
        if self.client is None:
            return False
        try:
            try:
                self.client.delete_collection(name=collection)
            except Exception:
                pass  # did not exist
            self.client.create_collection(
                name=collection,
                metadata={'hnsw:space': 'cosine', **(metadata or {})},
            )
            return True
        except Exception as error:
            logger.error('[vector-store] reset failed for %s: %s', collection, error)
            return False

    def delete(self, collection: str, id: str) -> bool:
        handle = self._collection(collection, create=False)
        if handle is None:
            return False
        try:
            handle.delete(ids=[str(id)])
            return True
        except Exception as error:
            logger.error('[vector-store] delete failed for %s/%s: %s', collection, id, error)
            return False

    def count(self, collection: str) -> int:
        handle = self._collection(collection, create=False)
        try:
            return handle.count() if handle is not None else 0
        except Exception:
            return 0

    def collections(self) -> list:
        if self.client is None:
            return []
        try:
            return [item.name for item in self.client.list_collections()]
        except Exception:
            return []


vector_store = VectorStore()
