"""Zone-only authorization over the accepted finite acquisition/cleanup."""
from .native_alarm_adapter import NativeAlarmAdapter, FEATURES, _unique, _uuid


class NativeZoneAdapter(NativeAlarmAdapter):
    """Never permit inherited alarm writes or own HA's shared connection."""

    def __init__(self, api, address, metadata, connected, cleanup_allowed=None):
        super().__init__(api, address, metadata, connected, cleanup_allowed)
        service = _unique(s for s in metadata.services if _uuid(s.uuid) == FEATURES)
        if (type(service.handle) is not int or not 0 < service.handle <= 65535
                or service.handle in (self.primary.handle, self.query.handle, self.cccd)):
            raise ValueError('Invalid zone service handle')
        self.query_index = 0
        self.ready = False
        self.closing = False

    def guard(self):
        super().guard()
        if self.closing:
            raise RuntimeError('Zone link closing')

    async def start_notify(self, char, callback):
        def notify(characteristic, data):
            if not self.disconnected:
                callback(characteristic, data)
        await super().start_notify(char, notify)
        self.guard()
        self.ready = True

    async def write_gatt_char(self, char, data, response):
        self.guard()
        queries = (b'\x1d\x00', b'\x1e\x00', b'\x1e\x01')
        if (char is not self.query or response is not False or not self.ready
                or self.query_index >= len(queries)
                or not isinstance(data, (bytes, bytearray, memoryview))
                or bytes(data) != queries[self.query_index]):
            raise ValueError('Only ordered subscribed zone GET is allowed')
        query = bytes(data)
        self.query_index += 1
        # Called inside the transport's scheduled child; no intervening await
        # between this ownership/allowlist check and installed public API entry.
        await self.api.bluetooth_gatt_write(self.address, char.handle, query, False, 10)
        self.guard()

    def remove_notifications(self):
        if self.remove_notify is not None:
            remove, self.remove_notify = self.remove_notify, None
            remove()

    async def disconnect(self):
        self.closing = True
        await super().disconnect()
