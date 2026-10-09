from app import query_progress


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = (value, ex)

    def get(self, key):
        stored = self.values.get(key)
        return stored[0] if stored else None


def test_query_progress_tracks_exact_counts_and_result(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(query_progress, '_client', lambda: redis)

    job = query_progress.create(17, 'campaigns')
    query_progress.update(job['job_id'], total=10, loaded=4)

    processing = query_progress.read(job['job_id'])
    assert processing['status'] == 'processing'
    assert processing['loaded'] == 4
    assert processing['remaining'] == 6
    assert processing['percent'] == 40

    query_progress.complete(job['job_id'], [{'id': 1}], total=10)
    completed = query_progress.read(job['job_id'])
    assert completed['status'] == 'completed'
    assert completed['loaded'] == 10
    assert completed['remaining'] == 0
    assert completed['percent'] == 100
    assert completed['result'] == [{'id': 1}]
    assert redis.values[query_progress._key(job['job_id'])][1] == query_progress.JOB_TTL_SECONDS
