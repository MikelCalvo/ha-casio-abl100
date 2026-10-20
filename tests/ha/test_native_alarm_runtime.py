"""Real HA entry/button/publication; only native connection IO is synthetic."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from aioesphomeapi import APIVersion, APIClient, BluetoothProxyFeature, DeviceInfo
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceConnectionResponse,
    BluetoothDeviceRequest,
    BluetoothGATTGetServicesRequest,
    BluetoothGATTGetServicesResponse,
    BluetoothGATTNotifyDataResponse,
    BluetoothGATTNotifyRequest,
    BluetoothGATTNotifyResponse,
    BluetoothGATTWriteDescriptorRequest,
    BluetoothGATTWriteRequest,
    BluetoothGATTWriteResponse,
    BluetoothLERawAdvertisementsResponse,
)
from bleak_esphome.backend.device import ESPHomeBluetoothDevice
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.casio_abl100 import DOMAIN
from tests.protocol.test_native_alarm_adapter import metadata

ADDRESS = 'AA:BB:CC:DD:EE:FF'
TARGET = int(ADDRESS.replace(':', ''), 16)
SOURCE = '11:22:33:44:55:66'
BUTTON = 'button.casio_abl_100we_read_alarms'
FIRST = b'\x15\x40\x00\x07\x23'
REST = b'\x16' + b'\x00\x00\x08\x09' * 4


class NativeWire:
    """External APIConnection exchange fake, not a replacement client method."""

    def __init__(self):
        self.handlers = set()
        self.calls = []
        self.hold_at = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.registered = asyncio.Event()
        self.connected = True
        self.error = 0
        self.bad = False
        self.bad_metadata = False
        self.notify_failure = None

    def add_message_callback(self, callback, types):
        key = (callback, types)
        self.handlers.add(key)
        self.registered.set()
        return lambda: self.handlers.discard(key)

    def emit(self, message):
        for callback, types in tuple(self.handlers):
            if type(message) in types:
                callback(message)

    def raw(self, address=TARGET, address_type=1):
        message = BluetoothLERawAdvertisementsResponse()
        message.advertisements.add(address=address, address_type=address_type)
        self.emit(message)

    def send_message_callback_response(self, request, callback, types):
        remove = self.add_message_callback(callback, types)
        self.send_message(request)
        return remove

    def send_message(self, request):
        self.calls.append(request)
        if isinstance(request, BluetoothDeviceRequest):
            if int(request.request_type) == 0:
                raise AssertionError('Unsupported legacy connect')
            # DISCONNECT is 1; CONNECT_V3_WITHOUT_CACHE is 4.
            if int(request.request_type) == 1:
                self.emit(
                    BluetoothDeviceConnectionResponse(address=TARGET, connected=False)
                )
            elif self.hold_at != 'connect':
                self.emit(
                    BluetoothDeviceConnectionResponse(
                        address=TARGET,
                        connected=self.connected,
                        mtu=23,
                        error=self.error,
                    )
                )
            else:
                self.entered.set()
        elif isinstance(request, BluetoothGATTWriteRequest):
            if self.hold_at == 'response':
                self.entered.set()
                return
            frame = FIRST if request.data == b'\x15' else REST
            if self.bad:
                frame = frame[:-1]
            self.emit(
                BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=frame)
            )
        else:
            raise AssertionError(type(request).__name__)

    async def send_messages_await_response_complex(
        self, requests, append, stop, types, timeout
    ):
        (request,) = requests
        self.calls.append(request)
        if isinstance(request, BluetoothGATTNotifyRequest):
            stage = 'notify'
            response = BluetoothGATTNotifyResponse(
                address=TARGET, handle=request.handle
            )
        elif isinstance(request, BluetoothGATTWriteDescriptorRequest):
            stage = 'cccd'
            response = BluetoothGATTWriteResponse(address=TARGET, handle=request.handle)
        elif isinstance(request, BluetoothGATTGetServicesRequest):
            stage = 'services'
            response = BluetoothGATTGetServicesResponse(address=TARGET)
            for service in metadata().services:

                def uuid_words(value):
                    from uuid import UUID

                    raw = UUID(value).int
                    return [raw >> 64, raw & ((1 << 64) - 1)]

                s = response.services.add(
                    uuid=uuid_words(service.uuid), handle=service.handle
                )
                for char in service.characteristics:
                    c = s.characteristics.add(
                        uuid=uuid_words(char.uuid),
                        handle=char.handle,
                        properties=char.properties,
                    )
                    for desc in char.descriptors:
                        c.descriptors.add(
                            uuid=uuid_words(desc.uuid), handle=desc.handle
                        )
            if self.bad_metadata:
                response.ClearField('services')
        elif isinstance(request, BluetoothDeviceRequest):
            stage = 'disconnect'
            response = BluetoothDeviceConnectionResponse(
                address=TARGET, connected=False
            )
        else:
            raise AssertionError(type(request).__name__)
        if self.hold_at == stage:
            self.entered.set()
            await self.release.wait()
        if stage == 'notify' and self.notify_failure:
            raise self.notify_failure
        return [response]


class WireAPI(APIClient):
    """Keep installed API methods; replace only the external connection."""

    def _get_connection(self):
        return self.wire


@pytest.fixture
async def native(hass, enable_custom_integrations):
    wire = NativeWire()
    api = WireAPI('synthetic.invalid', 6053, None)
    api.wire = wire
    proxy = ESPHomeBluetoothDevice(
        'synthetic',
        SOURCE,
        ble_connections_free=1,
        ble_connections_limit=1,
        available=True,
    )
    runtime = SimpleNamespace(
        client=api,
        available=True,
        bluetooth_device=proxy,
        device_info=DeviceInfo(
            bluetooth_mac_address=SOURCE,
            bluetooth_proxy_feature_flags=int(
                BluetoothProxyFeature.REMOTE_CACHING
                | BluetoothProxyFeature.ACTIVE_CONNECTIONS
                | BluetoothProxyFeature.RAW_ADVERTISEMENTS
            ),
        ),
        api_version=APIVersion(1, 9),
        disconnect_callbacks=set(),
    )
    esp = MockConfigEntry(domain='esphome', title='Synthetic proxy', data={})
    esp.add_to_hass(hass)
    esp.runtime_data = runtime
    esp._async_set_state(hass, ConfigEntryState.LOADED, None)
    watch = MockConfigEntry(
        domain=DOMAIN,
        title='Casio ABL-100WE',
        data={'address': ADDRESS},
        unique_id=ADDRESS,
    )
    watch.add_to_hass(hass)
    device = SimpleNamespace(
        address=ADDRESS, name='ABL-100WE', details={'source': SOURCE}
    )
    with (
        patch(
            'custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
            return_value=device,
        ),
        patch(
            'custom_components.casio_abl100.establish_connection',
            side_effect=RuntimeError('PRIVATE_CONNECT_CANARY'),
        ) as bleak,
    ):
        assert await hass.config_entries.async_setup(watch.entry_id)
        await hass.async_block_till_done()
        yield SimpleNamespace(
            hass=hass,
            wire=wire,
            api=api,
            proxy=proxy,
            runtime=runtime,
            esp=esp,
            watch=watch,
            session=watch.runtime_data,
            device=device,
            bleak=bleak,
        )
        if watch.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(watch.entry_id)
        esp._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        # Synthetic ESPHome runtime was not set up: do not invoke its real
        # removal hooks (unrelated optional voice dependencies) in teardown.


async def spin_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), 'Expected cooperative stage not reached'


async def press(native):
    return await native.hass.services.async_call(
        'button', 'press', {'entity_id': BUTTON}, blocking=True
    )


async def armed(native):
    task = asyncio.create_task(press(native))
    # Fail at the public missing contract, not the old connector exception.
    await spin_until(
        lambda: (
            native.hass.states.get(BUTTON).attributes.get('phase') == 'waiting'
            or task.done()
        )
    )
    if task.done():
        task.exception()
    assert native.hass.states.get(BUTTON).attributes.get('phase') == 'waiting'
    return task


def assert_clean(native):
    assert not native.wire.handlers
    assert not native.runtime.disconnect_callbacks
    assert not native.session.tasks
    assert not native.api._notify_callbacks
    native.bleak.assert_not_called()


@pytest.mark.asyncio
async def test_native_real_button_wait_read_and_existing_publication(native):
    hass = native.hass
    session = native.watch.runtime_data
    ids = {
        e.entity_id
        for e in er.async_get(hass).entities.values()
        if e.config_entry_id == native.watch.entry_id
    }
    assert len(ids) == 11 + 4 + 4 + 4 + 13 and not native.wire.calls
    from tests.ha.test_zone_entities import assert_zone_addition
    assert_zone_addition(native.hass, native.watch)
    task = await armed(native)
    state = hass.states.get(BUTTON)
    expiry = datetime.fromisoformat(state.attributes['wait_expires_at'])
    assert expiry.tzinfo == timezone.utc
    assert 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 360
    assert not task.done() and any(
        BluetoothLERawAdvertisementsResponse in types
        for _, types in native.wire.handlers
    )
    native.wire.raw(address=TARGET - 1)
    await asyncio.sleep(0)
    assert not native.wire.calls
    native.wire.raw()
    await task
    await hass.async_block_till_done()
    assert native.watch.runtime_data is session
    assert hass.states.get('sensor.casio_abl_100we_alarm_1').state == '07:35'
    assert [
        hass.states.get(f'sensor.casio_abl_100we_alarm_{i}').attributes['enabled']
        for i in range(1, 6)
    ] == [True, False, False, False, False]
    assert all(
        hass.states.get(f'sensor.casio_abl_100we_alarm_{i}').attributes['fresh']
        for i in range(1, 6)
    )
    assert hass.states.get('sensor.casio_abl_100we_steps_today').state == 'unavailable'
    assert session.last_sync is None and not session.fresh
    assert hass.states.get(BUTTON).attributes['phase'] == 'idle'
    assert hass.states.get(BUTTON).attributes['wait_expires_at'] is None
    assert native.api.wire is native.wire
    requests = native.wire.calls
    assert [type(x).__name__ for x in requests] == [
        'BluetoothDeviceRequest',
        'BluetoothGATTGetServicesRequest',
        'BluetoothGATTNotifyRequest',
        'BluetoothGATTWriteDescriptorRequest',
        'BluetoothGATTWriteRequest',
        'BluetoothGATTWriteRequest',
        'BluetoothDeviceRequest',
    ]
    assert requests[0].address_type == 1 and requests[0].has_address_type
    assert requests[3].data == b'\x01\x00'
    assert [x.data for x in requests if isinstance(x, BluetoothGATTWriteRequest)] == [
        b'\x15',
        b'\x16',
    ]
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['negative', 'error', 'metadata', 'frame'])
async def test_native_failed_refresh_retains_last_good_and_safe_error(
    native, failure, caplog
):
    task = await armed(native)
    native.wire.raw()
    await task
    previous = native.watch.runtime_data.alarm_snapshot
    stamp = native.watch.runtime_data.alarm_last_sync
    native.wire.calls.clear()
    if failure == 'negative':
        native.wire.connected = False
    elif failure == 'error':
        native.wire.error = 133
    elif failure == 'metadata':
        native.wire.bad_metadata = True
    else:
        native.wire.bad = True
    task = await armed(native)
    native.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    await native.hass.async_block_till_done()
    session = native.watch.runtime_data
    assert session.alarm_snapshot is previous and session.alarm_last_sync == stamp
    assert not session.alarms_fresh
    assert (
        native.hass.states.get('sensor.casio_abl_100we_alarm_1').attributes['fresh']
        is False
    )
    if failure in ('negative', 'error'):
        assert not any(
            isinstance(x, BluetoothGATTGetServicesRequest) for x in native.wire.calls
        )
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert ADDRESS not in str(error.value.args) + caplog.text
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'stage',
    ['waiting', 'connect', 'services', 'notify', 'cccd', 'response', 'disconnect'],
)
async def test_cancellation_at_native_boundaries_joins_and_removes_owned_callbacks(
    native, stage
):
    task = await armed(native)
    if stage != 'waiting':
        native.wire.hold_at = stage
        native.wire.raw()
        await asyncio.wait_for(native.wire.entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    if stage in ('notify', 'disconnect'):
        # Acquisition and cleanup must finish despite repeated caller cancel.
        task.cancel()
        native.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await native.hass.async_block_till_done()
    assert native.watch.runtime_data.alarm_snapshot is None
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'stage', ['waiting', 'connect', 'notify', 'cccd', 'response', 'disconnect']
)
async def test_replacement_generation_never_receives_cancel_cleanup(native, stage):
    task = await armed(native)
    if stage != 'waiting':
        native.wire.hold_at = stage
        native.wire.raw()
        await asyncio.wait_for(native.wire.entered.wait(), 2)
    replacement = NativeWire()
    native.api.wire = replacement
    for callback in tuple(native.runtime.disconnect_callbacks):
        callback()
    if stage in ('notify', 'disconnect'):
        native.wire.release.set()
    with pytest.raises((HomeAssistantError, asyncio.CancelledError)):
        await task
    await native.hass.async_block_till_done()
    assert not replacement.calls and not replacement.handlers
    assert native.watch.runtime_data.alarm_snapshot is None
    assert_clean(native)


@pytest.mark.asyncio
async def test_wait_expiry_is_finite_and_not_ready_after_close(native):
    from custom_components.casio_abl100 import native_alarms

    with patch.object(native_alarms, 'WAIT_SECONDS', 0.01):
        task = await armed(native)
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(task, 2)
    await native.hass.async_block_till_done()
    assert not native.wire.calls
    assert native.hass.states.get(BUTTON).attributes['phase'] == 'idle'
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode',
    [
        'missing_source',
        'ambiguous',
        'unavailable',
        'allocated',
        'tracked',
        'no_capacity',
        'no_source_single',
    ],
)
async def test_proxy_selection_and_ownership_refuse_or_wait_without_gatt(native, mode):
    extra = None
    if mode == 'missing_source':
        native.device.details = {'source': '00:00:00:00:00:00'}
    elif mode in ('ambiguous', 'no_source_single'):
        native.device.details = {}
        if mode == 'ambiguous':
            extra = MockConfigEntry(domain='esphome', data={})
            extra.add_to_hass(native.hass)
            extra.runtime_data = SimpleNamespace(**vars(native.runtime))
            extra._async_set_state(native.hass, ConfigEntryState.LOADED, None)
    elif mode == 'unavailable':
        native.runtime.available = False
    elif mode == 'allocated':
        native.proxy.ble_allocations = [TARGET]
    elif mode == 'tracked':
        native.proxy._tracked_clients[TARGET] = lambda: None
    else:
        native.proxy.ble_connections_free = 0
    try:
        if mode == 'no_source_single':
            task = await armed(native)
            assert not native.wire.calls
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(HomeAssistantError):
                await press(native)
        assert not native.wire.calls
        assert_clean(native)
    finally:
        if extra:
            extra._async_set_state(native.hass, ConfigEntryState.NOT_LOADED, None)


@pytest.mark.asyncio
async def test_duplicate_reads_owned_before_lock_and_unload_joins_both(native):
    task = await armed(native)
    queued = asyncio.create_task(press(native))
    await spin_until(lambda: len(native.watch.runtime_data.tasks) == 2)
    assert len(native.wire.handlers) == 1 and not native.wire.calls
    session = native.watch.runtime_data
    assert await native.hass.config_entries.async_unload(native.watch.entry_id)
    for request in (task, queued):
        with pytest.raises(asyncio.CancelledError):
            await request
    assert session.closed and not session.listeners and not session.lock.locked()
    assert session.alarm_phase == 'closed' and session.alarm_wait_expires_at is None
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'error', 'backend_cancel'])
async def test_installed_notify_acquisition_window_is_joined_on_caller_cancel(
    native, outcome
):
    """Runs exact installed APIClient method after its local registration."""
    task = await armed(native)
    native.wire.hold_at = 'notify'
    native.wire.raw()
    await asyncio.wait_for(native.wire.entered.wait(), 2)
    assert any(
        BluetoothGATTNotifyDataResponse in types for _, types in native.wire.handlers
    )
    if outcome == 'error':
        native.wire.notify_failure = RuntimeError('PRIVATE_NOTIFY_CANARY')
    elif outcome == 'backend_cancel':
        native.wire.notify_failure = asyncio.CancelledError()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    native.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_clean(native)


@pytest.mark.asyncio
async def test_unload_during_link_read_disconnects_owned_watch_before_join(native):
    task = await armed(native)
    native.wire.hold_at = 'response'
    native.wire.raw()
    await asyncio.wait_for(native.wire.entered.wait(), 2)
    session = native.session
    assert await native.hass.config_entries.async_unload(native.watch.entry_id)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any(
        isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1
        for x in native.wire.calls
    ), 'Unload must release its original BLE link'
    assert session.closed and not session.tasks and not session.listeners
    assert_clean(native)


@pytest.mark.asyncio
async def test_generation_swap_without_callback_during_disconnect_never_commits(native):
    task = await armed(native)
    native.wire.hold_at = 'disconnect'
    native.wire.raw()
    await asyncio.wait_for(native.wire.entered.wait(), 2)
    replacement = NativeWire()
    native.api.wire = replacement
    native.wire.release.set()
    with pytest.raises(HomeAssistantError):
        await task
    assert native.session.alarm_snapshot is None
    assert not replacement.calls and not replacement.handlers
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode', ['identity', 'no_active', 'no_raw', 'incompatible', 'watch_unloading']
)
async def test_readiness_requires_exact_identity_capability_and_loaded_owner(
    native, mode
):
    if mode == 'identity':
        native.runtime.device_info = DeviceInfo(
            bluetooth_mac_address='00:00:00:00:00:00',
            bluetooth_proxy_feature_flags=int(BluetoothProxyFeature.REMOTE_CACHING),
        )
    elif mode == 'watch_unloading':
        native.watch._async_set_state(
            native.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None
        )
    else:
        flags = int(
            BluetoothProxyFeature.REMOTE_CACHING
            | BluetoothProxyFeature.ACTIVE_CONNECTIONS
            | BluetoothProxyFeature.RAW_ADVERTISEMENTS
        )
        flag = {
            'no_active': BluetoothProxyFeature.ACTIVE_CONNECTIONS,
            'no_raw': BluetoothProxyFeature.RAW_ADVERTISEMENTS,
            'incompatible': BluetoothProxyFeature.REMOTE_CACHING,
        }[mode]
        native.runtime.device_info = DeviceInfo(
            bluetooth_mac_address=SOURCE,
            bluetooth_proxy_feature_flags=flags & ~int(flag),
        )
    try:
        task = asyncio.create_task(press(native))
        await spin_until(lambda: task.done() or native.session.alarm_phase == 'waiting')
        assert native.session.alarm_phase != 'waiting', (
            'Must not advertise READY without authority'
        )
        with pytest.raises(HomeAssistantError):
            await task
        assert not native.wire.calls
        assert_clean(native)
    finally:
        if mode == 'watch_unloading':
            native.watch._async_set_state(native.hass, ConfigEntryState.LOADED, None)


@pytest.mark.asyncio
async def test_cancel_queued_read_does_not_clear_active_ready(native):
    task = await armed(native)
    queued = asyncio.create_task(press(native))
    await spin_until(lambda: len(native.session.tasks) == 2)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    await asyncio.sleep(0)
    assert native.session.alarm_phase == 'waiting'
    assert native.session.alarm_wait_expires_at is not None
    native.wire.raw()
    await task
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize('address_type', [0, 1, 2, 255])
async def test_only_fresh_raw_valid_address_type_can_connect(native, address_type):
    task = await armed(native)
    native.wire.raw(address_type=address_type)
    if address_type in (0, 1):
        await task
        assert native.wire.calls[0].address_type == address_type
    else:
        with pytest.raises(HomeAssistantError):
            await task
        assert not native.wire.calls
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'component',
    ['runtime', 'client', 'callbacks', 'connection', 'unavailable', 'capacity'],
)
async def test_wait_periodically_revalidates_generation_and_capacity(native, component):
    from custom_components.casio_abl100 import native_alarms

    with patch.object(native_alarms, 'WAIT_SECONDS', 0.03):
        task = await armed(native)
        if component == 'runtime':
            native.esp.runtime_data = SimpleNamespace(**vars(native.runtime))
        elif component == 'client':
            native.runtime.client = WireAPI('synthetic.invalid', 6053, None)
        elif component == 'callbacks':
            native.runtime.disconnect_callbacks = set()
        elif component == 'connection':
            native.api.wire = NativeWire()
        elif component == 'unavailable':
            native.runtime.available = False
        else:
            native.proxy.ble_connections_free = 0
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(task, 2)
    assert native.session.alarm_phase == 'idle'
    assert not native.wire.calls
    assert_clean(native)


@pytest.mark.asyncio
async def test_shared_callbacks_and_notification_map_remain_other_owners(native):
    def observer(_msg):
        pass

    remove = native.wire.add_message_callback(
        observer, (BluetoothGATTNotifyDataResponse,)
    )

    def unrelated():
        pass

    native.runtime.disconnect_callbacks.add(unrelated)
    native.api._notify_callbacks[(TARGET - 1, 61)] = unrelated
    task = await armed(native)
    native.wire.raw()
    await task
    assert native.wire.handlers == {(observer, (BluetoothGATTNotifyDataResponse,))}
    assert native.runtime.disconnect_callbacks == {unrelated}
    assert native.api._notify_callbacks == {(TARGET - 1, 61): unrelated}
    remove()
    native.runtime.disconnect_callbacks.clear()
    native.api._notify_callbacks.clear()
    assert_clean(native)


@pytest.mark.asyncio
async def test_non_esphome_source_keeps_existing_bleak_route(native):
    from tests.protocol.test_alarm_transport import Client

    native.device.details = {'source': '22:22:22:22:22:22'}
    scanner = SimpleNamespace(connector=SimpleNamespace(client=lambda: None))
    native.bleak.side_effect = None
    native.bleak.return_value = Client()
    with patch(
        'homeassistant.components.bluetooth.async_scanner_by_source',
        return_value=scanner,
    ):
        await press(native)
    assert native.bleak.call_count == 1 and not native.wire.calls
    assert native.session.alarms_fresh


@pytest.mark.asyncio
async def test_capacity_loss_clears_ready_before_whole_wait_deadline(native):
    task = await armed(native)
    native.proxy.ble_connections_free = 0
    try:
        # Periodic revalidation is at most one second, not the six-minute expiry.
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(asyncio.shield(task), 1.5)
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert native.session.alarm_phase == 'idle'
    assert_clean(native)


@pytest.mark.asyncio
async def test_entry_close_between_notify_scheduling_and_child_entry_never_submits(
    native,
):
    from custom_components.casio_abl100 import native_api

    original = native_api.joined
    called = False

    async def close_before_child(operation, acquired=None):
        nonlocal called
        if not called:
            called = True
            native.session.closed = True
        return await original(operation, acquired)

    with patch(
        'custom_components.casio_abl100.native_alarm_adapter.joined',
        side_effect=close_before_child,
    ):
        task = await armed(native)
        native.wire.raw()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not any(isinstance(x, BluetoothGATTNotifyRequest) for x in native.wire.calls)
    assert any(
        isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1
        for x in native.wire.calls
    )
    assert_clean(native)


@pytest.mark.asyncio
async def test_cold_start_without_cached_ble_device_waits_for_exact_raw(native):
    with patch(
        'custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
        return_value=None,
    ):
        task = await armed(native)
        assert not native.wire.calls
        native.wire.raw()
        await task
    assert native.session.alarms_fresh
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['steps', 'alarms'])
async def test_native_alarm_and_step_service_share_existing_lock_in_both_orders(
    native, first
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def step_record(client, timeout):
        from datetime import date
        from custom_components.casio_abl100.protocol import StepRecord

        entered.set()
        await release.wait()
        return StepRecord(date(2026, 10, 18), 42)

    async def steps():
        await native.hass.services.async_call(
            'button',
            'press',
            {'entity_id': 'button.casio_abl_100we_read_steps'},
            blocking=True,
        )

    native.bleak.side_effect = None
    native.bleak.return_value = SimpleNamespace()
    # Preserve this historical native-alarm/non-native-step lock fixture.
    # Both fully native orders are exercised by test_native_step_runtime.
    selections = [None, native.esp] if first == 'steps' else [native.esp, None]
    with (
        patch('custom_components.casio_abl100.native_alarms.select_proxy', side_effect=selections),
        patch('custom_components.casio_abl100.read_record', side_effect=step_record),
        patch(
            'custom_components.casio_abl100.dt_util.now',
            return_value=datetime(2026, 10, 18),
        ),
    ):
        if first == 'steps':
            step_task = asyncio.create_task(steps())
            await asyncio.wait_for(entered.wait(), 2)
            alarm_task = asyncio.create_task(press(native))
            await spin_until(lambda: len(native.session.tasks) == 2)
            assert not native.wire.handlers and not native.wire.calls
            release.set()
            await step_task
            await spin_until(lambda: native.session.alarm_phase == 'waiting')
            native.wire.raw()
            await alarm_task
        else:
            alarm_task = await armed(native)
            step_task = asyncio.create_task(steps())
            await spin_until(lambda: len(native.session.tasks) == 2)
            assert native.bleak.call_count == 0
            native.wire.raw()
            await alarm_task
            await asyncio.wait_for(entered.wait(), 2)
            release.set()
            await step_task
    assert native.session.fresh and native.session.alarms_fresh
    assert native.session.record.steps == 42
    assert not native.session.lock.locked() and not native.session.tasks
    assert native.bleak.call_count == 1
    assert not native.wire.handlers and not native.runtime.disconnect_callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['timeout', 'ordinary', 'backend_cancel'])
async def test_notify_backend_failure_is_sanitized_without_leaked_registration(
    native, fault, caplog
):
    native.wire.notify_failure = {
        'timeout': TimeoutError('PRIVATE_NOTIFY_TIMEOUT'),
        'ordinary': RuntimeError('PRIVATE_NOTIFY_FAILURE'),
        'backend_cancel': asyncio.CancelledError(),
    }[fault]
    task = await armed(native)
    native.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert native.session.alarm_snapshot is None
    assert not any(isinstance(x, BluetoothGATTWriteRequest) for x in native.wire.calls)
    assert_clean(native)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['timeout', 'ordinary', 'backend_cancel'])
async def test_disconnect_backend_failure_withholds_valid_snapshot(
    native, fault, caplog
):
    original = native.wire.send_messages_await_response_complex

    async def fault_disconnect(requests, *args):
        if isinstance(requests[0], BluetoothDeviceRequest):
            native.wire.calls.append(requests[0])
            raise {
                'timeout': TimeoutError('PRIVATE_DISCONNECT_TIMEOUT'),
                'ordinary': RuntimeError('PRIVATE_DISCONNECT_FAILURE'),
                'backend_cancel': asyncio.CancelledError(),
            }[fault]
        return await original(requests, *args)

    native.wire.send_messages_await_response_complex = fault_disconnect
    task = await armed(native)
    native.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert native.session.alarm_snapshot is None
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert_clean(native)


@pytest.mark.asyncio
async def test_close_before_services_child_enters_never_submits_gatt(native):
    from custom_components.casio_abl100.native_api import NativeAPI

    original = NativeAPI.bluetooth_gatt_get_services

    async def close_before_services(api, address):
        native.session.closed = True
        return await original(api, address)

    with patch.object(NativeAPI, 'bluetooth_gatt_get_services', close_before_services):
        task = await armed(native)
        native.wire.raw()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not any(
        isinstance(x, BluetoothGATTGetServicesRequest) for x in native.wire.calls
    )
    assert any(
        isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1
        for x in native.wire.calls
    )
    assert_clean(native)


@pytest.mark.asyncio
async def test_caller_cancel_pending_before_notify_child_does_not_submit(native):
    from custom_components.casio_abl100 import native_api

    original = native_api.joined
    called = False

    async def cancel_before_child(operation, acquired=None):
        nonlocal called
        if not called:
            called = True
            asyncio.current_task().cancel()
        return await original(operation, acquired)

    with patch(
        'custom_components.casio_abl100.native_alarm_adapter.joined',
        side_effect=cancel_before_child,
    ):
        task = await armed(native)
        native.wire.raw()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not any(isinstance(x, BluetoothGATTNotifyRequest) for x in native.wire.calls)
    assert_clean(native)
