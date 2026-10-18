"""Initial RAW admission via compiled ingress; synthetic external wire only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi import APIClient
from aioesphomeapi.api_pb2 import (BluetoothLERawAdvertisementsResponse,
    BluetoothDeviceRequest, BluetoothGATTGetServicesRequest, BluetoothGATTWriteRequest)
from aioesphomeapi.connection import APIConnection
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO
from bleak_esphome.backend.scanner import ESPHomeScanner
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from tests.ha.test_native_alarm_runtime import native, spin_until, SOURCE, TARGET
from tests.ha.test_unified_sync_runtime import unified, SyncWire, GROUPS, QUERIES


class ProducerWire(SyncWire):
    """Real installed packet dispatcher plus original producer-shaped GATT fake."""
    def __init__(self):
        super().__init__()
        client = APIClient('synthetic.invalid', 6053, None, provide_time=False)
        self.ingress = APIConnection(client._params, None, False, 'synthetic')
        self.raw_count = 0

    def add_message_callback(self, callback, types):
        key = (callback, types)
        self.handlers.add(key)
        remove = self.ingress.add_message_callback(callback, types)
        def close():
            self.handlers.discard(key)
            remove()
        return close

    def emit(self, message):
        number = next(k for k, cls in MESSAGE_TYPE_TO_PROTO.items() if cls is type(message))
        self.ingress.process_packet(number, message.SerializeToString())

    def raw(self, address=TARGET, address_type=1, *, data=None, wrong_first=False):
        self.raw_count += 1
        name = b'ABL-100WE'
        if data is None:
            data = bytes([len(name) + 1, 9]) + name
        message = BluetoothLERawAdvertisementsResponse()
        if wrong_first:
            message.advertisements.add(address=address + 1, address_type=1, data=data)
        message.advertisements.add(address=address, address_type=address_type, rssi=-50, data=data)
        self.emit(message)


@pytest.fixture
async def producer(unified, enable_bluetooth):
    wire = ProducerWire()
    unified.wire = unified.api.wire = wire
    scanner = ESPHomeScanner(SOURCE, 'Synthetic', None, True)
    remove = wire.add_message_callback(scanner.async_on_raw_advertisements,
                                       (BluetoothLERawAdvertisementsResponse,))
    unified.scanner = scanner
    unified.remove_scanner = remove
    yield unified
    if unified.session.auto_reader is not None:
        await unified.session.auto_reader.stop()
    remove()
    assert not wire.handlers


def admission(p, **overrides):
    from custom_components.casio_abl100 import native_sync
    cls = getattr(native_sync, '_RawSyncAdmission', None)
    assert cls is not None, 'Private initial RAW admission API is missing (absence only)'
    args = dict(runtime=p.runtime, client=p.api, connection=p.wire, proxy=p.proxy,
                disconnect_callbacks=p.runtime.disconnect_callbacks, target=TARGET,
                address_type=1, seen=monotonic_time_coarse())
    args.update(overrides)
    return cls(p.session, p.esp, **args)


@pytest.mark.parametrize('address_type', [0, 1])
async def test_admission_runs_without_a_second_raw_wait(producer, address_type):
    p = producer
    ticket = admission(p, address_type=address_type)
    await asyncio.wait_for(p.session.read_all(admission=ticket), 2)
    connects = [x for x in p.wire.calls if p.wire.stage(x) == 'connect']
    assert len(connects) == 1 and connects[0].address_type == address_type
    assert p.wire.raw_count == 0
    assert p.session.sync_group_results == dict.fromkeys(GROUPS, 'success')


@pytest.mark.parametrize('invalid', ['expired', 'future', 'nan', 'type', 'target',
    'source', 'generation', 'runtime', 'client', 'proxy', 'callbacks', 'owner', 'unloaded', 'unavailable', 'wrong_session'])
async def test_ticket_refusal_precedes_freshness_mutation_and_io(producer, invalid):
    p = producer
    args = {}
    if invalid == 'expired': args['seen'] = monotonic_time_coarse() - 6
    if invalid == 'future': args['seen'] = monotonic_time_coarse() + 1
    if invalid == 'nan': args['seen'] = float('nan')
    if invalid == 'type': args['address_type'] = 2
    if invalid == 'target': args['target'] = TARGET + 1
    ticket = admission(p, **args)
    owner = p.watch.runtime_data
    replacement = ProducerWire()
    if invalid == 'source': p.device.details['source'] = '00:00:00:00:00:00'
    if invalid == 'generation': p.api.wire = replacement
    if invalid == 'runtime': p.esp.runtime_data = SimpleNamespace()
    if invalid == 'client': p.runtime.client = object()
    if invalid == 'proxy': p.runtime.bluetooth_device = object()
    if invalid == 'callbacks': p.runtime.disconnect_callbacks = set()
    if invalid == 'owner': p.watch.runtime_data = object()
    if invalid == 'unloaded': p.watch._async_set_state(p.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    if invalid == 'unavailable': p.runtime.available = False
    if invalid == 'wrong_session': ticket.session = object()
    p.session.alarms_fresh = p.session.fresh = True
    before = dict(p.session.sync_group_results)
    try:
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(p.session.read_all(admission=ticket), 2)
        assert not p.wire.calls and not replacement.calls
        assert p.session.alarms_fresh and p.session.fresh
        assert p.session.sync_result == 'never' and p.session.sync_group_results == before
    finally:
        p.watch.runtime_data = owner
        p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)
        p.esp.runtime_data = p.runtime
        p.runtime.client = p.api
        p.runtime.bluetooth_device = p.proxy
        p.api.wire = p.wire
        p.runtime.available = True


async def test_single_use_ticket_does_not_fall_back_to_manual_wait(producer):
    p = producer
    ticket = admission(p)
    await asyncio.wait_for(p.session.read_all(admission=ticket), 2)
    before = len(p.wire.calls)
    with pytest.raises(HomeAssistantError):
        await asyncio.wait_for(p.session.read_all(admission=ticket), 2)
    assert len(p.wire.calls) == before
    assert p.session.alarms_fresh and p.session.timer_fresh


@pytest.mark.parametrize('boundary', ['construct', 'consume'])
async def test_public_owner_generation_and_cancel_rechecked_at_consumption(producer, boundary):
    from custom_components.casio_abl100 import native_sync
    p = producer
    ticket = admission(p)
    old = native_sync.NativeSyncRead.wait
    async def changed(reader):
        if boundary == 'consume':
            reader.owner.cancel()
        else:
            p.api.wire = ProducerWire()
        return await old(reader)
    try:
        with patch.object(native_sync.NativeSyncRead, 'wait', changed):
            expected = asyncio.CancelledError if boundary == 'consume' else HomeAssistantError
            with pytest.raises(expected):
                await asyncio.wait_for(p.session.read_all(admission=ticket), 2)
        assert not p.wire.calls and not p.api.wire.calls
    finally:
        p.api.wire = p.wire


async def test_manual_zero_argument_read_still_waits_for_later_packet(producer):
    p = producer
    p.wire.raw()
    task = asyncio.create_task(p.session.read_all())
    try:
        await spin_until(lambda: p.session.sync_phase == 'waiting')
        assert not p.wire.calls
        p.wire.raw()
        await task
        assert p.session.sync_result == 'success'
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
