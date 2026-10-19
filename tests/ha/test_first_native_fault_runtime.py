"""First-fault observation through real HA/native callbacks; external IO fake.

Producer-shaped cases use installed aioesphomeapi callbacks and protobuf messages.
Malformed statuses are separately labeled defensive synthetic callback inputs.
Operational and diagnostic assertions are deliberately independent.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from aioesphomeapi.api_pb2 import BluetoothDeviceConnectionResponse, BluetoothDeviceRequest, BluetoothGATTWriteRequest
from homeassistant.exceptions import HomeAssistantError

from custom_components.casio_abl100.alarms import parse_alarms
from tests.ha.test_native_alarm_runtime import native, TARGET, FIRST, REST, spin_until  # noqa: F401
from tests.ha.test_unified_sync_runtime import unified, assert_clean, GROUPS, QUERIES  # noqa: F401
from tests.ha.test_native_interactive_ui_runtime import connect_ui, press, status, pristine, entities

FAULT_KEYS = {'failure_kind', 'failure_stage', 'native_status'}
OLD_KEYS = {'phase', 'link_state', 'usable', 'wait_expires_at', 'idle_expires_at',
            'session_expires_at', 'operation_result', 'group_results', 'cleanup_result', 'close_reason'}


def diagnostic(p, stage=None, code=None):
    attrs = status(p)['attributes']
    assert set(attrs) == OLD_KEYS | FAULT_KEYS | {'friendly_name', 'icon'}
    expected = dict(failure_kind='native_link_loss' if stage is not None else None,
                    failure_stage=stage, native_status=code)
    assert {key: attrs[key] for key in FAULT_KEYS} == expected
    assert attrs['native_status'] is None or type(attrs['native_status']) is int
    return attrs


def loss(w, code):
    w.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False, error=code))


def queries(w):
    return [bytes(x.data) for x in w.calls if isinstance(x, BluetoothGATTWriteRequest)]


def cleanup_count(w):
    return sum(isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1 for x in w.calls)


async def failed_read(p, caller):
    with pytest.raises(HomeAssistantError) as caught:
        await caller
    assert caught.value.translation_key == 'interactive_read_failed'
    assert caught.value.translation_placeholders is None
    assert caller.cancelling() == 0
    await p.hass.async_block_till_done()


@pytest.mark.parametrize('held,expected', [('notify61', 'primary_notify'), ('response', 'get15_wait')])
@pytest.mark.parametrize('code', [8, 19])
async def test_public_first_read_discriminates_real_native_histories(unified, held, expected, code):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    ids = {key: (e.entity_id, e.unique_id, e.device_id) for key, e in entities(p).items()}
    previous = parse_alarms(FIRST, REST)
    stamp = datetime(2026, 10, 17, tzinfo=timezone.utc)
    p.session.alarm_snapshot, p.session.alarm_last_sync = previous, stamp
    w.hold_at = held
    caller = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(w.entered.is_set)
    before = queries(w)
    loss(w, code)
    w.release.set()
    await failed_read(p, caller)
    # Unchanged operational oracle, independent of the new fields.
    attrs = status(p)['attributes']
    assert before == ([] if held == 'notify61' else [b'\x15'])
    assert attrs['phase'] == 'closed' and attrs['link_state'] == 'disconnected'
    assert attrs['operation_result'] == 'failed' and attrs['cleanup_result'] == 'success'
    assert attrs['close_reason'] == 'link_lost' and attrs['usable'] is False
    assert attrs['group_results'] == dict(zip(GROUPS, ['failed'] + ['not_attempted'] * 5, strict=True))
    assert p.session.alarm_snapshot is previous and p.session.alarm_last_sync == stamp
    assert p.session.alarms_fresh is False and cleanup_count(w) == 1
    assert queries(w) == before and c.root.done()
    assert {key: (e.entity_id, e.unique_id, e.device_id) for key, e in entities(p).items()} == ids
    assert len(ids) == 23
    assert_clean(p)
    # New diagnostic oracle at HA's actual terminal State.as_dict boundary.
    diagnostic(p, expected, code)


@pytest.mark.parametrize('boundary,expected,code', [
    ('cccd62', 'primary_cccd', 0), ('get15', 'get15_submit', 8),
    ('get16', 'get16_submit', 19), ('get16_wait', 'get16_wait', -2147483648),
])
async def test_primary_cccd_and_query_invocation_vs_local_return(unified, boundary, expected, code):
    p, w = unified, unified.wire
    await connect_ui(p)
    if boundary == 'cccd62':
        w.hold_at = boundary
    elif boundary == 'get16_wait':
        def hold(stage, _request):
            if stage == 'get16':
                w.hold_at = 'response'
        w.before = hold
    else:
        def lose(stage, _request):
            if stage == boundary:
                loss(w, code)
        w.before = lose
    caller = asyncio.create_task(press(p, 'read_interactive'))
    if boundary in ('cccd62', 'get16_wait'):
        await spin_until(w.entered.is_set)
        loss(w, code)
        w.release.set()
    await failed_read(p, caller)
    assert queries(w) == ([] if boundary == 'cccd62' else [b'\x15'] if boundary == 'get15' else [b'\x15', b'\x16'])
    assert p.session.alarm_snapshot is None and p.session.alarm_last_sync is None
    assert not p.session.alarms_fresh and cleanup_count(w) == 1
    assert status(p)['attributes']['operation_result'] == 'failed'
    assert_clean(p)
    diagnostic(p, expected, code)


@pytest.mark.parametrize('cleanup_fault', [False, True])
async def test_first_fault_survives_repeated_callbacks_finalizer_zero_and_cleanup_error(unified, cleanup_fault):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    w.hold_at = 'response'
    caller = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(w.entered.is_set)
    w.entered.clear()
    w.hold_at = 'disconnect'
    if cleanup_fault:
        w.failure_stage = 'disconnect'
    loss(w, 8)
    # Repeated callback before finalizer plus expected callbacks during cleanup.
    loss(w, 19)
    await spin_until(w.entered.is_set)
    if cleanup_fault:
        loss(w, 0)
    # Success path gets its zero from the fake's ordinary disconnect response.
    # Injecting another false response before send would duplicate the vendor's
    # single-response result list, a harness error rather than cleanup success.
    w.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=True, error=0))
    w.release.set()
    await failed_read(p, caller)
    attrs = status(p)['attributes']
    assert attrs['cleanup_result'] == ('failed' if cleanup_fault else 'success')
    assert attrs['close_reason'] == 'link_lost' and attrs['operation_result'] == 'failed'
    assert cleanup_count(w) == 1 and c.root.done()
    frozen = dict(attrs)
    w.notify(TARGET, 61, FIRST)
    loss(w, 2147483647)
    c.reader.observe_link(False, 19)  # Defensive retained old callback after JOIN.
    await p.hass.async_block_till_done()
    assert status(p)['attributes'] == frozen
    assert p.session.alarm_snapshot is None
    assert_clean(p)
    diagnostic(p, 'get15_wait', 8)


@pytest.mark.parametrize('mode', ['explicit_close', 'idle_expired', 'caller_cancelled', 'cleanup_only', 'local_error'])
async def test_non_native_exits_do_not_fabricate_fault(unified, mode):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    if mode in ('explicit_close', 'cleanup_only', 'idle_expired'):
        await press(p, 'read_interactive')
        assert queries(w) == list(QUERIES) and p.session.alarms_fresh
        if mode == 'cleanup_only':
            w.failure_stage = 'disconnect'
            with pytest.raises(HomeAssistantError):
                await press(p, 'close_interactive')
        elif mode == 'idle_expired':
            c._idle_deadline = asyncio.get_running_loop().time() - 1
            c.maintenance_handle.cancel()
            c._tick()
            await c._join()
        else:
            await press(p, 'close_interactive')
    elif mode == 'caller_cancelled':
        w.hold_at = 'notify61'
        caller = asyncio.create_task(press(p, 'read_interactive'))
        await spin_until(w.entered.is_set)
        caller.cancel()
        await asyncio.sleep(0)
        assert not caller.done()
        w.release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert c.operation_result == 'cancelled'
    else:
        w.failure_stage = 'get15'
        await failed_read(p, asyncio.create_task(press(p, 'read_interactive')))
    await p.hass.async_block_till_done()
    assert c.root.done() and cleanup_count(w) == 1
    assert c.close_reason == {'cleanup_only': 'explicit_close', 'local_error': 'read_failed'}.get(mode, mode)
    assert c.cleanup_result == ('failed' if mode == 'cleanup_only' else 'success')
    assert_clean(p)
    diagnostic(p)


@pytest.mark.parametrize('boundary,expected,index', [('idle', 'unknown', None), ('after_read', 'unknown', None),
    ('notify71', 'other', 1), ('get1d00', 'other', 2), ('get13', 'other', 3)])
async def test_idle_and_other_groups_never_retain_get15_stage(unified, boundary, expected, index):
    from custom_components.casio_abl100.native_sync import DESTINATIONS
    p, w = unified, unified.wire
    c = await connect_ui(p)
    if boundary == 'after_read':
        await press(p, 'read_interactive')
    if index is None:
        loss(w, 2147483647)
        await c._join()
    else:
        def lose(stage, _request):
            if stage == boundary:
                loss(w, 19)
        w.before = lose
        caller = asyncio.create_task(press(p, 'read_interactive'))
        await failed_read(p, caller)
        assert list(c.group_results.values()) == ['success'] * index + ['failed'] + ['not_attempted'] * (5 - index)
        assert c.operation_result == 'partial'
        for i, (value, stamp, fresh) in enumerate(DESTINATIONS):
            assert (getattr(p.session, value) is not None) is (i < index)
            assert (getattr(p.session, stamp) is not None) is (i < index)
            assert getattr(p.session, fresh) is (i < index)
    await p.hass.async_block_till_done()
    assert c.close_reason == 'link_lost' and cleanup_count(w) == 1
    assert_clean(p)
    diagnostic(p, expected, 2147483647 if index is None else 19)


@pytest.mark.parametrize('reset', ['reconnect', 'reload'])
async def test_repeated_cycle_fault_successor_and_native_reload_reset(unified, reset):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    await press(p, 'read_interactive')
    previous, stamp = p.session.alarm_snapshot, p.session.alarm_last_sync
    w.hold_at = 'response'
    caller = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(w.entered.is_set)
    loss(w, 8)
    await failed_read(p, caller)
    assert p.session.alarm_snapshot is previous and p.session.alarm_last_sync == stamp
    assert p.session.alarms_fresh is False and cleanup_count(w) == 1
    diagnostic(p, 'get15_wait', 8)
    w.hold_at = None
    if reset == 'reload':
        # Faulted controller itself, not a later pristine successor, is unloaded.
        assert await p.hass.config_entries.async_reload(p.watch.entry_id)
        await p.hass.async_block_till_done()
        p.session = p.watch.runtime_data
        assert p.session.interactive_session is None
        pristine(status(p))
        diagnostic(p)
        assert_clean(p)
        return
    newer = await connect_ui(p)
    assert newer is not c and newer.usable
    diagnostic(p)
    before = list(w.calls)
    c.reader.observe_link(False, 19)
    c.reader.invalidate()
    await p.hass.async_block_till_done()
    assert newer.usable and before == w.calls
    diagnostic(p)
    await press(p, 'close_interactive')
    assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    pristine(status(p))
    assert p.session.interactive_session is None
    diagnostic(p)
    assert_clean(p)


@pytest.mark.parametrize('code', [None, True, False, 1.5, 'PRIVATE_STATUS_CANARY', [], {}, -2147483649, 2147483648],
                         ids=['null', 'true', 'false', 'float', 'private_string', 'list', 'dict', 'below_int32', 'above_int32'])
async def test_defensive_synthetic_status_types_are_null_at_public_sink(unified, code, caplog, capsys):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    # Protobuf int32 cannot produce these; do not mislabel them native captures.
    c.reader.observe_link(False, code)
    await c._join()
    await p.hass.async_block_till_done()
    assert c.close_reason == 'link_lost' and cleanup_count(w) == 1
    assert_clean(p)
    diagnostic(p, 'unknown', None)
    assert 'PRIVATE_STATUS_CANARY' not in str(status(p)) + caplog.text
    captured = capsys.readouterr()
    assert 'PRIVATE_STATUS_CANARY' not in captured.out + captured.err


async def test_prior_explicit_close_intent_does_not_capture_expected_native_loss(unified):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    w.hold_at = 'notify61'
    caller = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(w.entered.is_set)
    closer = asyncio.create_task(press(p, 'close_interactive'))
    await spin_until(lambda: c.close_reason == 'explicit_close')
    # Acquisition is still held; close intent precedes native callback/cleanup.
    assert not c.cleanup_in_progress and not closer.done()
    loss(w, 19)
    w.release.set()
    await closer
    await failed_read(p, caller)
    assert c.close_reason == 'explicit_close' and c.cleanup_result == 'success'
    assert cleanup_count(w) == 1 and queries(w) == []
    assert_clean(p)
    diagnostic(p)
