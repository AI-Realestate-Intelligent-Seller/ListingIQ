"""Offline campaign draft benchmarks; no provider, Redis, or worker access."""

import gc
import json
import tracemalloc
from time import perf_counter
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.leads import campaign as campaign_service
from app.models import Campaign, Lead


def _measure_preview(session, owner, campaign):
    """Measure one normal preview and one isolated peak-allocation preview."""
    engine = session.get_bind()
    queries = 0

    def count_query(*_args, **_kwargs):
        nonlocal queries
        queries += 1

    original_json_loads = json.loads
    event.listen(engine, 'before_cursor_execute', count_query)
    try:
        with patch.object(
                campaign_service.json, 'loads', wraps=original_json_loads,
        ) as loads:
            started = perf_counter()
            preview = campaign_service.preview(session, owner, campaign, page=1)
            latency_ms = (perf_counter() - started) * 1_000
            json_parse_count = loads.call_count
    finally:
        event.remove(engine, 'before_cursor_execute', count_query)

    gc.collect()
    tracemalloc.start()
    try:
        campaign_service.preview(session, owner, campaign, page=1)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return {
        'preview': preview,
        'latency_ms': latency_ms,
        'query_count': queries,
        'json_parse_count': json_parse_count,
        'peak_bytes': peak_bytes,
    }


@pytest.mark.parametrize('lead_count', [500, 1_000, 5_000, 10_000])
def test_campaign_draft_editor_benchmark(lead_count, make_user, session):
    owner = make_user(f'benchmark-{lead_count}@example.test', role='broker')
    campaign = Campaign(
        user_id=owner.id,
        name=f'Benchmark {lead_count}',
        message_template='Hi {{first_name}}, about {{address}}. {{reason}}',
        reason_groups='{"version":1,"groups":{}}',
        status='draft',
    )
    session.add(campaign)
    session.commit()
    session.bulk_insert_mappings(Lead, [
        {
            'user_id': owner.id,
            'owner_name': f'Owner {index}',
            'phone': f'+1312{index:07d}',
            'property_address': f'{index} Benchmark Ave',
            'area': 'Chicago' if index % 2 else '60601',
            'signals': 'expired,high_equity',
            'outreach_reason': 'The property matches the imported vendor criteria',
            'source': 'csv_import',
            'campaign_id': campaign.id,
            'score': index % 100,
            'dnc': False,
        }
        for index in range(lead_count)
    ])
    session.commit()

    started = perf_counter()
    counts = campaign_service._counts_by_campaign(session, owner, {campaign.id})
    counts_ms = (perf_counter() - started) * 1_000
    assert counts[0][campaign.id] == lead_count

    unfinalized = _measure_preview(session, owner, campaign)
    assert len(unfinalized['preview']['recipients']) == 20
    assert unfinalized['preview']['total_items'] == lead_count
    assert unfinalized['json_parse_count'] == 1

    started = perf_counter()
    snapshot_context = campaign_service._finalize_reason_signal_snapshot(
        session, owner, campaign,
    )
    session.commit()
    # Route handlers enter preview with the campaign already loaded. Refresh
    # outside the measurement so commit-expiration is not counted as preview SQL.
    # Detail has also already resolved the brokerage scope for campaign counts.
    session.refresh(campaign)
    campaign_service.brokerage_user_ids(session, owner)
    snapshot_ms = (perf_counter() - started) * 1_000
    snapshot_state = json.loads(campaign.reason_groups)
    snapshot_bytes = len(campaign.reason_groups.encode('utf-8'))
    assert snapshot_context['finalized'] is True
    assert len(snapshot_state['reason_signal_snapshot']['properties']) == lead_count
    del snapshot_context, snapshot_state
    gc.collect()

    finalized = _measure_preview(session, owner, campaign)
    assert len(finalized['preview']['recipients']) == 20
    assert finalized['preview']['total_items'] == lead_count
    assert finalized['json_parse_count'] == 1

    selection = {
        'mode': 'filtered',
        'filters': {'signals': ['expired'], 'area': 'Chicago'},
    }
    started = perf_counter()
    filtered = campaign_service.preview(
        session, owner, campaign, page=1, filters=selection['filters'],
    )
    filter_ms = (perf_counter() - started) * 1_000
    assert filtered['total_items'] == lead_count // 2

    started = perf_counter()
    assigned = campaign_service.assign_main_signal(
        session, owner, campaign, 'expired', selection,
    )
    assign_ms = (perf_counter() - started) * 1_000
    assert assigned['updated_properties'] == lead_count // 2

    started = perf_counter()
    removed = campaign_service.remove_recipients(
        session, owner, campaign, selection,
    )
    removal_ms = (perf_counter() - started) * 1_000
    assert removed['removed_contacts'] == lead_count // 2

    print(
        f'campaign-draft-benchmark leads={lead_count} '
        f'counts_ms={counts_ms:.1f} '
        f'unfinalized_preview_ms={unfinalized["latency_ms"]:.1f} '
        f'unfinalized_queries={unfinalized["query_count"]} '
        f'unfinalized_json_parses={unfinalized["json_parse_count"]} '
        f'unfinalized_peak_mib={unfinalized["peak_bytes"] / 1024 / 1024:.2f} '
        f'finalized_preview_ms={finalized["latency_ms"]:.1f} '
        f'finalized_queries={finalized["query_count"]} '
        f'finalized_json_parses={finalized["json_parse_count"]} '
        f'finalized_peak_mib={finalized["peak_bytes"] / 1024 / 1024:.2f} '
        f'filter_ms={filter_ms:.1f} assign_ms={assign_ms:.1f} '
        f'snapshot_ms={snapshot_ms:.1f} snapshot_bytes={snapshot_bytes} '
        f'removal_ms={removal_ms:.1f}'
    )
