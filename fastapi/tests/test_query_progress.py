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


class CountingRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.sets = 0
        self.gets = 0

    def set(self, key, value, ex=None):
        self.sets += 1
        super().set(key, value, ex)

    def get(self, key):
        self.gets += 1
        return super().get(key)


def test_per_row_progress_is_throttled_but_ends_exact(monkeypatch):
    redis = CountingRedis()
    monkeypatch.setattr(query_progress, '_client', lambda: redis)
    job = query_progress.create(17, 'lead-pool')
    redis.sets = redis.gets = 0

    for loaded in range(0, 1001):          # one tick per row, as the loaders do
        query_progress.update(job['job_id'], total=1000, loaded=loaded)

    # Fast rows: just the 0% and 100% frames — not 1,001 round trips, no reads.
    assert redis.sets <= 3
    assert redis.gets == 0
    final = query_progress.read(job['job_id'])
    assert (final['loaded'], final['percent'], final['remaining']) == (1000, 100, 0)


def test_complete_and_fail_always_write_and_release_memory(monkeypatch):
    redis = CountingRedis()
    monkeypatch.setattr(query_progress, '_client', lambda: redis)
    done = query_progress.create(1, 'followups')
    query_progress.update(done['job_id'], total=4, loaded=2)
    query_progress.complete(done['job_id'], [1, 2, 3, 4], total=4)
    assert query_progress.read(done['job_id'])['result'] == [1, 2, 3, 4]

    broken = query_progress.create(1, 'followups')
    query_progress.fail(broken['job_id'], RuntimeError('boom'))
    assert query_progress.read(broken['job_id'])['status'] == 'failed'
    assert done['job_id'] not in query_progress._local_jobs
    assert broken['job_id'] not in query_progress._local_jobs


def test_a_job_made_by_another_process_is_read_once(monkeypatch):
    redis = CountingRedis()
    monkeypatch.setattr(query_progress, '_client', lambda: redis)
    job = query_progress.create(5, 'campaigns')
    query_progress._forget(job['job_id'])   # as if created elsewhere
    redis.gets = 0
    for loaded in range(5):
        query_progress.update(job['job_id'], total=4, loaded=loaded)
    assert redis.gets == 1


def test_slow_work_still_shows_intermediate_progress(monkeypatch):
    redis = CountingRedis()
    monkeypatch.setattr(query_progress, '_client', lambda: redis)
    clock = iter(i * 0.3 for i in range(100))         # each row takes 300 ms
    monkeypatch.setattr(query_progress.time, 'monotonic', lambda: next(clock))
    job = query_progress.create(1, 'lead-pool')
    redis.sets = 0
    for loaded in range(0, 6):
        query_progress.update(job['job_id'], total=5, loaded=loaded)
    assert redis.sets >= 4      # visible movement while slow rows load
