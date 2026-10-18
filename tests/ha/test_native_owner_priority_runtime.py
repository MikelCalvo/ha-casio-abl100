"""Reviewer-owned real HA/native tests; no implementer test adapters imported."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from aioesphomeapi import APIClient, APIVersion, BluetoothProxyFeature, DeviceInfo
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceConnectionResponse, BluetoothDeviceRequest,
    BluetoothGATTGetServicesRequest, BluetoothGATTGetServicesResponse,
    BluetoothGATTGetServicesDoneResponse, BluetoothGATTNotifyRequest,
    BluetoothGATTNotifyResponse, BluetoothGATTNotifyDataResponse,
    BluetoothGATTWriteDescriptorRequest, BluetoothGATTWriteRequest,
    BluetoothGATTWriteResponse, BluetoothLERawAdvertisementsResponse,
)
from bleak_esphome.backend.device import ESPHomeBluetoothDevice
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN

ADDRESS = 'AA:BB:CC:DD:EE:FA'
TARGET = int(ADDRESS.replace(':', ''), 16)
SOURCE = '11:22:33:44:55:66'
BUTTON = 'button.casio_abl_100we_read_steps'
SENSOR = 'sensor.casio_abl_100we_steps_today'
BASE_UUID = '-b012-49a8-b1f8-394fb2032b0f'


def record(day):
    raw = bytearray(400)
    raw[:5] = bytes(((v // 10) * 16 + v % 10) for v in (day.year - 2000, day.month, day.day, 12, 34))
    raw[374:378] = (123).to_bytes(4, 'little')
    return bytes(raw)


class Wire:
    """External message bus; real APIClient retains producer filters/parsing."""
    def __init__(self):
        self.handlers = set()
        self.retained = []
        self.calls = []
        self.day = date(2026, 10, 18)
        self.write_error = None
        self.disconnect_error = None
        self.bad_header = False
        self.cancel_after_write = False
        self.counts = []

    def add_message_callback(self, callback, types):
        item = (callback, types)
        self.handlers.add(item)
        self.retained.append(item)
        return lambda: self.handlers.discard(item)

    def emit(self, message):
        for callback, types in tuple(self.handlers):
            if type(message) in types:
                callback(message)

    def raw(self):
        message = BluetoothLERawAdvertisementsResponse()
        message.advertisements.add(address=TARGET, address_type=1)
        self.emit(message)

    def send_message_callback_response(self, request, callback, types):
        remove = self.add_message_callback(callback, types)
        self.send_message(request)
        return remove

    def send_message(self, request):
        self.calls.append(request)
        if isinstance(request, BluetoothDeviceRequest):
            disconnect = request.request_type == 1
            if disconnect and self.disconnect_error is not None:
                raise self.disconnect_error
            self.emit(BluetoothDeviceConnectionResponse(address=request.address, connected=not disconnect, mtu=23))
        elif isinstance(request, BluetoothGATTGetServicesRequest):
            response = BluetoothGATTGetServicesResponse(address=request.address)
            def words(value):
                n = UUID(value).int
                return [n >> 64, n & ((1 << 64) - 1)]
            service = response.services.add(uuid=words('26eb000d' + BASE_UUID), handle=42)
            for prefix, handle, properties in [('26eb002d', 61, 0x10), ('26eb0023', 71, 0x18), ('26eb0024', 81, 0x10), ('26eb002c', 91, 0x04)]:
                char = service.characteristics.add(uuid=words(prefix + BASE_UUID), handle=handle, properties=properties)
                char.descriptors.add(uuid=words('00002902-0000-1000-8000-00805f9b34fb'), handle=handle + 1)
            self.emit(response)
            self.emit(BluetoothGATTGetServicesDoneResponse(address=request.address))
        elif isinstance(request, BluetoothGATTNotifyRequest):
            self.emit(BluetoothGATTNotifyResponse(address=request.address, handle=request.handle))
        elif isinstance(request, BluetoothGATTWriteDescriptorRequest):
            self.emit(BluetoothGATTWriteResponse(address=request.address, handle=request.handle))
        elif isinstance(request, BluetoothGATTWriteRequest):
            if bytes(request.data) in (b'\x15', b'\x16'):
                if self.write_error is not None:
                    raise self.write_error
                raise AssertionError('No alarm success fixture needed')
            header = b'\x00\x11\x91\x01\x00' if self.bad_header else b'\x00\x11\x90\x01\x00'
            raw = record(self.day)
            for handle, value in [(71, header), (81, raw[:200]), (81, raw[200:])]:
                self.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=handle, data=value))
            if self.cancel_after_write:
                asyncio.current_task().cancel()
            self.counts.append(asyncio.current_task().cancelling())
            if self.write_error is not None:
                raise self.write_error
            self.emit(BluetoothGATTWriteResponse(address=request.address, handle=request.handle))
        else:
            raise AssertionError(type(request).__name__)

    async def send_messages_await_response_complex(self, requests, append, stop, types, timeout):
        # Preserve the predicates provided by the real producer methods rather
        # than returning a prefiltered, consumer-preferred response list.
        future = asyncio.get_running_loop().create_future()
        responses = []
        def receive(message):
            if append(message):
                responses.append(message)
            if stop(message) and not future.done():
                future.set_result(responses)
        remove = self.add_message_callback(receive, types)
        try:
            for request in requests:
                self.send_message(request)
            return await asyncio.wait_for(future, timeout)
        finally:
            remove()


class Client(APIClient):
    def _get_connection(self):
        return self.wire


async def until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), 'reviewer stage not reached'


@pytest.fixture
async def owner(hass, enable_custom_integrations):
    wire = Wire()
    client = Client('synthetic.invalid', 6053, None)
    client.wire = wire
    proxy = ESPHomeBluetoothDevice('synthetic', SOURCE, ble_connections_free=1, ble_connections_limit=1, available=True)
    runtime = SimpleNamespace(client=client, available=True, bluetooth_device=proxy,
        device_info=DeviceInfo(bluetooth_mac_address=SOURCE, bluetooth_proxy_feature_flags=int(
            BluetoothProxyFeature.ACTIVE_CONNECTIONS | BluetoothProxyFeature.REMOTE_CACHING | BluetoothProxyFeature.RAW_ADVERTISEMENTS)),
        api_version=APIVersion(1, 9), disconnect_callbacks=set())
    esp = MockConfigEntry(domain='esphome', title='Proxy', data={})
    esp.add_to_hass(hass)
    esp.runtime_data = runtime
    esp._async_set_state(hass, ConfigEntryState.LOADED, None)
    watch = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': ADDRESS}, unique_id=ADDRESS)
    watch.add_to_hass(hass)
    device = SimpleNamespace(address=ADDRESS, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch(
        'custom_components.casio_abl100.establish_connection', side_effect=AssertionError('Unexpected fallback')) as fallback, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18, tzinfo=timezone.utc)):
        assert await hass.config_entries.async_setup(watch.entry_id)
        await hass.async_block_till_done()
        assert not wire.calls
        yield SimpleNamespace(hass=hass, wire=wire, client=client, runtime=runtime, watch=watch, session=watch.runtime_data)
        fallback.assert_not_called()
        if watch.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(watch.entry_id)
        esp._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)


async def start(owner, service=True):
    coro = owner.hass.services.async_call('button', 'press', {'entity_id': BUTTON}, blocking=True) if service else owner.session.read()
    task = asyncio.create_task(coro)
    await until(lambda: owner.session.step_phase == 'waiting' or task.done())
    assert not task.done()
    owner.wire.raw()
    return task


def clean(owner):
    assert not owner.wire.handlers
    assert not owner.runtime.disconnect_callbacks
    assert not owner.session.tasks and not owner.session.lock.locked()
    assert not owner.client._notify_callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [date(2026, 10, 17), date(2026, 10, 19)])
async def test_native_date_gate_retains_last_good_and_alarm_freshness(owner, day):
    await (await start(owner))
    await owner.hass.async_block_till_done()
    previous, stamp = owner.session.record, owner.session.last_sync
    assert previous.steps == 123 and previous.record_date == date(2026, 10, 18)
    from custom_components.casio_abl100.alarms import parse_alarms
    alarm = parse_alarms(b'\x15\x40\x00\x07\x23', b'\x16' + b'\x00\x00\x08\x09' * 4)
    alarm_stamp = datetime(2026, 10, 17, tzinfo=timezone.utc)
    owner.session.alarm_snapshot, owner.session.alarm_last_sync, owner.session.alarms_fresh = alarm, alarm_stamp, True
    owner.wire.day = day
    task = await start(owner)
    with pytest.raises(HomeAssistantError) as error:
        await task
    await owner.hass.async_block_till_done()
    assert error.value.translation_key == 'stale_date'
    assert owner.session.record is previous and owner.session.last_sync == stamp
    assert not owner.session.fresh
    assert owner.session.alarm_snapshot is alarm and owner.session.alarm_last_sync is alarm_stamp
    assert owner.session.alarms_fresh
    assert owner.hass.states.get(SENSOR).state == '123'
    assert owner.hass.states.get(SENSOR).attributes['fresh'] is False
    clean(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('primary_kind', ['ordinary', 'caller_cancel'])
@pytest.mark.parametrize('cleanup', ['none', 'once', 'ordinary', 'backend_cancel'])
async def test_original_write_failure_has_priority_over_cleanup_fault(owner, primary_kind, cleanup, caplog):
    primary = RuntimeError('PRIVATE_PRIMARY_WRITE')
    owner.wire.bad_header = True
    owner.wire.write_error = primary
    owner.wire.cancel_after_write = primary_kind == 'caller_cancel'
    if cleanup != 'none':
        owner.wire.disconnect_error = RuntimeError('PRIVATE_CLEANUP') if cleanup != 'backend_cancel' else asyncio.CancelledError('PRIVATE_CLEANUP')
    if cleanup == 'once':
        send = owner.wire.send_message
        def fail_once(request):
            try:
                return send(request)
            finally:
                if isinstance(request, BluetoothDeviceRequest) and request.request_type == 1:
                    owner.wire.disconnect_error = None
        owner.wire.send_message = fail_once
    task = await start(owner, service=False)
    if primary_kind == 'caller_cancel':
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.wire.counts == [1]
        clean(owner)
        assert owner.session.record is None and owner.session.last_sync is None and not owner.session.fresh
        return
    with pytest.raises(HomeAssistantError) as error:
        await task
    clean(owner)
    assert owner.wire.counts == [0]
    assert owner.session.record is None and owner.session.last_sync is None and not owner.session.fresh
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert error.value.translation_key == 'read_failed'
    assert error.value.__cause__ is primary, 'Fallback owner disconnect replaced the original START write failure'


@pytest.mark.asyncio
async def test_new_step_consumer_rechecks_cancel_at_notify_child_entry(owner):
    from custom_components.casio_abl100 import native_step_adapter
    original = native_step_adapter.joined
    calls = []
    async def pending(operation, acquired=None):
        if not calls:  # Cancel exactly once at the first acquisition boundary.
            calls.append(True)
            asyncio.current_task().cancel()
        return await original(operation, acquired)
    with patch.object(native_step_adapter, 'joined', pending):
        task = await start(owner, service=False)
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == [True]
    assert not any(isinstance(r, (BluetoothGATTNotifyRequest, BluetoothGATTWriteDescriptorRequest, BluetoothGATTWriteRequest)) for r in owner.wire.calls)
    clean(owner)


@pytest.mark.asyncio
async def test_retained_notify_callback_after_completion_cannot_mutate_commit(owner):
    await (await start(owner))
    await owner.hass.async_block_till_done()
    previous, stamp = owner.session.record, owner.session.last_sync
    retained = [cb for cb, types in owner.wire.retained if types == (BluetoothGATTNotifyDataResponse,)]
    assert len(retained) == 3
    for callback in retained:
        for handle in [61, 71, 81]:
            callback(BluetoothGATTNotifyDataResponse(address=TARGET, handle=handle, data=b'PRIVATE_LATE_CALLBACK'))
    await owner.hass.async_block_till_done()
    assert owner.session.record is previous and owner.session.last_sync == stamp and owner.session.fresh
    assert owner.hass.states.get(SENSOR).state == '123'
    clean(owner)


@pytest.mark.asyncio
async def test_alarm_failure_priority_baseline_comparison(owner):
    primary = RuntimeError('PRIVATE_ALARM_PRIMARY')
    owner.wire.write_error = primary
    owner.wire.disconnect_error = RuntimeError('PRIVATE_ALARM_CLEANUP')
    task = asyncio.create_task(owner.session.read_alarms())
    await until(lambda: owner.session.alarm_phase == 'waiting' or task.done())
    assert not task.done()
    owner.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    clean(owner)
    assert error.value.__cause__ is primary, 'Original alarm failure replaced by owner fallback cleanup'


@pytest.mark.asyncio
@pytest.mark.parametrize('backend_cancel', [False, True])
async def test_valid_step_data_with_cleanup_failure_never_publishes(owner, backend_cancel):
    owner.wire.disconnect_error = (
        asyncio.CancelledError('PRIVATE_CLEANUP_ONLY') if backend_cancel
        else RuntimeError('PRIVATE_CLEANUP_ONLY')
    )
    task = await start(owner, service=False)
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert error.value.translation_key == 'read_failed'
    assert owner.session.record is None and owner.session.last_sync is None
    assert not owner.session.fresh
    assert task.cancelling() == 0
    clean(owner)
