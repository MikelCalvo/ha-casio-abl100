"""Narrow native-API bridge for the existing read-only alarm transport."""

from types import SimpleNamespace
from uuid import UUID
from .native_api import joined

FEATURES = '26eb000d-b012-49a8-b1f8-394fb2032b0f'
PRIMARY = '26eb002d-b012-49a8-b1f8-394fb2032b0f'
QUERY = '26eb002c-b012-49a8-b1f8-394fb2032b0f'
CCCD = '00002902-0000-1000-8000-00805f9b34fb'


def _unique(items):
    items = list(items)
    if len(items) != 1:
        raise ValueError('Missing or ambiguous GATT metadata')
    return items[0]


def _uuid(value):
    return str(UUID(str(value)))


class NativeAlarmAdapter:
    """Borrow an established BLE link, never the shared API connection itself."""

    def __init__(self, api, address, metadata, connected, cleanup_allowed=None):
        self.api = api
        self.cleanup_allowed = cleanup_allowed or connected
        self.address = address
        self.connected = connected
        self.disconnected = False
        self.remove_notify = None
        self.notify_started = False
        self.operations = []
        service = _unique(s for s in metadata.services if _uuid(s.uuid) == FEATURES)
        primary = _unique(
            c for c in service.characteristics if _uuid(c.uuid) == PRIMARY
        )
        query = _unique(c for c in service.characteristics if _uuid(c.uuid) == QUERY)
        desc = _unique(d for d in primary.descriptors if _uuid(d.uuid) == CCCD)
        if not (primary.properties & 0x10 and query.properties & 0x04):
            raise ValueError('Unsupported GATT properties')
        handles = [primary.handle, query.handle, desc.handle]
        if (
            any(type(x) is not int or not 0 < x <= 65535 for x in handles)
            or len(set(handles)) != 3
        ):
            raise ValueError('Invalid GATT handles')
        self.primary = SimpleNamespace(
            uuid=PRIMARY,
            handle=primary.handle,
            properties=['notify'],
            descriptors=[SimpleNamespace(uuid=CCCD, handle=desc.handle)],
        )
        self.query = SimpleNamespace(
            uuid=QUERY,
            handle=query.handle,
            properties=['write-without-response'],
            descriptors=[],
        )
        self.cccd = desc.handle
        resolved = SimpleNamespace(
            uuid=FEATURES,
            characteristics=[self.primary, self.query],
        )
        self.services = SimpleNamespace(
            get_service=lambda key: resolved if key.lower() == FEATURES else None,
        )

    def guard(self):
        if self.disconnected or not self.connected():
            raise RuntimeError('Native link unavailable')

    async def start_notify(self, char, callback):
        self.guard()
        if char is not self.primary or self.notify_started:
            raise ValueError('Unexpected subscription')
        self.notify_started = True
        self.operations.append('notify_submit')

        def acquired(result):
            _stop, self.remove_notify = result

        async def acquire():
            # joined() starts a child on the next turn. Revalidate immediately
            # at its public invocation, not only before scheduling that child.
            self.guard()
            return await self.api.bluetooth_gatt_start_notify(
                self.address,
                char.handle,
                lambda handle, data: callback(char, data),
                10,
            )

        await joined(acquire(), acquired)
        self.guard()
        self.operations.append('cccd_submit')
        await self.api.bluetooth_gatt_write_descriptor(
            self.address,
            self.cccd,
            b'\x01\x00',
            10,
        )
        self.operations.append('notify_ready')

    async def write_gatt_char(self, char, data, response):
        self.guard()
        if (
            char is not self.query
            or response is not False
            or bytes(data) not in (b'\x15', b'\x16')
        ):
            raise ValueError('Only alarm GET is allowed')
        self.operations.append('GET15' if bytes(data) == b'\x15' else 'GET16')
        await self.api.bluetooth_gatt_write(
            self.address,
            char.handle,
            bytes(data),
            False,
            10,
        )

    async def disconnect(self):
        if self.disconnected:
            return
        try:
            self.operations.append('disconnect_submit')

            def confirmed(_result):
                self.disconnected = True
                self.operations.append('disconnect_confirmed')

            if self.cleanup_allowed():
                await joined(
                    self.api.bluetooth_device_disconnect(self.address, timeout=5),
                    confirmed,
                )
            else:
                self.disconnected = True
        finally:
            if self.remove_notify is not None:
                remove, self.remove_notify = self.remove_notify, None
                remove()
