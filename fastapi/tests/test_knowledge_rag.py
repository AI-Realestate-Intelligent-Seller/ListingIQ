"""Bobbie's knowledge retrieval: PyMuPDF extraction, chunking and hybrid RAG."""

import pytest

from app.core.config import settings
from app.sms.embeddings import EmbeddingProvider
from app.sms.knowledge_index import BobbieKnowledgeIndex, KnowledgeUnavailableError

pytest.importorskip('fitz', reason='pymupdf not installed')

BROKER_EMAIL = 'ather.shamim@linchpinglobal.net'


@pytest.fixture(scope='module')
def index():
    """One index for the module — extraction and embedding are expensive."""
    index = BobbieKnowledgeIndex(collection='test_bobbie_knowledge')
    index.ensure_indexed(force=True)
    return index


# --------------------------------------------------------------------------
# Extraction and chunking
# --------------------------------------------------------------------------

def test_pdf_is_extracted_page_by_page(index):
    pages = index.extract_pages()
    assert len(pages) > 5
    assert all(page['page'] >= 1 and page['blocks'] for page in pages)


def test_running_headers_are_stripped(index):
    """The repeated page header would otherwise dominate every embedding."""
    header = 'BOBBIE FISHER — INTERNAL PROFILE & AI KNOWLEDGE BASE'
    starts_with_header = [chunk for chunk in index.chunks if chunk['text'].startswith(header)]
    assert starts_with_header == []


def test_chunks_are_split_within_pages(index):
    """One chunk per page means the splitter is not working."""
    pages = {chunk['page'] for chunk in index.chunks}
    assert len(index.chunks) > len(pages) * 2


def test_chunks_respect_the_size_budget(index):
    budget = settings['sms']['knowledge_chunk_chars']
    oversized = [chunk for chunk in index.chunks if len(chunk['text']) > budget * 1.5]
    assert oversized == []


def test_chunks_do_not_start_mid_word(index):
    """Character-sliced overlap used to produce chunks starting like 'ducation'."""
    import re

    for chunk in index.chunks:
        first = chunk['text'].split()[0]
        assert re.match(r'^[A-Za-z0-9“"\'(•\-—\[]', first), f'chunk starts mid-word: {first!r}'


def test_chunk_ids_are_stable_and_page_tagged(index):
    ids = [chunk['id'] for chunk in index.chunks]
    assert len(ids) == len(set(ids))
    assert all(chunk['id'].startswith(f"bobbie-p{chunk['page']}-") for chunk in index.chunks)


def test_missing_pdf_reports_clearly():
    missing = BobbieKnowledgeIndex(pdf_path='/nonexistent/knowledge.pdf')
    with pytest.raises(KnowledgeUnavailableError, match='not found'):
        missing.extract_pages()


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------

def test_embeddings_are_semantic_not_hashes():
    provider = EmbeddingProvider()
    if not provider.is_semantic:
        pytest.skip('embedding model unavailable (no cached model / no network)')

    def cosine(left, right):
        dot = sum(a * b for a, b in zip(left, right))
        norm = (sum(a * a for a in left) ** 0.5) * (sum(b * b for b in right) ** 0.5)
        return dot / norm

    related = provider.embed(['What commission does Bobbie charge?',
                              'Bobbie charges a commission on the sale.'])
    unrelated = provider.embed(['What commission does Bobbie charge?',
                                'The weather in Antarctica is cold.'])
    assert cosine(*related) > cosine(*unrelated) + 0.3
    assert provider.dimension == 384


def test_hash_fallback_keeps_the_pipeline_working():
    provider = EmbeddingProvider()
    vector = provider._hash_vector('some text')
    assert len(vector) == 128
    assert provider._hash_vector('some text') == vector, 'must be deterministic'


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

def test_search_returns_the_documented_contract(index):
    result = index.search('what areas does Bobbie serve?', 3)
    assert result['document'].endswith('.pdf')
    assert result['retrieval'] in ('hybrid', 'lexical')
    assert 'do not upgrade unverified claims' in result['document_status']
    assert 1 <= len(result['matches']) <= 3
    for match in result['matches']:
        assert match['id'] and match['text']
        assert isinstance(match['page'], int)
        assert match['score'] is not None


def test_semantic_retrieval_finds_paraphrases(index):
    """The point of RAG over TF-IDF: no shared keywords needed."""
    if index.stats()['retrieval'] != 'semantic':
        pytest.skip('vector store unavailable; hybrid retrieval inactive')
    result = index.search('Which towns and neighbourhoods does she cover?', 4)
    assert result['retrieval'] == 'hybrid'
    joined = ' '.join(match['text'] for match in result['matches']).lower()
    assert any(town in joined for town in ('barrington', 'lake forest', 'north shore', 'chicago'))


def test_results_are_capped_at_six(index):
    assert len(index.search('bobbie', 50)['matches']) <= 6


def test_lexical_fallback_when_vectors_are_unavailable(monkeypatch, index):
    """Losing Chroma degrades recall; it must not lose Bobbie her knowledge."""
    from app.vector_store import VectorStore

    monkeypatch.setattr('app.sms.knowledge_index.vector_store', VectorStore(path=''))
    result = index.search('service areas', 2)
    assert result['retrieval'] == 'lexical'
    assert result['matches'], 'lexical fallback must still return something'
    assert result['embedding_model'] is None


def test_reindexing_is_skipped_when_nothing_changed(index):
    before = index.stats()
    after = index.ensure_indexed()
    assert after == before


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def test_knowledge_endpoints_require_authentication(client):
    assert client.get('/api/v1/sms/knowledge/search?q=bobbie').status_code == 401
    assert client.get('/api/v1/sms/knowledge/status').status_code == 401
    assert client.post('/api/v1/sms/knowledge/reindex').status_code == 401


def test_reindex_is_hob_only(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    assert client.post('/api/v1/sms/knowledge/reindex',
                       headers=auth_header(BROKER_EMAIL)).status_code == 403


def test_status_reports_the_active_retrieval_mode(client, make_user, auth_header):
    make_user(BROKER_EMAIL, role='broker')
    body = client.get('/api/v1/sms/knowledge/status', headers=auth_header(BROKER_EMAIL)).json()
    assert body['document'].endswith('.pdf')
    assert body['chunks'] > 0
    assert body['retrieval'] in ('semantic', 'lexical')


# --------------------------------------------------------------------------
# The gate that decides whether Bobbie consults the knowledge base
# --------------------------------------------------------------------------

@pytest.mark.parametrize('message', [
    'How long have you been selling homes and what areas do you cover?',
    'Who is Bobbie?',
    'What does RE/MAX charge?',
    'Do you have buyers?',
    'Is that negotiable?',
])
def test_questions_about_the_business_trigger_a_lookup(message):
    from app.sms.knowledge import needs_bobbie_knowledge

    assert needs_bobbie_knowledge(message) is True


@pytest.mark.parametrize('message', [
    'Yes.',
    'Tuesday at 10am works for me.',
    'Stop texting me.',
])
def test_plain_replies_do_not_trigger_a_lookup(message):
    from app.sms.knowledge import needs_bobbie_knowledge

    assert needs_bobbie_knowledge(message) is False
