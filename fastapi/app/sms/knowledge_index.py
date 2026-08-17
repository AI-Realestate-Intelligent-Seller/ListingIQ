"""Bobbie's approved-knowledge retrieval (RAG).

Pipeline: PyMuPDF extracts the PDF page by page → text is chunked with overlap →
MiniLM embeddings are stored in ChromaDB → queries are answered by vector
similarity.

Ingestion is idempotent: the PDF's SHA-256 and the embedding model name are
stored on the collection, and re-indexing only happens when either changes.

If Chroma or the embedding model is unavailable the index falls back to the
lexical TF-IDF scorer over the same chunks, so Bobbie keeps working (with worse
recall) instead of losing her knowledge base entirely.
"""

import hashlib
import math
import re
import threading
from collections import Counter
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional dependency
    fitz = None

from ..core.config import settings
from ..logger import get_logger
from ..vector_store import vector_store
from .embeddings import embedding_provider

logger = get_logger(__name__)

RAG_STOP_WORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'was', 'were', 'you', 'your',
    'our', 'have', 'has', 'had', 'will', 'would', 'can', 'could', 'should', 'they', 'them',
    'their', 'there', 'these', 'those', 'what', 'when', 'where', 'which', 'who', 'how', 'why',
    'not', 'but', 'all', 'any', 'may', 'about', 'into', 'over', 'than', 'then', 'been', 'being',
}

DOCUMENT_STATUS = ('Draft for verification and approval; preserve cautions and do not upgrade '
                   'unverified claims.')

# A heading is a short line without terminal punctuation — used to give each
# chunk a little context about where in the document it came from.
HEADING = re.compile(r'^[A-Z][A-Za-z0-9 ,&/\'’()-]{2,70}$')


class KnowledgeUnavailableError(RuntimeError):
    """The knowledge document could not be read."""


class BobbieKnowledgeIndex:
    def __init__(self, pdf_path: str | Path | None = None, collection: str | None = None):
        self._pdf_path = Path(pdf_path) if pdf_path else None
        self._collection = collection
        self.chunks: list | None = None
        self.document_frequency: Counter = Counter()
        self._indexed_hash: str | None = None
        self.lock = threading.RLock()

    @property
    def pdf_path(self) -> Path:
        return self._pdf_path or Path(settings['sms']['knowledge_pdf'])

    @property
    def collection(self) -> str:
        return self._collection or settings['sms']['knowledge_collection']

    # -- extraction --------------------------------------------------------
    def _document_hash(self) -> str:
        try:
            return hashlib.sha256(self.pdf_path.read_bytes()).hexdigest()
        except OSError as error:
            raise KnowledgeUnavailableError(
                f'Bobbie knowledge PDF not found or unreadable: {self.pdf_path}') from error

    def extract_pages(self) -> list:
        """Layout blocks per page via PyMuPDF, with running headers/footers removed.

        Blocks are a far better paragraph proxy than raw text: `get_text('text')`
        separates lines with single newlines, so a whole page reads as one
        paragraph and never splits.
        """
        path = self.pdf_path
        if not path.is_file():
            raise KnowledgeUnavailableError(f'Bobbie knowledge PDF not found: {path}')
        if fitz is None:
            raise KnowledgeUnavailableError('PyMuPDF (pymupdf) is not installed')

        pages = []
        with fitz.open(path) as document:
            page_count = document.page_count
            for number, page in enumerate(document, start=1):
                blocks = []
                for block in sorted(page.get_text('blocks'), key=lambda item: (round(item[1], 1), item[0])):
                    # (x0, y0, x1, y1, text, block_no, block_type); type 0 is text.
                    if len(block) > 6 and block[6] != 0:
                        continue
                    text = self._normalize(str(block[4] or ''))
                    if text:
                        blocks.append(text)
                if blocks:
                    pages.append({'page': number, 'blocks': blocks})

        if not pages:
            raise KnowledgeUnavailableError(f'No extractable text in {path.name}')
        return self._strip_boilerplate(pages, page_count)

    @staticmethod
    def _normalize(text: str) -> str:
        """Clean a PDF text block for embedding.

        Symbol fonts encode bullets in the Unicode private-use area (Wingdings
        '' and friends); left as-is they end up embedded as noise and make
        chunks start with an unreadable glyph.
        """
        text = re.sub(r'[]', '• ', text)
        text = re.sub(r'[-]', '', text)
        text = text.replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        # Join wrapped lines, but keep breaks before bullets and numbered items.
        text = re.sub(r'\n(?![\n•]|\s*\d+[.)])', ' ', text)
        return re.sub(r' *\n *', '\n', text).strip()

    @staticmethod
    def _strip_boilerplate(pages: list, page_count: int) -> list:
        """Drop blocks repeated on most pages (running header, footer, page numbers).

        Left in, they dominate every embedding and make each chunk look alike.
        """
        counts = Counter(block for page in pages for block in set(page['blocks']))
        threshold = max(2, int(page_count * 0.5))
        boilerplate = {block for block, count in counts.items()
                       if count >= threshold and len(block) < 200}
        if boilerplate:
            logger.info('[bobbie-rag] removing %s repeated header/footer block(s)', len(boilerplate))
        cleaned = []
        for page in pages:
            blocks = [block for block in page['blocks']
                      if block not in boilerplate and not re.fullmatch(r'\d{1,3}', block)]
            if blocks:
                cleaned.append({'page': page['page'], 'blocks': blocks})
        return cleaned

    def build_chunks(self) -> list:
        """Block-aware chunks with overlap, tagged with page and nearest heading."""
        size = settings['sms']['knowledge_chunk_chars']
        overlap = settings['sms']['knowledge_chunk_overlap']
        chunks = []

        for page in self.extract_pages():
            heading = ''
            buffer = ''
            for block in page['blocks']:
                first_line = block.split('\n', 1)[0].strip()
                if len(block) <= 80 and HEADING.match(first_line):
                    heading = first_line
                # Oversized single blocks (tables, long prose) are split on
                # sentence boundaries so no chunk exceeds the target size.
                for piece in self._split_oversized(block, size):
                    if buffer and len(buffer) + len(piece) + 2 > size:
                        chunks.append({'page': page['page'], 'heading': heading, 'text': buffer})
                        # Carry the tail of the previous chunk so answers that
                        # straddle a boundary stay retrievable, cut on a word
                        # boundary so chunks never start mid-word.
                        buffer = self._tail(buffer, overlap)
                    buffer = f'{buffer}\n\n{piece}'.strip() if buffer else piece
            if buffer:
                chunks.append({'page': page['page'], 'heading': heading, 'text': buffer})

        for index, chunk in enumerate(chunks, start=1):
            chunk['id'] = f"bobbie-p{chunk['page']}-c{index}"
            chunk['tokens'] = self._tokens(chunk['text'])
        return chunks

    @staticmethod
    def _tail(text: str, overlap: int) -> str:
        """Last `overlap` characters, trimmed forward to a word boundary."""
        if overlap <= 0 or len(text) <= overlap:
            return text if overlap else ''
        tail = text[-overlap:]
        space = tail.find(' ')
        return (tail[space + 1:] if space != -1 else tail).lstrip()

    @staticmethod
    def _split_oversized(block: str, size: int) -> list:
        if len(block) <= size:
            return [block]
        pieces, current = [], ''
        for sentence in re.split(r'(?<=[.!?])\s+', block):
            if current and len(current) + len(sentence) + 1 > size:
                pieces.append(current)
                current = ''
            current = f'{current} {sentence}'.strip()
        if current:
            pieces.append(current)
        return pieces

    @staticmethod
    def _tokens(value: str) -> list:
        return [token for token in re.findall(r"[a-z0-9'/-]+", str(value).lower())
                if len(token) > 2 and token not in RAG_STOP_WORDS]

    # -- indexing ----------------------------------------------------------
    def ensure_indexed(self, force: bool = False) -> dict:
        """Extract, embed and store. Skips work when nothing has changed."""
        with self.lock:
            document_hash = self._document_hash()
            if self.chunks is None or force or self._indexed_hash != document_hash:
                self.chunks = self.build_chunks()
                self.document_frequency = Counter()
                for chunk in self.chunks:
                    self.document_frequency.update(set(chunk['tokens']))
                self._indexed_hash = document_hash
                logger.info('[bobbie-rag] extracted %s chunks from %s', len(self.chunks), self.pdf_path.name)

            if not vector_store.available:
                return self._status('lexical', 'vector store unavailable')

            fingerprint = f'{document_hash}:{embedding_provider.name}'
            stored = vector_store.collection_metadata(self.collection) or {}
            if not force and stored.get('fingerprint') == fingerprint \
                    and vector_store.count(self.collection) == len(self.chunks):
                return self._status('semantic')

            logger.info('[bobbie-rag] embedding %s chunks with %s', len(self.chunks), embedding_provider.name)
            vector_store.reset_collection(self.collection, metadata={'fingerprint': fingerprint})
            batch = 32
            for start in range(0, len(self.chunks), batch):
                window = self.chunks[start:start + batch]
                # Embed the heading with the body: it carries the section topic
                # that the chunk text alone often lacks.
                vectors = embedding_provider.embed([self._embeddable(chunk) for chunk in window])
                vector_store.add_many(
                    self.collection,
                    ids=[chunk['id'] for chunk in window],
                    vectors=vectors,
                    documents=[chunk['text'] for chunk in window],
                    metadatas=[{'page': chunk['page'], 'heading': chunk['heading'] or 'unspecified'}
                               for chunk in window],
                )
            logger.info('[bobbie-rag] indexed %s chunks into %s', len(self.chunks), self.collection)
            return self._status('semantic')

    def _status(self, retrieval: str, reason: str = '') -> dict:
        status = {
            'retrieval': retrieval,
            'chunks': len(self.chunks or []),
            'document': self.pdf_path.name,
            'embedding_model': embedding_provider.name if retrieval == 'semantic' else None,
            'collection': self.collection if retrieval == 'semantic' else None,
        }
        if reason:
            status['reason'] = reason
        return status

    def stats(self) -> dict:
        return self.ensure_indexed()

    # -- search ------------------------------------------------------------
    @staticmethod
    def _embeddable(chunk: dict) -> str:
        heading = chunk.get('heading')
        return f"{heading}\n{chunk['text']}" if heading else chunk['text']

    def search(self, query: str, limit: int = 4) -> dict:
        """Hybrid retrieval: vector similarity fused with keyword scoring.

        Vector search alone misses exact terms (place names, product names);
        keyword search alone misses paraphrases. Fusing both is markedly better
        than either for the mix of questions owners actually ask.
        """
        limit = max(1, min(limit, 6))
        status = self.ensure_indexed()
        pool = max(limit * 3, 10)
        lexical = self._lexical_search(query, pool)

        if status['retrieval'] == 'semantic' and embedding_provider.is_semantic:
            semantic = self._semantic_search(query, pool)
            if semantic:
                return self._response(query, self._fuse(semantic, lexical, limit), 'hybrid')
            # An empty vector result means the store is empty or broken; the
            # lexical scorer is better than returning nothing.
            logger.warning('[bobbie-rag] semantic search returned nothing; using lexical fallback')
        return self._response(query, lexical[:limit], 'lexical')

    @staticmethod
    def _fuse(semantic: list, lexical: list, limit: int, k: int = 60) -> list:
        """Reciprocal rank fusion — rank-based, so the two score scales never
        need to be normalised against each other."""
        ranked = {}
        for weight, results in ((1.0, semantic), (0.8, lexical)):
            for position, match in enumerate(results, start=1):
                entry = ranked.setdefault(match['id'], {'match': match, 'rrf': 0.0, 'sources': []})
                entry['rrf'] += weight / (k + position)
                entry['sources'].append('semantic' if results is semantic else 'lexical')
                # Prefer the semantic copy of the record (it carries similarity).
                if results is semantic:
                    entry['match'] = match
        ordered = sorted(ranked.values(), key=lambda item: item['rrf'], reverse=True)
        fused = []
        for entry in ordered[:limit]:
            match = dict(entry['match'])
            match['matched_by'] = '+'.join(sorted(set(entry['sources'])))
            fused.append(match)
        return fused

    def _semantic_search(self, query: str, limit: int) -> list:
        vector = embedding_provider.embed_one(query)
        hits = vector_store.search(self.collection, vector, k=limit)
        matches = []
        for hit in hits:
            distance = hit.get('score')
            metadata = hit.get('metadata') or {}
            matches.append({
                'id': hit.get('id'),
                'page': metadata.get('page'),
                'heading': metadata.get('heading'),
                # Cosine distance in, similarity out: higher stays better, as
                # the previous lexical scores were.
                'score': round(1.0 - float(distance), 4) if distance is not None else None,
                'text': hit.get('text') or '',
            })
        return matches

    def _lexical_search(self, query: str, limit: int) -> list:
        query_tokens = self._tokens(query)
        scored = []
        for chunk in (self.chunks or []):
            counts = Counter(chunk['tokens'])
            score = 0.0
            for token in query_tokens:
                frequency = counts[token]
                if frequency:
                    score += (1 + math.log(frequency)) * math.log(
                        1 + len(self.chunks) / max(1, self.document_frequency[token]))
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {'id': chunk['id'], 'page': chunk['page'], 'heading': chunk['heading'],
             'score': round(score, 3), 'text': chunk['text']}
            for score, chunk in scored[:limit]
        ]

    def _response(self, query: str, matches: list, retrieval: str) -> dict:
        return {
            'query': query,
            'document': self.pdf_path.name,
            'document_status': DOCUMENT_STATUS,
            'retrieval': retrieval,
            # Only vector-backed modes involve the embedding model.
            'embedding_model': embedding_provider.name if retrieval in ('semantic', 'hybrid') else None,
            'matches': matches,
        }


bobbie_knowledge = BobbieKnowledgeIndex()
