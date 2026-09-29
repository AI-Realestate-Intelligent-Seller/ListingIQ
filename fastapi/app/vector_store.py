"""Vector-store adapters used by RAG and the SMS knowledge index.

ChromaDB remains the local/default backend. Qdrant is selected only when both
a valid HTTP(S) QDRANT_URL and a non-empty QDRANT_API_KEY are configured.
Both adapters expose the same interface, so retrieval and indexing logic is
independent of the selected storage engine.

Chroma embeddings remain local through the existing MiniLM/hash provider. Qdrant
Cloud sends raw text descriptors to managed inference and loads no local model.
"""

import hashlib
import logging
import os
import uuid
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

# Must be set before chromadb is imported. Chroma's bundled telemetry client is
# incompatible with current posthog releases and logs an error on every start;
# the product does not need the telemetry either way.
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')
os.environ.setdefault('CHROMA_TELEMETRY_IMPL', 'chromadb.telemetry.product.posthog.Posthog')

chromadb = None
ChromaSettings = None
_chroma_import_attempted = False


def _load_chromadb():
    """Import Chroma only when the Chroma adapter is actually selected."""
    global chromadb, ChromaSettings, _chroma_import_attempted
    if _chroma_import_attempted:
        return chromadb, ChromaSettings
    _chroma_import_attempted = True
    try:
        import importlib

        chromadb = importlib.import_module('chromadb')
        ChromaSettings = importlib.import_module('chromadb.config').Settings
    except Exception:  # pragma: no cover - optional dependency
        chromadb = None
        ChromaSettings = None
    return chromadb, ChromaSettings

try:
    from qdrant_client import QdrantClient
    from qdrant_client import models as qdrant_models
except Exception:  # pragma: no cover - optional dependency
    QdrantClient = None
    qdrant_models = None

logging.getLogger('chromadb.telemetry.product.posthog').setLevel(logging.CRITICAL)

from .core.config import settings
from .logger import get_logger

logger = get_logger(__name__)

VECTOR_SIZE = 128
DEFAULT_QDRANT_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
DEFAULT_QDRANT_DIMENSION = 384


def _local_embedding_provider():
    """Load the existing local MiniLM provider only for local embedding paths."""
    from .sms.embeddings import embedding_provider

    return embedding_provider


@runtime_checkable
class VectorStoreInterface(Protocol):
    """Storage-independent contract consumed by existing RAG callers."""

    @property
    def available(self) -> bool: ...

    @property
    def embedding_name(self) -> str: ...

    @property
    def semantic_embeddings(self) -> bool: ...
    def embed_text(self, text: str): ...
    def upsert_text(self, collection: str, id: str, text: str,
                    metadata: dict | None = None,
                    embedding_text: str | None = None) -> bool: ...
    def add_texts(self, collection: str, ids: list, texts: list,
                  documents: list | None = None,
                  metadatas: list | None = None,
                  embedding_texts: list | None = None) -> bool: ...
    def search_text(self, collection: str, text: str, k: int = 5) -> list: ...
    def upsert_embedding(self, collection: str, id: str, vector: list | None = None,
                         metadata: dict | None = None, text: str | None = None) -> bool: ...
    def search(self, collection: str, vector: list, k: int = 5) -> list: ...
    def add_many(self, collection: str, ids: list, vectors: list,
                 documents: list | None = None, metadatas: list | None = None) -> bool: ...
    def collection_metadata(self, collection: str) -> dict | None: ...
    def reset_collection(self, collection: str, metadata: dict | None = None) -> bool: ...
    def delete(self, collection: str, id: str) -> bool: ...
    def count(self, collection: str) -> int: ...
    def collections(self) -> list: ...


def _valid_qdrant_config(url: str | None, api_key: str | None) -> bool:
    """Require both credentials and a valid remote HTTP(S) URL."""
    if not str(url or '').strip() or not str(api_key or '').strip():
        return False
    parsed = urlparse(str(url).strip())
    return (parsed.scheme in ('http', 'https') and bool(parsed.netloc)
            and parsed.username is None and parsed.password is None)


class ChromaVectorStore:
    """Thin wrapper over a persistent Chroma client."""

    backend = 'chromadb'

    def __init__(self, path: str | None = None):
        # None means "use the configured path"; an explicit empty string
        # disables the store (used by tests and by opt-out deployments).
        self.path = settings.get('vector_store', {}).get('path') if path is None else path
        self.url = None
        self.client = None
        _load_chromadb()
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
    @staticmethod
    def _vector_from_text(text: str, size: int = VECTOR_SIZE) -> list:
        """Deterministic stand-in embedding. Swap in a real model when one is
        wired up; the storage layer does not care what produced the vector."""
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        vector = [float(byte) / 255.0 for byte in digest]
        while len(vector) < size:
            vector.extend(vector)
        return vector[:size]

    def embed_text(self, text: str) -> list:
        return self._vector_from_text(text)

    @property
    def embedding_name(self) -> str:
        return _local_embedding_provider().name

    @property
    def semantic_embeddings(self) -> bool:
        return _local_embedding_provider().is_semantic

    def upsert_text(self, collection: str, id: str, text: str,
                    metadata: dict | None = None,
                    embedding_text: str | None = None) -> bool:
        vector = _local_embedding_provider().embed_one(embedding_text or text)
        return self.upsert_embedding(
            collection, id, vector=vector, metadata=metadata, text=text)

    def add_texts(self, collection: str, ids: list, texts: list,
                  documents: list | None = None,
                  metadatas: list | None = None,
                  embedding_texts: list | None = None) -> bool:
        vectors = _local_embedding_provider().embed(embedding_texts or texts)
        return self.add_many(
            collection, ids, vectors, documents=documents, metadatas=metadatas)

    def search_text(self, collection: str, text: str, k: int = 5) -> list:
        return self.search(
            collection, _local_embedding_provider().embed_one(text), k=k)

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



class QdrantVectorStore:
    """Qdrant adapter with Cloud Inference and explicit self-hosted modes."""

    backend = 'qdrant'
    _id_namespace = uuid.UUID('5fa32220-42b7-4f3f-9cc8-e77254292845')

    def __init__(self, url: str | None = None, api_key: str | None = None,
                 timeout: float | None = None, cloud_inference: bool | None = None,
                 embedding_model: str | None = None,
                 vector_dimension: int | None = None):
        config = settings.get('vector_store', {})
        self.url = (url if url is not None else config.get('qdrant_url')) or ''
        self.api_key = (
            api_key if api_key is not None else config.get('qdrant_api_key')
        ) or ''
        self.timeout = timeout if timeout is not None else config.get(
            'qdrant_timeout_seconds', 10.0)
        self.cloud_inference = (
            bool(cloud_inference)
            if cloud_inference is not None
            else bool(config.get('qdrant_cloud_inference', True))
        )
        self.embedding_model = (
            embedding_model
            or config.get('embedding_model')
            or DEFAULT_QDRANT_MODEL
        )
        self.vector_dimension = int(
            vector_dimension
            if vector_dimension is not None
            else config.get('vector_dimension', DEFAULT_QDRANT_DIMENSION)
        )
        self.path = None
        self.client = None
        # reset_collection is called before any embedding request. Creation is
        # deferred until the following upsert so metadata is retained.
        self._pending_collection_metadata: dict[str, dict] = {}

        if QdrantClient is None or qdrant_models is None:
            logger.error('[vector-store] qdrant-client not installed; Qdrant unavailable')
            return
        if not _valid_qdrant_config(self.url, self.api_key):
            logger.error('[vector-store] invalid Qdrant URL/API key; Qdrant unavailable')
            return
        try:
            self.client = QdrantClient(
                url=self.url,
                api_key=self.api_key,
                timeout=float(self.timeout),
                cloud_inference=self.cloud_inference,
                check_compatibility=False,
            )
            inference = 'cloud' if self.cloud_inference else 'local'
            logger.info('[vector-store] configured Qdrant at %s (%s inference)',
                        self.url, inference)
        except Exception as error:
            logger.error('[vector-store] could not configure Qdrant at %s: %s',
                         self.url, error)

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def embedding_name(self) -> str:
        if self.cloud_inference:
            return self.embedding_model
        return _local_embedding_provider().name

    @property
    def semantic_embeddings(self) -> bool:
        if self.cloud_inference:
            return True
        return _local_embedding_provider().is_semantic

    @classmethod
    def _point_id(cls, source_id: str) -> str:
        """Map arbitrary existing string IDs to Qdrant-compatible UUIDs."""
        return str(uuid.uuid5(cls._id_namespace, str(source_id)))

    @staticmethod
    def _clean_metadata(metadata: dict | None) -> dict:
        return ChromaVectorStore._clean_metadata(metadata)

    def _document(self, text: str):
        return qdrant_models.Document(
            text=str(text or ''),
            model=self.embedding_model,
        )

    def embed_text(self, text: str):
        """Return a remote inference descriptor or a self-hosted local vector."""
        if self.cloud_inference:
            return self._document(text)
        return _local_embedding_provider().embed_one(text)

    def upsert_text(self, collection: str, id: str, text: str,
                    metadata: dict | None = None,
                    embedding_text: str | None = None) -> bool:
        vector = (
            self._document(text)
            if self.cloud_inference
            else _local_embedding_provider().embed_one(embedding_text or text)
        )
        return self._upsert_many(
            collection,
            ids=[id],
            vectors=[vector],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def add_texts(self, collection: str, ids: list, texts: list,
                  documents: list | None = None,
                  metadatas: list | None = None,
                  embedding_texts: list | None = None) -> bool:
        vectors = (
            [self._document(text) for text in texts]
            if self.cloud_inference
            else _local_embedding_provider().embed(embedding_texts or texts)
        )
        return self._upsert_many(
            collection,
            ids=ids,
            vectors=vectors,
            documents=documents,
            metadatas=metadatas,
        )

    def search_text(self, collection: str, text: str, k: int = 5) -> list:
        query = (
            self._document(text)
            if self.cloud_inference
            else _local_embedding_provider().embed_one(text)
        )
        return self.search(collection, query, k=k)

    def _collection_exists(self, collection: str) -> bool:
        if self.client is None:
            return False
        try:
            return bool(self.client.collection_exists(collection_name=collection))
        except Exception:
            return False

    def _ensure_collection(self, collection: str,
                           vector_size: int | None = None) -> bool:
        if self.client is None:
            return False
        if self._collection_exists(collection):
            return True
        size = self.vector_dimension if self.cloud_inference else vector_size
        if not size:
            return False
        metadata = self._pending_collection_metadata.get(collection) or {}
        try:
            self.client.create_collection(
                collection_name=collection,
                vectors_config=qdrant_models.VectorParams(
                    size=int(size),
                    distance=qdrant_models.Distance.COSINE,
                ),
                metadata=metadata or None,
            )
            self._pending_collection_metadata.pop(collection, None)
            return True
        except Exception as error:
            logger.error('[vector-store] could not create Qdrant collection %s: %s',
                         collection, error)
            return False

    def _payload(self, id: str, text: str | None, metadata: dict | None) -> dict:
        return {
            'source_id': str(id),
            'document': text or '',
            'metadata': self._clean_metadata(metadata),
        }

    def upsert_embedding(self, collection: str, id: str, vector=None,
                         metadata: dict | None = None,
                         text: str | None = None) -> bool:
        if self.client is None:
            return False
        if self.cloud_inference:
            if text is None:
                return False
            vector = self._document(text)
        elif vector is None:
            if text is None:
                return False
            vector = _local_embedding_provider().embed_one(text)
        return self._upsert_many(
            collection,
            ids=[id],
            vectors=[vector],
            documents=[text or ''],
            metadatas=[metadata or {}],
        )

    def search(self, collection: str, vector, k: int = 5) -> list:
        if self.client is None or not self._collection_exists(collection):
            return []
        if self.cloud_inference and not isinstance(vector, qdrant_models.Document):
            logger.error('[vector-store] Qdrant Cloud search requires raw text inference')
            return []
        try:
            response = self.client.query_points(
                collection_name=collection,
                query=vector,
                limit=max(1, k),
                with_payload=True,
            )
            points = response.points
        except Exception as error:
            logger.error('[vector-store] Qdrant search failed for %s: %s',
                         collection, error)
            return []

        results = []
        for point in points:
            payload = point.payload or {}
            similarity = float(point.score) if point.score is not None else None
            results.append({
                'id': payload.get('source_id', str(point.id)),
                'text': payload.get('document', ''),
                'metadata': payload.get('metadata') or {},
                # Existing callers expect Chroma cosine distance (lower is
                # closer); Qdrant returns cosine similarity (higher is closer).
                'score': 1.0 - similarity if similarity is not None else None,
            })
        return results

    def _upsert_many(self, collection: str, ids: list, vectors: list,
                     documents: list | None = None,
                     metadatas: list | None = None) -> bool:
        if self.client is None or not ids or not vectors:
            return False
        vector_size = None if self.cloud_inference else len(vectors[0])
        if not self._ensure_collection(collection, vector_size):
            return False
        documents = documents or ['' for _ in ids]
        metadatas = metadatas or [{} for _ in ids]
        try:
            points = [
                qdrant_models.PointStruct(
                    id=self._point_id(id),
                    vector=vector,
                    payload=self._payload(
                        id,
                        documents[index] if index < len(documents) else '',
                        metadatas[index] if index < len(metadatas) else {},
                    ),
                )
                for index, (id, vector) in enumerate(zip(ids, vectors))
            ]
            self.client.upsert(
                collection_name=collection,
                points=points,
                wait=True,
            )
            return True
        except Exception as error:
            logger.error('[vector-store] Qdrant batch add failed for %s: %s',
                         collection, error)
            return False

    def add_many(self, collection: str, ids: list, vectors: list,
                 documents: list | None = None,
                 metadatas: list | None = None) -> bool:
        if self.cloud_inference:
            if not documents:
                return False
            return self.add_texts(
                collection,
                ids=ids,
                texts=documents,
                documents=documents,
                metadatas=metadatas,
            )
        return self._upsert_many(
            collection,
            ids=ids,
            vectors=vectors,
            documents=documents,
            metadatas=metadatas,
        )

    def collection_metadata(self, collection: str) -> dict | None:
        if self.client is None:
            return None
        if collection in self._pending_collection_metadata:
            return dict(self._pending_collection_metadata[collection])
        try:
            info = self.client.get_collection(collection_name=collection)
            metadata = getattr(info, 'metadata', None)
            if metadata is None:
                metadata = getattr(getattr(info, 'config', None), 'metadata', None)
            return dict(metadata or {})
        except Exception:
            return None

    def reset_collection(self, collection: str, metadata: dict | None = None) -> bool:
        if self.client is None:
            return False
        try:
            if self._collection_exists(collection):
                self.client.delete_collection(collection_name=collection)
            self._pending_collection_metadata[collection] = dict(metadata or {})
            return True
        except Exception as error:
            logger.error('[vector-store] Qdrant reset failed for %s: %s',
                         collection, error)
            return False

    def delete(self, collection: str, id: str) -> bool:
        if self.client is None or not self._collection_exists(collection):
            return False
        try:
            self.client.delete(
                collection_name=collection,
                points_selector=qdrant_models.PointIdsList(
                    points=[self._point_id(id)]),
                wait=True,
            )
            return True
        except Exception as error:
            logger.error('[vector-store] Qdrant delete failed for %s/%s: %s',
                         collection, id, error)
            return False

    def count(self, collection: str) -> int:
        if self.client is None or not self._collection_exists(collection):
            return 0
        try:
            return int(self.client.count(
                collection_name=collection, exact=True).count)
        except Exception:
            return 0

    def collections(self) -> list:
        if self.client is None:
            return []
        try:
            return [item.name for item in self.client.get_collections().collections]
        except Exception:
            return []


def create_vector_store(config: dict | None = None) -> VectorStoreInterface:
    """Use Qdrant for complete credentials, otherwise fall back to Chroma."""
    config = config if config is not None else settings.get('vector_store', {})
    url = config.get('qdrant_url')
    api_key = config.get('qdrant_api_key')
    if _valid_qdrant_config(url, api_key):
        return QdrantVectorStore(
            url=url,
            api_key=api_key,
            timeout=config.get('qdrant_timeout_seconds', 10.0),
            cloud_inference=config.get('qdrant_cloud_inference', True),
            embedding_model=config.get('embedding_model', DEFAULT_QDRANT_MODEL),
            vector_dimension=config.get(
                'vector_dimension', DEFAULT_QDRANT_DIMENSION),
        )
    return ChromaVectorStore(path=config.get('path'))


# Backward compatibility: explicit VectorStore(...) construction remains the
# existing local Chroma implementation. The shared app instance is automatic.
VectorStore = ChromaVectorStore
vector_store = create_vector_store()
