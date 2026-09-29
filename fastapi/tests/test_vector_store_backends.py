"""Backend-independent vector-store and Qdrant Cloud Inference tests."""

import math
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import app.vector_store as vector_store_module
from app.vector_store import (
    ChromaVectorStore,
    QdrantVectorStore,
    VectorStoreInterface,
    create_vector_store,
)

MODEL = 'sentence-transformers/all-MiniLM-L6-v2'


class FakeModels:
    class Distance:
        COSINE = 'cosine'

    class VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class Document:
        def __init__(self, text, model):
            self.text = text
            self.model = model

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    class PointIdsList:
        def __init__(self, points):
            self.points = points


class FakeQdrantClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.data = {}
        self.last_query = None

    def collection_exists(self, collection_name):
        return collection_name in self.data

    def create_collection(self, collection_name, vectors_config, metadata=None):
        self.data[collection_name] = {
            'vectors_config': vectors_config,
            'metadata': metadata or {},
            'points': {},
        }

    def delete_collection(self, collection_name):
        del self.data[collection_name]

    def upsert(self, collection_name, points, wait):
        assert wait is True
        for point in points:
            self.data[collection_name]['points'][point.id] = point

    def query_points(self, collection_name, query, limit, with_payload):
        assert with_payload is True
        self.last_query = query
        stored = list(self.data[collection_name]['points'].values())

        if isinstance(query, FakeModels.Document):
            matches = [
                SimpleNamespace(
                    id=point.id,
                    payload=point.payload,
                    score=0.9 - (index * 0.1),
                )
                for index, point in enumerate(stored)
            ]
        else:
            def cosine(vector):
                denominator = math.sqrt(sum(item * item for item in query))
                denominator *= math.sqrt(sum(item * item for item in vector))
                return sum(
                    left * right for left, right in zip(query, vector)
                ) / denominator

            matches = [
                SimpleNamespace(
                    id=point.id,
                    payload=point.payload,
                    score=cosine(point.vector),
                )
                for point in stored
            ]
            matches.sort(key=lambda point: point.score, reverse=True)
        return SimpleNamespace(points=matches[:limit])

    def get_collection(self, collection_name):
        return SimpleNamespace(metadata=self.data[collection_name]['metadata'])

    def get_collections(self):
        return SimpleNamespace(collections=[
            SimpleNamespace(name=name) for name in self.data
        ])

    def count(self, collection_name, exact):
        assert exact is True
        return SimpleNamespace(count=len(self.data[collection_name]['points']))

    def delete(self, collection_name, points_selector, wait):
        assert wait is True
        for point_id in points_selector.points:
            self.data[collection_name]['points'].pop(point_id, None)


def _install_fake_client(monkeypatch):
    client = FakeQdrantClient()

    def build_client(**options):
        client.options = options
        return client

    monkeypatch.setattr(vector_store_module, 'qdrant_models', FakeModels)
    monkeypatch.setattr(vector_store_module, 'QdrantClient', build_client)
    return client


@pytest.fixture
def self_hosted_store(monkeypatch):
    client = _install_fake_client(monkeypatch)
    store = QdrantVectorStore(
        url='http://127.0.0.1:6333',
        api_key='local-test-key',
        cloud_inference=False,
        embedding_model=MODEL,
        vector_dimension=384,
    )
    return store, client


@pytest.fixture
def cloud_store(monkeypatch):
    client = _install_fake_client(monkeypatch)

    def local_embedding_forbidden():
        raise AssertionError('local embedding provider used in Qdrant Cloud mode')

    monkeypatch.setattr(
        vector_store_module,
        '_local_embedding_provider',
        local_embedding_forbidden,
    )
    store = QdrantVectorStore(
        url='https://example.qdrant.io',
        api_key='cloud-test-key',
        cloud_inference=True,
        embedding_model=MODEL,
        vector_dimension=384,
    )
    return store, client


@pytest.mark.parametrize('config', [
    {'path': '', 'qdrant_url': '', 'qdrant_api_key': ''},
    {'path': '', 'qdrant_url': 'http://qdrant.example', 'qdrant_api_key': ''},
    {'path': '', 'qdrant_url': 'not-a-url', 'qdrant_api_key': 'test-api-key'},
    {'path': '', 'qdrant_url': 'http://user:password@qdrant.example',
     'qdrant_api_key': 'test-api-key'},
])
def test_factory_falls_back_to_chroma_without_complete_valid_qdrant(config):
    assert isinstance(create_vector_store(config), ChromaVectorStore)


def test_factory_selects_qdrant_cloud_for_complete_credentials(monkeypatch):
    client = _install_fake_client(monkeypatch)

    store = create_vector_store({
        'path': '',
        'qdrant_url': 'https://example.qdrant.io',
        'qdrant_api_key': 'test-api-key',
        'qdrant_timeout_seconds': 3,
        'qdrant_cloud_inference': True,
        'embedding_model': MODEL,
        'vector_dimension': 384,
    })

    assert isinstance(store, QdrantVectorStore)
    assert isinstance(store, VectorStoreInterface)
    assert store.client is client
    assert client.options['cloud_inference'] is True
    assert client.options['check_compatibility'] is False
    assert store.embedding_name == MODEL
    assert store.vector_dimension == 384


def test_qdrant_cloud_never_calls_local_embedding_provider(cloud_store):
    store, _ = cloud_store

    assert store.embedding_name == MODEL
    assert store.semantic_embeddings is True
    document = store.embed_text('raw text')
    assert isinstance(document, FakeModels.Document)
    assert document.text == 'raw text'
    assert document.model == MODEL


def test_qdrant_cloud_upsert_sends_raw_text_and_model(cloud_store):
    store, client = cloud_store

    assert store.reset_collection('knowledge', {'fingerprint': 'cloud'})
    assert store.add_texts(
        'knowledge',
        ids=['bobbie-p1-c1'],
        texts=['raw chunk body'],
        # This is retained for Chroma compatibility but must be ignored by
        # Cloud Inference.
        embedding_texts=['Heading\nraw chunk body'],
        documents=['raw chunk body'],
        metadatas=[{'page': 1}],
    )

    collection = client.data['knowledge']
    assert collection['vectors_config'].size == 384
    assert collection['vectors_config'].distance == FakeModels.Distance.COSINE
    point = next(iter(collection['points'].values()))
    assert isinstance(point.vector, FakeModels.Document)
    assert point.vector.text == 'raw chunk body'
    assert point.vector.model == MODEL
    assert point.payload['document'] == 'raw chunk body'
    assert point.payload['metadata'] == {'page': 1}


def test_qdrant_cloud_search_sends_raw_query_and_model(cloud_store):
    store, client = cloud_store
    assert store.upsert_text(
        'knowledge', 'a', 'raw chunk body', metadata={'page': 1})

    hits = store.search_text('knowledge', 'raw seller question', k=1)

    assert isinstance(client.last_query, FakeModels.Document)
    assert client.last_query.text == 'raw seller question'
    assert client.last_query.model == MODEL
    assert hits[0]['id'] == 'a'
    assert hits[0]['text'] == 'raw chunk body'
    assert hits[0]['score'] == pytest.approx(0.1)


def test_self_hosted_qdrant_vector_contract_is_unchanged(self_hosted_store):
    store, client = self_hosted_store

    assert store.reset_collection('knowledge', {'fingerprint': 'abc'}) is True
    assert store.add_many(
        'knowledge',
        ids=['bobbie-p1-c1', 'bobbie-p2-c2'],
        vectors=[[1.0, 0.0], [0.0, 1.0]],
        documents=['first document', 'second document'],
        metadatas=[
            {'page': 1, 'nested': {'ignored': True}},
            {'page': 2},
        ],
    ) is True

    assert client.options['cloud_inference'] is False
    assert client.data['knowledge']['vectors_config'].size == 2
    assert store.collection_metadata('knowledge') == {'fingerprint': 'abc'}
    assert store.count('knowledge') == 2

    hits = store.search('knowledge', [1.0, 0.0], k=2)
    assert [hit['id'] for hit in hits] == ['bobbie-p1-c1', 'bobbie-p2-c2']
    assert hits[0]['metadata'] == {'page': 1}
    assert hits[0]['score'] == pytest.approx(0.0)

    assert store.delete('knowledge', 'bobbie-p1-c1') is True
    assert store.count('knowledge') == 1


def test_chroma_text_operations_keep_local_embedding_provider(
        tmp_path, monkeypatch):
    class LocalProvider:
        name = 'all-MiniLM-L6-v2'
        is_semantic = True

        def __init__(self):
            self.batches = []
            self.queries = []

        def embed(self, texts):
            self.batches.append(texts)
            return [[1.0, 0.0] for _ in texts]

        def embed_one(self, text):
            self.queries.append(text)
            return [1.0, 0.0]

    provider = LocalProvider()
    monkeypatch.setattr(
        vector_store_module, '_local_embedding_provider', lambda: provider)
    store = ChromaVectorStore(path=str(tmp_path / 'chroma'))

    assert store.add_texts(
        'knowledge',
        ids=['a'],
        texts=['raw chunk body'],
        embedding_texts=['Heading\nraw chunk body'],
        documents=['raw chunk body'],
        metadatas=[{'page': 1}],
    )
    hits = store.search_text('knowledge', 'seller question', k=1)

    assert provider.batches == [['Heading\nraw chunk body']]
    assert provider.queries == ['seller question']
    assert hits[0]['text'] == 'raw chunk body'


def test_qdrant_adapter_matches_real_client_local_api():
    qdrant_client = pytest.importorskip('qdrant_client')
    store = object.__new__(QdrantVectorStore)
    store.url = ':memory:'
    store.api_key = ''
    store.timeout = 10.0
    store.cloud_inference = False
    store.embedding_model = MODEL
    store.vector_dimension = 384
    store.path = None
    store.client = qdrant_client.QdrantClient(':memory:')
    store._pending_collection_metadata = {}

    assert store.reset_collection('sdk_contract', {'fingerprint': 'real-sdk'})
    assert store.add_many(
        'sdk_contract',
        ids=['arbitrary-string-id'],
        vectors=[[1.0, 0.0]],
        documents=['real payload'],
        metadatas=[{'page': 7}],
    )
    assert store.collection_metadata('sdk_contract') == {'fingerprint': 'real-sdk'}
    assert store.search('sdk_contract', [1.0, 0.0], 1)[0]['id'] == (
        'arbitrary-string-id')
    assert store.delete('sdk_contract', 'arbitrary-string-id')


def test_importing_main_in_cloud_mode_does_not_load_local_embedding_stack():
    environment = os.environ.copy()
    environment.update({
        'QDRANT_URL': 'https://example.qdrant.io',
        'QDRANT_API_KEY': 'cloud-test-key',
        'QDRANT_CLOUD_INFERENCE': 'true',
        'EMBEDDING_MODEL': MODEL,
        'VECTOR_DIMENSION': '384',
    })
    script = """
import sys
import app.main
from app.vector_store import vector_store

assert vector_store.backend == 'qdrant'
assert vector_store.cloud_inference is True
for forbidden in ('chromadb', 'onnxruntime', 'fastembed', 'app.sms.embeddings'):
    assert not any(
        name == forbidden or name.startswith(forbidden + '.')
        for name in sys.modules
    ), (forbidden, [name for name in sys.modules if name.startswith(forbidden)])
"""
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_sdk_accepts_document_vectors_for_cloud_inference():
    qdrant_client = pytest.importorskip('qdrant_client')
    models = qdrant_client.models
    document = models.Document(text='raw chunk', model=MODEL)
    point = models.PointStruct(
        id='b90d8dbd-f90f-4c90-b4f7-d9ff3c7ce86e',
        vector=document,
        payload={'document': 'raw chunk'},
    )

    assert point.vector.text == 'raw chunk'
    assert point.vector.model == MODEL
    params = models.VectorParams(size=384, distance=models.Distance.COSINE)
    assert params.size == 384
    assert params.distance == models.Distance.COSINE
