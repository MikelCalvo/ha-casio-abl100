"""Borrow installed native BLE methods on one captured API generation.

No APIClient is constructed, copied or modified. The facade owns only its BLE
callbacks; even the vendor method's cancellation cleanup cannot resolve a new
HA connection or clear another client's notification map.
"""

import asyncio
from aioesphomeapi import APIClient
from aioesphomeapi.api_pb2 import BluetoothDeviceRequest
from aioesphomeapi.model import BluetoothDeviceRequestType


async def joined(operation, acquired=None):
    """Shield and JOIN a backend-bounded operation, retrieving every outcome.

    wait() never cancels the child and removes its waiter on cancellation, so
    there is no orphan shield future. Backend timeouts supply the finite bound;
    this does not force an indefinitely uncooperative backend to terminate.
    """
    owner = asyncio.current_task()
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            cancelled = True
    try:
        result = task.result()
        if acquired is not None:
            acquired(result)
    except BaseException:
        if cancelled or (owner is not None and owner.cancelling()):
            raise asyncio.CancelledError from None
        raise
    if cancelled or (owner is not None and owner.cancelling()):
        raise asyncio.CancelledError
    return result


class _Connection:
    """Guard submissions and retain only this operation's original handlers."""

    def __init__(self, connection, guard):
        self.connection = connection
        self.guard = guard
        self.removers = set()

    def _own(self, remove):
        self.removers.add(remove)

        def owned_remove():
            if remove in self.removers:
                self.removers.discard(remove)
                remove()

        return owned_remove

    def add_message_callback(self, *args):
        self.guard()
        return self._own(self.connection.add_message_callback(*args))

    def send_message_callback_response(self, request, *args):
        self.guard(request)
        return self._own(self.connection.send_message_callback_response(request, *args))

    def send_message(self, request):
        self.guard(request)
        return self.connection.send_message(request)

    async def send_messages_await_response_complex(self, requests, *args):
        for request in requests:
            self.guard(request)
        return await self.connection.send_messages_await_response_complex(
            requests, *args
        )

    def close(self):
        for remove in tuple(self.removers):
            try:
                remove()
            finally:
                self.removers.discard(remove)


class NativeAPI:
    """Fixed-generation method facade, not a second network API client."""

    bluetooth_device_connect = APIClient.bluetooth_device_connect
    bluetooth_device_disconnect = APIClient.bluetooth_device_disconnect
    _bluetooth_device_disconnect_guard_timeout = (
        APIClient._bluetooth_device_disconnect_guard_timeout
    )
    _bluetooth_disconnect_no_wait = APIClient._bluetooth_disconnect_no_wait
    _bluetooth_device_request = APIClient._bluetooth_device_request
    bluetooth_gatt_get_services = APIClient.bluetooth_gatt_get_services
    _raise_for_ble_connection_change = APIClient._raise_for_ble_connection_change
    bluetooth_gatt_start_notify = APIClient.bluetooth_gatt_start_notify
    bluetooth_gatt_stop_notify_for_address = (
        APIClient.bluetooth_gatt_stop_notify_for_address
    )
    _send_bluetooth_message_await_response = (
        APIClient._send_bluetooth_message_await_response
    )
    bluetooth_gatt_write = APIClient.bluetooth_gatt_write
    bluetooth_gatt_write_descriptor = APIClient.bluetooth_gatt_write_descriptor
    _bluetooth_gatt_write = APIClient._bluetooth_gatt_write

    def __init__(self, connection, valid, operational=None):
        self._loop = asyncio.get_running_loop()
        self._debug_enabled = False
        self.log_name = 'Native alarm session'
        self._notify_callbacks = {}
        self.valid = valid
        self.operational = operational or valid
        self.connection = _Connection(connection, self.guard)

    def guard(self, request=None):
        if not self.valid():
            raise RuntimeError('Native API generation unavailable')
        cleanup = (
            isinstance(request, BluetoothDeviceRequest)
            and request.request_type == BluetoothDeviceRequestType.DISCONNECT
        )
        if request is not None and not cleanup and not self.operational():
            raise RuntimeError('Native operation no longer active')

    def _get_connection(self):
        self.guard()
        return self.connection

    def close(self):
        self.connection.close()
        self._notify_callbacks.clear()
