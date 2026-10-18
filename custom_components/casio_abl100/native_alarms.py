"""One manually armed alarm read on HA's existing ESPHome connection."""

import asyncio
from datetime import timedelta

from aioesphomeapi import BluetoothProxyFeature
from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisementsResponse
from homeassistant.components import bluetooth
from homeassistant.util import dt as dt_util

from .native_api import NativeAPI, joined
from .native_alarm_adapter import NativeAlarmAdapter
from .transport import read_alarms

WAIT_SECONDS = 360


def _loaded(entry):
    return entry.state.value == 'loaded'


def _usable(entry):
    if not _loaded(entry):
        return False
    runtime = getattr(entry, 'runtime_data', None)
    proxy = getattr(runtime, 'bluetooth_device', None)
    if not (
        runtime
        and runtime.available
        and proxy
        and proxy.available
        and proxy.ble_connections_limit > 0
        and runtime.device_info
    ):
        return False
    info = runtime.device_info
    source = info.bluetooth_mac_address or info.mac_address
    flags = info.bluetooth_proxy_feature_flags_compat(runtime.api_version)
    required = (
        BluetoothProxyFeature.ACTIVE_CONNECTIONS
        | BluetoothProxyFeature.REMOTE_CACHING
        | BluetoothProxyFeature.RAW_ADVERTISEMENTS
    )
    return source == proxy.mac_address and flags & required == required


def select_proxy(hass, device):
    """Exact source selection; cold start permits one unambiguous proxy only.

    Non-ESPHome devices retain Bleak. A source matching a known but unavailable
    ESPHome entry fails closed instead of being handed to another scanner.
    """
    entries = hass.config_entries.async_entries('esphome')
    details = getattr(device, 'details', None)
    source = details.get('source') if isinstance(details, dict) else None
    if source is not None:
        matches = [
            entry
            for entry in entries
            if (
                proxy := getattr(
                    getattr(entry, 'runtime_data', None), 'bluetooth_device', None
                )
            )
            and proxy.mac_address == source
        ]
        if not matches:
            scanner = bluetooth.async_scanner_by_source(hass, source)
            if scanner is not None:
                from functools import partial
                from bleak_esphome.backend.client import ESPHomeClient

                connector = scanner.connector
                factory = connector.client if connector is not None else None
                while isinstance(factory, partial):
                    factory = factory.func
                if factory is not ESPHomeClient:
                    return None  # Positively identified non-ESPHome scanner.
            raise ValueError('Selected native source unavailable')
        if len(matches) != 1 or not _usable(matches[0]):
            raise ValueError('Selected native proxy unavailable')
        return matches[0]
    if device is not None and (not isinstance(details, dict) or details):
        return None  # A non-remote Bleak device with backend metadata.
    candidates = [entry for entry in entries if _usable(entry)]
    if len(candidates) != 1:
        raise ValueError('Native proxy selection ambiguous or unavailable')
    return candidates[0]


class NativeAlarmRead:
    """Borrow a captured live proxy generation; own only local callbacks/link."""

    def __init__(self, session, entry):
        self.session = session
        self.hass = session.hass
        self.entry = entry
        self.runtime = entry.runtime_data
        self.client = self.runtime.client
        self.connection = self.client._get_connection()
        self.callbacks = self.runtime.disconnect_callbacks
        self.proxy = self.runtime.bluetooth_device
        self.target = int(session.address.replace(':', ''), 16)
        self.lost = False
        self.owner = asyncio.current_task()
        self.link = False
        self.attempted = False
        self.remove_connection = None
        self.adapter = None
        self.remove_raw = None
        self.timer = None
        self.ready = None
        self.api = NativeAPI(self.connection, self.generation_valid, self.valid)

    def valid(self):
        owner = self.session.entry
        return (
            not self.session.closed
            and not self.owner.cancelling()
            and _loaded(owner)
            and owner.runtime_data is self.session
            and self.generation_valid()
        )

    def generation_valid(self):
        try:
            return (
                not self.lost
                and _usable(self.entry)
                and self.entry.runtime_data is self.runtime
                and self.runtime.client is self.client
                and self.runtime.bluetooth_device is self.proxy
                and self.runtime.disconnect_callbacks is self.callbacks
                and self.client._get_connection() is self.connection
            )
        except Exception:
            return False

    def guard(self):
        if not self.valid():
            raise RuntimeError('Native proxy generation changed')

    def free(self):
        self.guard()
        if (
            self.proxy.ble_connections_free <= 0
            or self.target in self.proxy.ble_allocations
            or self.target in self.proxy._tracked_clients
        ):
            raise ValueError('Native target busy')

    def invalidate(self):
        self.lost = True
        self.session.set_alarm_phase('idle')
        self.owner.cancel()

    async def wait(self):
        self.free()
        loop = asyncio.get_running_loop()
        self.ready = loop.create_future()
        deadline = loop.time() + WAIT_SECONDS
        expiry = dt_util.utcnow() + timedelta(seconds=WAIT_SECONDS)

        def raw(message):
            if self.ready.done():
                return
            if not self.valid() or loop.time() >= deadline:
                self.ready.set_exception(RuntimeError('Native wait invalidated'))
                return
            for adv in message.advertisements:
                if adv.address == self.target:
                    if adv.address_type not in (0, 1):
                        self.ready.set_exception(ValueError('Invalid address type'))
                    else:
                        self.ready.set_result(adv.address_type)
                    return

        def tick():
            self.timer = None
            if self.ready.done():
                return
            try:
                self.free()
                if loop.time() >= deadline:
                    raise TimeoutError('Native wait ended')
            except Exception:
                self.session.set_alarm_phase('idle')
                self.ready.set_exception(TimeoutError('Native wait ended'))
                return
            self.timer = loop.call_later(min(1, deadline - loop.time()), tick)

        try:
            self.remove_raw = self.connection.add_message_callback(
                raw, (BluetoothLERawAdvertisementsResponse,)
            )
            self.timer = loop.call_later(min(1, WAIT_SECONDS), tick)
            self.free()
            self.session.set_alarm_phase('waiting', expiry)
            result = await self.ready
            self.free()
            if loop.time() >= deadline:
                raise TimeoutError('Native wait ended')
            return result
        finally:
            if self.remove_raw:
                remove, self.remove_raw = self.remove_raw, None
                remove()
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if not self.ready.done():
                self.ready.cancel()

    async def read(self):
        self.callbacks.add(self.invalidate)
        try:
            address_type = await self.wait()
            self.session.set_alarm_phase('reading')
            self.free()
            self.attempted = True

            def state(connected, _mtu, error):
                self.link = connected is True and error == 0

            flags = self.runtime.device_info.bluetooth_proxy_feature_flags_compat(
                self.runtime.api_version
            )
            if not flags & BluetoothProxyFeature.REMOTE_CACHING:
                raise ValueError('Native proxy incompatible')
            self.remove_connection = await self.api.bluetooth_device_connect(
                self.target,
                state,
                timeout=30,
                disconnect_timeout=5,
                has_cache=False,
                feature_flags=flags,
                address_type=address_type,
            )
            self.guard()
            if not self.link:
                raise RuntimeError('Native connection not confirmed')
            metadata = await asyncio.wait_for(
                self.api.bluetooth_gatt_get_services(self.target), 10
            )
            self.guard()
            self.adapter = NativeAlarmAdapter(
                self.api,
                self.target,
                metadata,
                lambda: self.valid() and self.link,
                self.generation_valid,
            )
            snapshot = await read_alarms(self.adapter, timeout=12)
            self.guard()
            return snapshot
        finally:
            try:
                if (
                    self.attempted
                    and self.generation_valid()
                    and not (self.adapter and self.adapter.disconnected)
                ):
                    await joined(
                        self.api.bluetooth_device_disconnect(self.target, timeout=5)
                    )
            finally:
                if self.adapter and self.adapter.remove_notify:
                    self.adapter.remove_notify()
                    self.adapter.remove_notify = None
                if self.remove_connection:
                    self.remove_connection()
                    self.remove_connection = None
                self.callbacks.discard(self.invalidate)
                self.api.close()
