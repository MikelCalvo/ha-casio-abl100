"""Actual HA services and native methods; only the external producer is fake."""
import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import (BluetoothGATTWriteRequest, BluetoothDeviceRequest,
    BluetoothGATTNotifyRequest, BluetoothGATTGetServicesResponse, BluetoothDeviceConnectionResponse)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from tests.ha.test_native_alarm_runtime import native, spin_until, ADDRESS, TARGET, SOURCE
from tests.ha.test_unified_sync_runtime import SyncWire, drive, assert_clean, QUERIES
from tests.ha.test_native_interactive_ui_runtime import entities, press, connect_ui
from tests.ha.test_zone_entities import OLD_KEYS, ZONE_KEYS, SYNC_KEYS, INTERACTIVE_KEYS

BASE23 = OLD_KEYS | ZONE_KEYS | SYNC_KEYS | INTERACTIVE_KEYS
ALARM_EDIT_KEYS = {*(f'alarm_{n}_time_draft' for n in range(1,6)),
                   *(f'alarm_{n}_enabled_draft' for n in range(1,6)),
                   'hourly_signal_draft', 'apply_alarm_draft', 'discard_alarm_draft'}
FIRST = bytes.fromhex('15 40 07 06 11')
REST = bytes.fromhex('16 00 00 08 09 40 07 0a 0b 00 07 0c 0d 40 00 0e 0f')


class MemoryWire(SyncWire):
    """Finite SET-capable watch memory, shared by all target addresses."""
    def __init__(self):
        super().__init__()
        self.memories = {}
        self.sets = []
        self.readbacks = {}
        self.hold_readback = False
        self.pending_readback = []
        self.write_supported = True
        self.mutate_readback = None
        self.duplicate = False

    def memory(self, address):
        return self.memories.setdefault(address, {b'\x15': FIRST, b'\x16': REST})

    def responses(self, request):
        result = super().responses(request)
        for response in result:
            if isinstance(response, BluetoothGATTGetServicesResponse):
                for service in response.services:
                    for char in service.characteristics:
                        if char.handle == 61 and self.write_supported:
                            char.properties |= 0x08
        return result

    def stage(self, request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.handle == 61:
            return f'set{request.data[0]:02x}'
        return super().stage(request)

    def send_message(self, request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.handle == 61:
            self.calls.append(request)
            stage = self.stage(request)
            if self.before:
                self.before(stage, request)
            if self.failure_stage == stage:
                raise self.failure
            raw = bytes(request.data)
            assert request.response and ((raw[0],len(raw)) in ((0x15,5),(0x16,17)))
            old = self.memory(request.address)[raw[:1]]
            assert all(raw[i] == 0x40 for i in range(2,len(raw),4))
            value = bytearray(raw)
            for i in range(2,len(raw),4):
                value[i] = old[i]  # SET aux40 is not GET aux; preserve GET memory.
            self.memory(request.address)[raw[:1]] = bytes(value)
            self.sets.append(request)
            for response in self.responses(request):
                self.emit(response)
            if self.after:
                self.after(stage, request)
            return
        if isinstance(request, BluetoothGATTWriteRequest) and bytes(request.data) in (b'\x15',b'\x16'):
            self.calls.append(request)
            data = bytes(request.data)
            if self.failure_stage == self.stage(request):
                raise self.failure
            raw = self.memory(request.address)[data]
            is_readback = any(x.address == request.address for x in self.sets)
            if is_readback and self.mutate_readback:
                raw = self.mutate_readback(data, raw)
            if is_readback and self.hold_readback:
                self.entered.set()
                self.pending_readback.append((request.address,61,raw))
            else:
                self.notify(request.address,61,raw)
                if self.duplicate:
                    asyncio.get_running_loop().call_soon(self.notify,request.address,61,raw)
            return
        super().send_message(request)

    def release_readback(self):
        self.hold_readback = False
        for args in self.pending_readback:
            self.notify(*args)
        self.pending_readback.clear()


@pytest.fixture
async def editing(native):
    wire = MemoryWire()
    native.wire = native.api.wire = wire
    yield native


def state(p, key, entry=None):
    return p.hass.states.get(entities(p, entry)[key].entity_id).as_dict()


async def service(p, key, service, value=None, entry=None):
    entity = entities(p,entry)[key]
    data = {'entity_id':entity.entity_id}
    if value is not None:
        data['time'] = value
    await p.hass.services.async_call(entity.domain,service,data,blocking=True)


async def test_native_services_stage_connect_apply_readback_and_discard(editing):
    p = editing
    assert entities(p).keys() == BASE23 | ALARM_EDIT_KEYS
    assert len(BASE23) == 23 and len(entities(p)) == 36
    assert state(p,'alarm_2_time_draft')['state'] == 'unknown'
    assert state(p,'alarm_4_enabled_draft')['state'] == 'unknown'
    await service(p,'alarm_2_time_draft','set_value','09:27')
    await service(p,'alarm_4_enabled_draft','turn_on')
    await service(p,'hourly_signal_draft','turn_on')
    assert not p.wire.calls and p.session.alarm_snapshot is None
    c = await connect_ui(p)
    assert not p.wire.sets and not any(isinstance(x,BluetoothGATTWriteRequest) for x in p.wire.calls)
    p.wire.hold_readback = True
    task = asyncio.create_task(press(p,'apply_alarm_draft'))
    try:
        await spin_until(lambda: p.wire.entered.is_set() or task.done())
        if task.done():
            await task
        assert p.wire.entered.is_set()
        assert p.session.alarm_snapshot.alarms[1].hour == 8
        assert p.session.alarm_snapshot.hourly_signal is False
        assert not p.session.alarms_fresh and not task.done()
        assert [bytes(x.data) for x in p.wire.sets] == [
            bytes.fromhex('15 c0 40 06 11'),
            bytes.fromhex('16 00 40 09 1b 40 40 0a 0b 40 40 0c 0d 40 40 0e 0f')]
        assert all(x.handle == 61 and x.response for x in p.wire.sets)
        p.wire.release_readback()
        await task
        await p.hass.async_block_till_done()
        assert state(p,'alarm_2_time_draft')['state'] == '09:27:00'
        assert state(p,'alarm_4_enabled_draft')['state'] == 'on'
        assert state(p,'hourly_signal_draft')['state'] == 'on'
        attrs = state(p,'apply_alarm_draft')['attributes']
        assert attrs['apply_result'] == 'verified' and not attrs['draft_pending']
        assert attrs['write_submitted'] and attrs['verified_at']
        assert p.session.alarms_fresh
        assert p.session.alarm_snapshot.alarms[0].aux == 7
        assert p.session.observations is not None
        before = list(p.wire.calls)
        await service(p,'alarm_2_time_draft','set_value','05:06:00')
        await press(p,'discard_alarm_draft')
        await p.hass.async_block_till_done()
        assert p.wire.calls == before and state(p,'alarm_2_time_draft')['state'] == '09:27:00'
        assert c.usable
    finally:
        p.wire.release_readback()
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await p.session.close_interactive()
    assert_clean(p)


async def test_noop_conflict_and_one_lifetime_across_readall(editing):
    p = editing
    await connect_ui(p)
    await service(p,'alarm_1_time_draft','set_value','06:17:00')
    await press(p,'apply_alarm_draft')
    assert not p.wire.sets and state(p,'apply_alarm_draft')['attributes']['apply_result'] == 'unchanged'
    await service(p,'alarm_2_time_draft','set_value','11:12')
    p.wire.memory(TARGET)[b'\x15'] = bytes.fromhex('15 40 07 05 11')
    with pytest.raises(HomeAssistantError):
        await press(p,'apply_alarm_draft')
    assert not p.wire.sets and state(p,'apply_alarm_draft')['attributes']['apply_result'] == 'conflict'
    assert p.session.alarm_snapshot.alarms[0].hour == 5
    await press(p,'discard_alarm_draft')
    await service(p,'alarm_1_time_draft','set_value','04:18')
    await press(p,'apply_alarm_draft')
    with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
        await press(p,'read_interactive')
    await service(p,'hourly_signal_draft','turn_on')
    await press(p,'apply_alarm_draft')
    await p.session.close_interactive()
    assert len([x for x in p.wire.calls if isinstance(x,BluetoothDeviceRequest) and x.request_type != 1]) == 1
    assert len([x for x in p.wire.calls if isinstance(x,BluetoothGATTNotifyRequest) and x.handle == 61]) == 1
    assert_clean(p)


@pytest.mark.parametrize('fault', ['set16','cancel','mismatch','preservation','malformed','duplicate','cleanup'])
async def test_unknown_mismatch_no_replay_and_getonly_reconciliation(editing,fault):
    p = editing
    c = await connect_ui(p)
    await service(p,'alarm_2_time_draft','set_value','01:02')
    if fault == 'set16':
        p.wire.failure_stage = 'set16'
    elif fault in ('cancel','cleanup'):
        p.wire.hold_readback = True
    elif fault == 'mismatch':
        p.wire.mutate_readback = lambda query,raw: REST if query == b'\x16' else raw
    elif fault == 'preservation':
        p.wire.mutate_readback = lambda query,raw: raw[:2]+b'\0'+raw[3:] if query == b'\x15' else raw
    elif fault == 'malformed':
        p.wire.mutate_readback = lambda query,raw: b'\xff'
    elif fault == 'duplicate':
        p.wire.duplicate = True
    task = asyncio.create_task(press(p,'apply_alarm_draft'))
    if fault in ('cancel','cleanup'):
        await spin_until(lambda: p.wire.entered.is_set() or task.done())
        assert p.wire.entered.is_set()
        if fault == 'cleanup':
            p.wire.failure_stage = 'disconnect'
            with pytest.raises(HomeAssistantError):
                await p.session.close_interactive()
        else:
            task.cancel()
    with pytest.raises(asyncio.CancelledError if fault == 'cancel' else HomeAssistantError):
        await task
    await p.hass.async_block_till_done()
    expected = {'mismatch':'mismatch','preservation':'preservation_mismatch','duplicate':'failed_before_write'}.get(fault,'unknown')
    attrs = state(p,'apply_alarm_draft')['attributes']
    assert attrs['apply_result'] == expected
    assert attrs['draft_pending']
    if expected == 'unknown':
        assert attrs['needs_reconciliation'] and not p.session.alarms_fresh
        old = p.session.alarm_snapshot
        before = len(p.wire.sets)
        with pytest.raises(HomeAssistantError):
            await p.session.apply_alarm_draft()
        await press(p,'discard_alarm_draft')
        assert state(p,'discard_alarm_draft')['attributes']['needs_reconciliation']
        p.wire.failure_stage = None
        p.wire.mutate_readback = None
        p.wire.release_readback()
        if not c.usable:
            await connect_ui(p)
        with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
            await press(p,'read_interactive')
        assert p.session.alarm_snapshot is not old
        attrs = state(p,'apply_alarm_draft')['attributes']
        assert not attrs['needs_reconciliation'] and attrs['apply_result'] == 'unknown'
        assert len(p.wire.sets) == before
    await p.session.close_interactive()
    assert_clean(p)


async def test_busy_local_guards_and_native_invocation_before_att(editing):
    p = editing
    await service(p,'alarm_1_time_draft','set_value','02:03')
    with pytest.raises(HomeAssistantError):
        await p.session.apply_alarm_draft()
    await connect_ui(p)
    p.wire.hold_at = 'set15'
    task = asyncio.create_task(press(p,'apply_alarm_draft'))
    await spin_until(lambda: p.wire.entered.is_set() or task.done())
    try:
        assert p.wire.entered.is_set()
        for _ in range(8):
            await asyncio.sleep(0)
        attrs = state(p,'apply_alarm_draft')['attributes']
        assert attrs['write_submitted'] and not p.session.alarms_fresh
        assert not p.wire.sets  # public native method invoked, ATT not complete
        before = list(p.wire.calls)
        for action in (p.session.apply_alarm_draft,p.session.read_interactive):
            with pytest.raises(HomeAssistantError):
                await action()
        for action in (lambda:p.session.stage_alarm_time(1,time(4,5)),p.session.discard_alarm_draft):
            with pytest.raises(HomeAssistantError):
                action()
        await service(p,'alarm_1_time_draft','set_value','04:05')  # HA filters unavailable
        assert p.wire.calls == before
    finally:
        p.wire.release.set()
        await task
        await p.session.close_interactive()
    assert_clean(p)


async def test_write_metadata_lazy_and_read_still_supported(editing):
    p = editing
    p.wire.write_supported = False
    await connect_ui(p)
    await service(p,'hourly_signal_draft','turn_on')
    with pytest.raises(HomeAssistantError):
        await press(p,'apply_alarm_draft')
    assert not p.wire.sets
    if not p.session.interactive_session.usable:
        await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
        await press(p,'read_interactive')
    assert p.session.alarms_fresh
    await p.session.close_interactive()
    assert_clean(p)


@pytest.mark.parametrize('language',['en','es'])
async def test_translated_additions_minute_rejection_and_reload(editing,language):
    from homeassistant.components.time import DATA_COMPONENT as TIMES
    p = editing
    p.hass.config.language = language
    assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    await p.hass.async_block_till_done()
    mapping = entities(p)
    assert mapping.keys() == BASE23 | ALARM_EDIT_KEYS
    for n in range(1,6):
        key = f'alarm_{n}_time_draft'
        name = f'Borrador de hora de alarma {n}' if language == 'es' else f'Alarm {n} time draft'
        assert mapping[key].original_name == name
        suffix = f'borrador_de_hora_de_alarma_{n}' if language == 'es' else key
        # Registry retains original IDs across language reload; names translate.
        assert mapping[key].domain == 'time'
        assert state(p,key)['state'] == 'unknown'
        enable = f'alarm_{n}_enabled_draft'
        assert mapping[enable].original_name == (f'Borrador de activación de alarma {n}' if language == 'es' else f'Alarm {n} enabled draft')
    assert mapping['hourly_signal_draft'].original_name == ('Borrador de señal horaria' if language == 'es' else 'Hourly signal draft')
    control = p.hass.data[TIMES].get_entity(mapping['alarm_1_time_draft'].entity_id)
    from datetime import timezone
    for value in (time(1,2,3),time(1,2,0,7),time(1,2,tzinfo=timezone.utc)):
        with pytest.raises(ValueError):
            await control.async_set_value(value)
    assert not p.session.alarm_draft.pending and not p.wire.calls
    await service(p,'alarm_1_time_draft','set_value','03:04:00')
    assert state(p,'alarm_1_time_draft')['state'] == '03:04:00'
    assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending and not p.wire.calls
    assert state(p,'alarm_1_time_draft')['state'] == 'unknown'


async def test_restored_dto_requires_raw_baseline_and_preserves_other_groups(editing):
    p = editing
    c = await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
        await press(p,'read_interactive')
    from tests.ha.test_observations_runtime import observations
    before = observations(p.session)[1:]
    await p.session.close_interactive()
    assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    snapshot = p.session.alarm_snapshot
    assert not hasattr(snapshot.alarms[0],'flags')
    await service(p,'alarm_2_enabled_draft','turn_on')
    await connect_ui(p)
    start = len(p.wire.calls)
    await press(p,'apply_alarm_draft')
    writes = [bytes(x.data) for x in p.wire.calls[start:] if isinstance(x,BluetoothGATTWriteRequest)]
    assert writes[:2] == [b'\x15',b'\x16']
    assert writes[2] == bytes.fromhex('15 40 40 06 11')
    assert p.session.alarm_snapshot.alarms[0].aux == 7
    after = observations(p.session)[1:]
    assert [t for _,t,_ in after] == [t for _,t,_ in before]
    assert all(not fresh for _,_,fresh in after)
    await p.session.close_interactive()
    assert_clean(p)


async def test_two_entries_shared_producer_isolation(editing):
    p = editing
    address = 'AA:BB:CC:DD:EE:01'
    target = int(address.replace(':',''),16)
    other = MockConfigEntry(domain=DOMAIN,title='Other watch',data={'address':address},unique_id=address)
    other.add_to_hass(p.hass)
    def lookup(_hass,address,**kwargs):
        return SimpleNamespace(address=address,name='ABL-100WE',details={'source':SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address',side_effect=lookup):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        await p.hass.async_block_till_done()
        await service(p,'alarm_2_time_draft','set_value','01:02')
        await service(p,'alarm_2_time_draft','set_value','03:04',entry=other)
        a = await connect_ui(p)
        task = asyncio.create_task(press(p,'connect_interactive',other))
        for _ in range(1000):
            if task.done(): break
            p.wire.raw(target)
            await asyncio.sleep(0)
        await task
        b = other.runtime_data.interactive_session
        assert a.reader.connection is b.reader.connection is p.wire
        await press(p,'apply_alarm_draft')
        assert other.runtime_data.alarm_snapshot is None
        assert other.runtime_data.alarm_draft.pending
        await press(p,'apply_alarm_draft',other)
        assert p.session.alarm_snapshot.alarms[1].hour == 1
        assert other.runtime_data.alarm_snapshot.alarms[1].hour == 3
        await service(p,'hourly_signal_draft','turn_on',entry=other)
        other_before = other.runtime_data.alarm_snapshot
        assert await p.hass.config_entries.async_reload(p.watch.entry_id)
        p.session = p.watch.runtime_data
        assert b.usable and other.runtime_data.alarm_snapshot is other_before
        assert other.runtime_data.alarm_draft.pending
        await press(p,'discard_alarm_draft',other)
        assert not other.runtime_data.alarm_draft.pending
        assert await p.hass.config_entries.async_unload(other.entry_id)
    assert_clean(p)


@pytest.mark.parametrize('refused',[False,True])
async def test_unload_during_apply_joins_unknown_and_removes_subscriptions(editing,refused):
    p = editing
    await connect_ui(p)
    await service(p,'hourly_signal_draft','turn_on')
    original = p.session
    p.wire.hold_readback = True
    task = asyncio.create_task(press(p,'apply_alarm_draft'))
    await spin_until(p.wire.entered.is_set)
    if refused:
        from custom_components.casio_abl100 import async_unload_entry
        with patch.object(p.hass.config_entries,'async_unload_platforms',return_value=False):
            assert not await async_unload_entry(p.hass,p.watch)
        assert not original.closed
    else:
        assert await p.hass.config_entries.async_unload(p.watch.entry_id)
        assert original.closed and not original.listeners
    with pytest.raises(HomeAssistantError):
        await task
    assert original.alarm_draft.outcome.result == 'unknown'
    assert original.alarm_draft.needs_reconciliation
    if not refused:
        for action in (lambda:original.stage_hourly_signal(False),original.discard_alarm_draft):
            with pytest.raises(HomeAssistantError): action()
    assert_clean(p)


async def test_first_native_fault_after_partial_set_and_generation_loss(editing):
    p = editing
    c = await connect_ui(p)
    await service(p,'alarm_1_enabled_draft','turn_off')
    def lost(stage,request):
        if stage == 'set15':
            p.wire.emit(BluetoothDeviceConnectionResponse(address=TARGET,connected=False,error=7))
    p.wire.after = lost
    with pytest.raises(HomeAssistantError):
        await press(p,'apply_alarm_draft')
    assert c.native_fault.stage == 'set15_submit' and c.native_fault.status == 7
    assert p.session.alarm_draft.outcome.result == 'unknown'
    assert len(p.wire.sets) == 1
    await p.hass.async_block_till_done()
    assert_clean(p)
