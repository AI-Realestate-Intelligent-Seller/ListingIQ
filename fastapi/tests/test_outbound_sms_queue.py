"""Provider-free coverage for the durable campaign submission queue."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading
import time

import pytest
import requests

from app import db
from app.leads import campaign as campaign_service
from app.models import Campaign, Conversation, Lead, Message, OutboundSmsJob, User
from app.sms import outbound_worker
from app.sms.service import SmsDeliveryError
from app.sms import rate_limit, service as sms_service


def _user_and_campaign(session, count: int, suffix: str = ''):
    user = User(
        email=f'queue{suffix}@example.com', hashed_password='x', full_name='Queue Test',
        role='hob', brokerage_id=f'queue-{suffix}', is_active=True, is_verified=True,
    )
    session.add(user)
    session.flush()
    campaign = Campaign(
        user_id=user.id, name=f'Queue {suffix}',
        message_template='Hi {{owner_name}}, about {{address}}.', status='draft',
    )
    session.add(campaign)
    session.flush()
    leads = [Lead(
        user_id=user.id,
        owner_name=f'Owner {suffix}-{index}',
        phone=f'+1{2000000000 + index:010d}',
        property_address=f'{index} Queue Test Ave',
        campaign_id=campaign.id,
        dnc=False,
    ) for index in range(count)]
    session.bulk_save_objects(leads)
    session.commit()
    session.refresh(user)
    session.refresh(campaign)
    return user, campaign


@pytest.mark.parametrize('count', [500, 1000, 5000])
def test_complete_campaign_is_durably_queued_independent_of_preview_page(
    session, monkeypatch, count,
):
    user, campaign = _user_and_campaign(session, count, str(count))
    monkeypatch.setitem(campaign_service.settings['telnyx'], 'mode', 'simulation')
    result = campaign_service.send(session, user, campaign)

    assert result['queued'] == count
    assert result['started'] == []
    assert campaign.status == 'sending'
    jobs = (session.query(OutboundSmsJob)
            .filter(OutboundSmsJob.campaign_id == campaign.id)
            .order_by(OutboundSmsJob.id).all())
    assert len(jobs) == count
    assert len({job.lead_id for job in jobs}) == count
    assert len({job.job_key for job in jobs}) == count
    assert all(job.status == 'queued' for job in jobs)
    # Only the requested 20 rows are rendered for display; enqueueing above
    # still used every campaign member.
    assert len(campaign_service.preview(session, user, campaign, page=2)['recipients']) == 20

    # Drain the same durable rows with the real claim/state machine but mocked
    # provider and campaign side effects. This keeps the scale test free and
    # local while exercising queue processing, not just insertion.
    monkeypatch.setattr(outbound_worker, '_prepare_message', _mock_prepare)
    monkeypatch.setattr(
        outbound_worker.sms_service, 'send_sms',
        lambda _payload: {'data': {'id': f'mock-{time.monotonic_ns()}'}}
    )
    monkeypatch.setattr(outbound_worker.campaign_service, '_record_sent', lambda *args: {})
    monkeypatch.setattr(outbound_worker, 'finalize_campaign_if_complete', lambda _id: False)

    def drain(slot):
        while outbound_worker.process_submission_once(f'scale:{slot}'):
            pass

    with ThreadPoolExecutor(max_workers=outbound_worker.SMS_WORKER_COUNT) as pool:
        list(pool.map(drain, range(outbound_worker.SMS_WORKER_COUNT)))
    session.expire_all()
    assert (session.query(OutboundSmsJob)
            .filter(OutboundSmsJob.campaign_id == campaign.id,
                    OutboundSmsJob.status == 'accepted').count()) == count


def _bare_job(session, suffix='one', status='queued'):
    user, campaign = _user_and_campaign(session, 1, suffix)
    lead = session.query(Lead).filter(Lead.campaign_id == campaign.id).one()
    job = OutboundSmsJob(
        job_key=f'{suffix}:{lead.id}', campaign_id=campaign.id, lead_id=lead.id,
        user_id=user.id, to_number=lead.phone, property_key=lead.property_address,
        text='Test message', status=status,
    )
    session.add(job)
    campaign.status = 'sending'
    session.commit()
    return job


def _mock_prepare(session, job):
    lead = session.get(Lead, job.lead_id)
    return (SimpleNamespace(), lead, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(
        status='submission_queued', telnyx_id=None, failure_reason=None,
    ))


def test_ten_workers_process_simultaneous_campaign_jobs_without_duplicates(session, monkeypatch):
    jobs = []
    for index in range(40):
        jobs.append(_bare_job(session, f'parallel-{index}'))
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_send(_payload):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {'data': {'id': f'mock-{time.monotonic_ns()}'}}

    monkeypatch.setattr(outbound_worker, '_prepare_message', _mock_prepare)
    monkeypatch.setattr(outbound_worker.sms_service, 'send_sms', fake_send)
    monkeypatch.setattr(outbound_worker.campaign_service, '_record_sent', lambda *args: {})
    monkeypatch.setattr(outbound_worker, 'finalize_campaign_if_complete', lambda _id: False)

    def drain(slot):
        while outbound_worker.process_submission_once(f'test:{slot}'):
            pass

    with ThreadPoolExecutor(max_workers=outbound_worker.SMS_WORKER_COUNT) as pool:
        list(pool.map(drain, range(outbound_worker.SMS_WORKER_COUNT)))

    session.expire_all()
    assert outbound_worker.SMS_WORKER_COUNT == 10
    # SQLite serializes claim writes, but accepted sends still overlap and the
    # worker implementation never exceeds its fixed ten-thread pool.
    assert 1 <= peak <= 10
    assert session.query(OutboundSmsJob).filter(OutboundSmsJob.status == 'accepted').count() == 40
    assert session.query(OutboundSmsJob).filter(OutboundSmsJob.attempts != 1).count() == 0


def test_worker_process_starts_exactly_ten_submission_threads(monkeypatch):
    stop = threading.Event()
    release = threading.Event()
    workers = set()
    lock = threading.Lock()

    monkeypatch.setattr(outbound_worker, 'recover_stale_jobs', lambda _session: 0)
    monkeypatch.setattr(outbound_worker, 'process_lookup_once', lambda: False)

    def observe(worker_id):
        with lock:
            workers.add(worker_id)
            if len(workers) == 10:
                stop.set()
                release.set()
        release.wait(2)
        return False

    monkeypatch.setattr(outbound_worker, 'process_submission_once', observe)
    outbound_worker.run_forever(stop)
    assert len(workers) == 10


def test_explicit_429_retries_but_ambiguous_timeout_never_retries(session, monkeypatch):
    retry_job = _bare_job(session, 'retry')
    timeout_job = _bare_job(session, 'timeout')
    calls = {retry_job.id: 0, timeout_job.id: 0}

    monkeypatch.setattr(outbound_worker, '_prepare_message', _mock_prepare)
    monkeypatch.setattr(outbound_worker.campaign_service, '_record_sent', lambda *args: {})
    monkeypatch.setattr(outbound_worker, 'finalize_campaign_if_complete', lambda _id: False)

    def send_for_current(_payload):
        # Claim order is stable: retry job first, then timeout job.
        if calls[retry_job.id] == 0:
            calls[retry_job.id] += 1
            raise SmsDeliveryError('429', retryable=True, retry_after=0)
        if calls[timeout_job.id] == 0:
            calls[timeout_job.id] += 1
            raise SmsDeliveryError('timeout', ambiguous=True)
        return {'data': {'id': 'accepted-after-retry'}}

    monkeypatch.setattr(outbound_worker.sms_service, 'send_sms', send_for_current)
    outbound_worker.process_submission_once('test')
    session.expire_all()
    retry_job = session.get(OutboundSmsJob, retry_job.id)
    assert retry_job.status == 'retry_wait'
    # The retry waits; the second campaign's ready job is claimed next.
    outbound_worker.process_submission_once('test')
    session.expire_all()
    assert session.get(OutboundSmsJob, timeout_job.id).status == 'uncertain'
    # The explicit rejection is eligible and succeeds; the timeout stays terminal.
    retry_job = session.get(OutboundSmsJob, retry_job.id)
    retry_job.available_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()
    outbound_worker.process_submission_once('test')
    session.expire_all()
    assert session.get(OutboundSmsJob, retry_job.id).status == 'accepted'
    assert session.get(OutboundSmsJob, retry_job.id).attempts == 2
    assert session.get(OutboundSmsJob, timeout_job.id).attempts == 1


def test_restart_recovery_does_not_resubmit_in_flight_job(session, monkeypatch):
    job = _bare_job(session, 'stale', status='submitting')
    job.claimed_at = datetime.utcnow() - timedelta(hours=1)
    session.commit()
    monkeypatch.setitem(outbound_worker.settings['sms_submission'], 'stale_after_seconds', 30)

    assert outbound_worker.recover_stale_jobs(session) == 1
    session.refresh(job)
    assert job.status == 'uncertain'
    assert outbound_worker.process_submission_once('test') is False


def test_worker_preserves_message_attribution_and_finalizes_campaign(session, monkeypatch):
    job = _bare_job(session, 'lifecycle')
    monkeypatch.setitem(sms_service.settings['telnyx'], 'mode', 'simulation')
    monkeypatch.setattr(campaign_service, 'schedule_initial_followup', lambda *_: None)

    assert outbound_worker.process_submission_once('lifecycle-worker') is True
    session.expire_all()
    job = session.get(OutboundSmsJob, job.id)
    lead = session.get(Lead, job.lead_id)
    campaign = session.get(Campaign, job.campaign_id)
    message = session.get(Message, job.message_id)
    assert job.status == 'accepted'
    assert job.telnyx_id == message.telnyx_id
    assert message.campaign_id == campaign.id
    assert message.event_type == 'outreach.initial'
    assert message.status == 'queued'
    assert lead.conversation_id == message.conversation_id
    assert campaign.status == 'sent'
    assert campaign.sent_at is not None


def test_stop_received_while_queued_prevents_submission(session, monkeypatch):
    job = _bare_job(session, 'stopped')
    lead = session.get(Lead, job.lead_id)
    lead.dnc = True
    session.commit()
    monkeypatch.setattr(
        outbound_worker.sms_service, 'send_sms',
        lambda _payload: pytest.fail('DNC recipient reached the provider'),
    )

    assert outbound_worker.process_submission_once('stop-worker') is True
    session.expire_all()
    job = session.get(OutboundSmsJob, job.id)
    assert job.status == 'skipped'
    # Checked before the thread is prepared, so no outbound row is written.
    assert job.message_id is None
    assert 'do-not-contact' in job.last_error


def test_stop_on_owner_thread_while_queued_prevents_second_property(session, monkeypatch):
    """The owner's first property was texted; they replied STOP before the second sent.

    STOP marks the thread, not the queued lead, so the lead's own flag is
    still False when the worker reaches the second job.
    """
    job = _bare_job(session, 'thread-stop')
    lead = session.get(Lead, job.lead_id)
    thread = Conversation(
        contact=lead.phone, user_id=job.user_id, lead_status='dnc', dnc_alert=True,
        handled_by='broker', created_at=datetime.utcnow(),
    )
    session.add(thread)
    session.commit()
    assert lead.dnc is False
    monkeypatch.setattr(
        outbound_worker.sms_service, 'send_sms',
        lambda _payload: pytest.fail('Opted-out owner reached the provider'),
    )

    assert outbound_worker.process_submission_once('stop-worker') is True
    session.expire_all()
    job = session.get(OutboundSmsJob, job.id)
    thread = session.get(Conversation, thread.id)
    assert job.status == 'skipped'
    assert 'STOP' in job.last_error
    # The opted-out thread keeps its markers; preparing a send would reset them.
    assert thread.lead_status == 'dnc'
    assert thread.dnc_alert is True
    assert session.get(Campaign, job.campaign_id).status == 'sent'


def test_submission_thread_survives_unexpected_cycle_failure(monkeypatch):
    stop = threading.Event()
    calls = []

    def flaky(worker_id):
        calls.append(worker_id)
        if len(calls) == 1:
            raise RuntimeError('connection reset by Neon')
        stop.set()
        return False

    monkeypatch.setattr(outbound_worker, 'process_submission_once', flaky)
    monkeypatch.setattr(outbound_worker.threading.Event, 'wait', lambda self, _t=None: None)
    outbound_worker._submission_loop(0, stop)
    assert len(calls) == 2


def test_live_429_is_explicitly_retryable(monkeypatch):
    response = SimpleNamespace(
        ok=False, status_code=429, headers={'Retry-After': '2'},
        json=lambda: {'errors': [{'detail': 'rate limited'}]}, text='',
    )
    monkeypatch.setitem(sms_service.settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(sms_service.settings['telnyx'], 'api_key', 'test-only')
    monkeypatch.setattr(rate_limit, 'acquire_submission_slot', lambda: None)
    monkeypatch.setattr(sms_service.requests, 'post', lambda *args, **kwargs: response)

    with pytest.raises(SmsDeliveryError) as raised:
        sms_service.send_sms({'to': '+12000000000', 'text': 'mock'})
    assert raised.value.retryable is True
    assert raised.value.ambiguous is False
    assert raised.value.retry_after == 2


def test_live_timeout_is_ambiguous_and_not_retryable(monkeypatch):
    monkeypatch.setitem(sms_service.settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(sms_service.settings['telnyx'], 'api_key', 'test-only')
    monkeypatch.setattr(rate_limit, 'acquire_submission_slot', lambda: None)
    monkeypatch.setattr(
        sms_service.requests, 'post',
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout('mock timeout')),
    )

    with pytest.raises(SmsDeliveryError) as raised:
        sms_service.send_sms({'to': '+12000000000', 'text': 'mock'})
    assert raised.value.ambiguous is True
    assert raised.value.retryable is False


class _LeaseRedis:
    def __init__(self):
        self.value = None
        self.lock = threading.Lock()

    def set(self, _key, value, nx=False, ex=None):
        assert nx is True and ex == outbound_worker.SUPERVISOR_LEASE_SECONDS
        with self.lock:
            if self.value is not None:
                return False
            self.value = value
            return True

    def eval(self, script, _keys, _key, token, *args):
        with self.lock:
            if script == outbound_worker._RENEW_LEASE_LUA:
                return int(self.value == token)
            if self.value == token:
                self.value = None
                return 1
            return 0


def test_redis_supervisor_lease_excludes_overlapping_render_instances():
    redis_store = _LeaseRedis()
    old_instance = outbound_worker.SupervisorLease(redis_store, token='old')
    replacement = outbound_worker.SupervisorLease(redis_store, token='new')

    assert old_instance.acquire() is True
    assert replacement.acquire() is False
    assert old_instance.renew() is True
    old_instance.release()
    assert replacement.acquire() is True


def test_embedded_start_is_nonblocking_singleton_and_shutdown_joins(monkeypatch):
    outbound_worker.stop_embedded_worker()
    started = threading.Event()
    stopped = threading.Event()

    def fake_supervisor(stop):
        started.set()
        stop.wait()
        stopped.set()

    monkeypatch.setitem(
        outbound_worker.settings['sms_submission'], 'embedded_worker_enabled', True)
    monkeypatch.setattr(outbound_worker, '_run_embedded_supervisor', fake_supervisor)

    before = time.monotonic()
    assert outbound_worker.start_embedded_worker() is True
    assert time.monotonic() - before < 0.25
    assert started.wait(1)
    assert outbound_worker.start_embedded_worker() is False
    assert outbound_worker.stop_embedded_worker() is True
    assert stopped.is_set()
    assert outbound_worker.stop_embedded_worker() is False


def test_embedded_worker_flag_disables_start(monkeypatch):
    outbound_worker.stop_embedded_worker()
    monkeypatch.setitem(
        outbound_worker.settings['sms_submission'], 'embedded_worker_enabled', False)
    assert outbound_worker.start_embedded_worker() is False
