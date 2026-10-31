"""Backing stores: the ChromaDB vector store and path resolution.

The vector tests skip when chromadb is not installed; the client degrades to a
no-op in that case rather than failing requests.
"""

import pytest

from app.core.config import resolve_path, settings
from app.vector_store import VectorStore

pytest.importorskip('chromadb', reason='chromadb not installed; vector store disabled')


@pytest.fixture
def store(tmp_path):
    """A VectorStore backed by an empty temporary directory."""
    return VectorStore(path=str(tmp_path / 'chroma'))


def test_empty_store_is_usable_immediately(store):
    """The first write must create the collection, not silently no-op.

    The previous LanceDB client failed exactly here: its connection object
    defined __len__, so an empty store was falsy and the first write was skipped.
    """
    assert store.available
    assert store.upsert_embedding('bobbie', 'first', text='the very first document') is True
    assert store.count('bobbie') == 1


def test_documents_round_trip_with_metadata(store):
    store.upsert_embedding('bobbie', 'a', text='Commission depends on a written agreement.',
                           metadata={'source': 'fees'})
    store.upsert_embedding('bobbie', 'b', text='Bobbie Fisher is a managing broker.',
                           metadata={'source': 'profile'})

    hits = store.search('bobbie', store.embed_text('Commission depends on a written agreement.'), k=2)
    assert len(hits) == 2
    top = hits[0]
    assert top['id'] == 'a'
    assert top['metadata']['source'] == 'fees'
    assert 'Commission' in top['text']
    assert top['score'] is not None


def test_reindexing_updates_instead_of_duplicating(store):
    store.upsert_embedding('bobbie', 'a', text='first version', metadata={'v': 1})
    store.upsert_embedding('bobbie', 'a', text='second version', metadata={'v': 2})

    assert store.count('bobbie') == 1
    hit = store.search('bobbie', store.embed_text('second version'), k=1)[0]
    assert hit['text'] == 'second version'
    assert hit['metadata']['v'] == 2


def test_metadata_is_sanitised_for_chroma(store):
    """Chroma rejects empty metadata and non-scalar values."""
    assert store.upsert_embedding('bobbie', 'a', text='doc', metadata={}) is True
    assert store.upsert_embedding('bobbie', 'b', text='doc',
                                  metadata={'ok': 'yes', 'nested': {'no': True}}) is True
    hit = store.search('bobbie', store.embed_text('doc'), k=2)[0]
    assert 'nested' not in hit['metadata']


def test_delete_removes_a_document(store):
    store.upsert_embedding('bobbie', 'a', text='doc a')
    store.upsert_embedding('bobbie', 'b', text='doc b')
    assert store.delete('bobbie', 'a') is True
    assert store.count('bobbie') == 1


def test_search_on_a_missing_collection_is_empty_not_an_error(store):
    assert store.search('never-created', store.embed_text('anything')) == []
    assert store.count('never-created') == 0


def test_data_persists_across_clients(tmp_path):
    path = str(tmp_path / 'chroma')
    VectorStore(path=path).upsert_embedding('bobbie', 'a', text='persisted document')
    # A fresh client on the same directory sees the data.
    assert VectorStore(path=path).count('bobbie') == 1


def test_client_degrades_when_the_path_is_unusable():
    client = VectorStore(path='')
    assert client.available is False
    assert client.upsert_embedding('bobbie', 'x', text='doc') is False
    assert client.search('bobbie', [0.0] * 8) == []
    assert client.collections() == []


def test_rag_service_is_read_only_and_owner_scoped(tmp_path, monkeypatch):
    """The legacy RAG facade can no longer write ownerless points; reads go through
    the owner/brokerage/version-scoped agent search."""
    from app import rag as rag_module
    from app.sms.runtime_context import AgentRuntimeContext

    rag = rag_module.RagService()
    assert not hasattr(rag, 'index_document')
    calls = []
    monkeypatch.setattr(rag_module, 'search_agent_knowledge',
                        lambda query, context, limit=4: calls.append((query, context, limit)) or {
                            'matches': [{'text': 'Commission depends on a written agreement.',
                                         'owner_user_id': context.user_id}]})
    context = AgentRuntimeContext(user_id=7, brokerage_id='b1', conversation_id=3, active_persona_id=1,
                                  active_persona_version_id=2, agent_name='Agent', brokerage_name='', persona=None)
    assert rag.prompt_context('commission agreement', context)[0]['owner_user_id'] == 7
    assert calls == [('commission agreement', context, 5)]


def test_relative_paths_resolve_against_the_backend_directory():
    """The app must behave identically whatever directory it is started from."""
    resolved = resolve_path('data/chroma')
    assert resolved.startswith('/') and resolved.endswith('/data/chroma')
    assert resolve_path('/var/lib/chroma') == '/var/lib/chroma'


def test_configured_stores_are_absolute_paths():
    assert settings['vector_store']['path'].startswith('/')
    assert settings['sms']['knowledge_pdf'].startswith('/')
    # A plain boolean keeps pytest's assertion detail from printing the URL,
    # which can carry a password.
    plain_redis = settings['redis_url'].startswith('redis://')
    assert plain_redis, 'REDIS_URL must use redis://'
