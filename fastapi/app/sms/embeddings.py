"""Text embeddings for retrieval.

Uses the MiniLM ONNX model that ships with chromadb (all-MiniLM-L6-v2, 384-dim,
CPU only, no API cost). The model is downloaded once on first use and cached.

If the model cannot be loaded the provider degrades to a deterministic hash
"embedding" so ingestion and storage still work — but that carries no semantic
meaning, so callers should check `is_semantic` before relying on similarity.
"""

import hashlib
import threading

from ..core.config import settings
from ..logger import get_logger

logger = get_logger(__name__)

HASH_DIMENSION = 128


class EmbeddingProvider:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings['ai'].get('embedding_model') or 'chroma-minilm-l6-v2'
        self._function = None
        self._loaded = False
        self._lock = threading.Lock()

    # -- model -------------------------------------------------------------
    def _load(self):
        if self._loaded:
            return self._function
        with self._lock:
            if self._loaded:
                return self._function
            self._loaded = True
            try:
                from chromadb.utils import embedding_functions

                self._function = embedding_functions.DefaultEmbeddingFunction()
                # Touch the model once so a failure surfaces here, not mid-request.
                self._function(['warmup'])
                logger.info('[embeddings] loaded %s', self.model_name)
            except Exception as error:
                self._function = None
                logger.error('[embeddings] could not load %s (%s); '
                             'falling back to non-semantic hash vectors', self.model_name, error)
            return self._function

    @property
    def is_semantic(self) -> bool:
        """True when a real model is in use (as opposed to the hash fallback)."""
        return self._load() is not None

    @property
    def name(self) -> str:
        return self.model_name if self.is_semantic else 'hash-fallback'

    @property
    def dimension(self) -> int:
        return len(self.embed(['dimension probe'])[0])

    # -- embedding ---------------------------------------------------------
    def embed(self, texts: list) -> list:
        cleaned = [str(text or '') for text in texts]
        function = self._load()
        if function is None:
            return [self._hash_vector(text) for text in cleaned]
        try:
            return [list(vector) for vector in function(cleaned)]
        except Exception as error:
            logger.error('[embeddings] embedding failed (%s); using hash fallback', error)
            return [self._hash_vector(text) for text in cleaned]

    def embed_one(self, text: str) -> list:
        return self.embed([text])[0]

    @staticmethod
    def _hash_vector(text: str, size: int = HASH_DIMENSION) -> list:
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        vector = [float(byte) / 255.0 for byte in digest]
        while len(vector) < size:
            vector.extend(vector)
        return vector[:size]


embedding_provider = EmbeddingProvider()
