"""Manual step commit boundaries; synthetic backend, real WatchSession."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import custom_components.casio_abl100 as integration
from custom_components.casio_abl100.protocol import StepRecord


@pytest.fixture
def session(monkeypatch):
    owner = integration.WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    device = SimpleNamespace(address=owner.address, name='ABL-100WE')
    monkeypatch.setattr(integration.bluetooth, 'async_ble_device_from_address',
                        lambda *args, **kwargs: device)
    monkeypatch.setattr(integration.dt_util, 'now', lambda: datetime(2026, 8, 1))
    owner.client_class = type('Client', (), {})
    return owner


@pytest.mark.asyncio
@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('boundary', [
    'read_cancel', 'read_cancel_twice', 'read_closed', 'cleanup_cancel',
    'clock_cancel', 'clock_closed',
])
async def test_invalidated_read_never_commits(session, monkeypatch, boundary, previous):
    old_record = StepRecord(date(2026, 7, 31), 12) if previous else None
    old_stamp = datetime(2026, 7, 31, tzinfo=timezone.utc) if previous else None
    session.record, session.last_sync, session.fresh = old_record, old_stamp, True
    record = StepRecord(date(2026, 8, 1), 1485)
    observations = []
    session.subscribe(lambda: observations.append(
        (session.record, session.last_sync, session.fresh)))
    cancellation_counts = []
    cleanup_calls = []
    clock_calls = []

    async def swallow_caller_cancel(count=1):
        task = asyncio.current_task()
        for _ in range(count):
            task.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancellation_counts.append(task.cancelling())
        # Deliberately do not uncancel: the public count remains positive.

    async def connect(client_class, *args, **kwargs):
        if boundary == 'cleanup_cancel':
            client_class()  # A connector-created partial client still owned locally.
        return object()

    async def read_record(client, **kwargs):
        assert asyncio.current_task() in session.tasks
        if boundary.startswith('read_cancel'):
            await swallow_caller_cancel(2 if boundary.endswith('twice') else 1)
        elif boundary == 'read_closed':
            session.closed = True
        return record

    async def disconnect(client):
        cleanup_calls.append(client)
        await swallow_caller_cancel()

    def utcnow():
        clock_calls.append(True)
        if boundary == 'clock_cancel':
            asyncio.current_task().cancel()
        elif boundary == 'clock_closed':
            session.closed = True
        return datetime(2026, 8, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(integration, 'establish_connection', connect)
    monkeypatch.setattr(integration, 'read_record', read_record)
    monkeypatch.setattr(integration, 'disconnect_client', disconnect)
    monkeypatch.setattr(integration.dt_util, 'utcnow', utcnow)
    task = asyncio.create_task(session.read())
    result, = await asyncio.gather(task, return_exceptions=True)

    assert session.record is old_record, 'invalidated read committed a new record'
    assert session.last_sync is old_stamp, 'invalidated read committed last_sync'
    assert session.fresh is False, 'invalidated read reported fresh success'
    assert isinstance(result, asyncio.CancelledError)
    assert observations == [(old_record, old_stamp, False)]
    assert not session.tasks and not session.lock.locked()
    if boundary.startswith('read_cancel') or boundary == 'cleanup_cancel':
        assert cancellation_counts == [2 if boundary.endswith('twice') else 1]
    assert len(cleanup_calls) == (1 if boundary == 'cleanup_cancel' else 0)
    assert len(clock_calls) == (1 if boundary.startswith('clock_') else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('caller_cancel', [False, True])
async def test_raised_cancellation_never_commits_and_retains_provenance(
    session, monkeypatch, caller_cancel,
):
    counts = []

    async def connect(*args, **kwargs):
        return object()

    async def read_record(client, **kwargs):
        task = asyncio.current_task()
        if caller_cancel:
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                counts.append(task.cancelling())
                raise
        counts.append(task.cancelling())
        raise asyncio.CancelledError  # Backend-only: caller count is zero.

    monkeypatch.setattr(integration, 'establish_connection', connect)
    monkeypatch.setattr(integration, 'read_record', read_record)
    session.fresh = True
    task = asyncio.create_task(session.read())
    result, = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result, asyncio.CancelledError)
    assert counts == [int(caller_cancel)]
    assert session.record is None and session.last_sync is None
    assert session.fresh is False
    assert not session.tasks and not session.lock.locked()


@pytest.mark.asyncio
async def test_valid_manual_read_still_commits_after_clock(session, monkeypatch):
    record = StepRecord(date(2026, 8, 1), 1485)
    stamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    seen = []

    async def connect(*args, **kwargs):
        return object()

    async def read_record(client, **kwargs):
        return record

    def utcnow():
        assert session.record is None and session.last_sync is None
        assert session.fresh is False
        return stamp

    monkeypatch.setattr(integration, 'establish_connection', connect)
    monkeypatch.setattr(integration, 'read_record', read_record)
    monkeypatch.setattr(integration.dt_util, 'utcnow', utcnow)
    session.subscribe(lambda: seen.append((session.record, session.last_sync, session.fresh)))
    await session.read()
    assert seen == [(record, stamp, True)]
    assert not session.tasks and not session.lock.locked()
