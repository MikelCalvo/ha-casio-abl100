"""Allowlisted, metadata-resolved bridge for the existing step transport."""
from types import SimpleNamespace

from .native_api import joined
from .native_alarm_adapter import CCCD, _unique, _uuid
from .transport import ALL_FEATURES, CONVOY, DRSP, FEATURES_SERVICE, START


class NativeStepAdapter:
    """Own three finite subscriptions, never HA's shared API connection."""

    def __init__(self, api, address, metadata, connected, cleanup_allowed=None):
        self.api = api
        self.address = address
        self.connected = connected
        self.cleanup_allowed = cleanup_allowed or connected
        self.disconnected = False
        self.closing = False
        self.invalid = False
        self.started = False
        self.removers = []
        self.subscriptions = set()
        self.ready = set()
        self.chars = {}
        service = _unique(s for s in metadata.services if _uuid(s.uuid) == FEATURES_SERVICE)
        handles = [service.handle]
        for uuid in (ALL_FEATURES, DRSP, CONVOY):
            char = _unique(c for c in service.characteristics if _uuid(c.uuid) == uuid)
            desc = _unique(d for d in char.descriptors if _uuid(d.uuid) == CCCD)
            if not (char.properties & 0x10) or (uuid == DRSP and not char.properties & 0x08):
                raise ValueError('Unsupported step GATT properties')
            handles.extend((char.handle, desc.handle))
            self.chars[uuid] = SimpleNamespace(
                uuid=uuid, handle=char.handle, properties=['notify'],
                descriptors=[SimpleNamespace(uuid=CCCD, handle=desc.handle)],
            )
        if (any(type(h) is not int or not 0 < h <= 65535 for h in handles)
                or len(set(handles)) != len(handles)):
            raise ValueError('Invalid step GATT handles')
        resolved = SimpleNamespace(uuid=FEATURES_SERVICE, characteristics=list(self.chars.values()))
        self.services = SimpleNamespace(get_service=lambda key: resolved if key.lower() == FEATURES_SERVICE else None)

    def guard(self):
        if self.closing or self.disconnected or self.invalid or not self.connected():
            raise RuntimeError('Native step link unavailable')

    def characteristic(self, char):
        if isinstance(char, str):
            result = self.chars.get(char.lower())
        else:
            result = next((c for c in self.chars.values() if c is char), None)
        if result is None:
            raise ValueError('Unexpected step characteristic')
        return result

    async def start_notify(self, char, callback):
        self.guard()
        char = self.characteristic(char)
        if char.uuid in self.subscriptions:
            raise ValueError('Duplicate step subscription')
        self.subscriptions.add(char.uuid)

        def notify(_handle, data):
            if self.disconnected:
                return
            if char.uuid != ALL_FEATURES and (not self.started or self.closing):
                self.invalid = True
                return
            callback(char, data)

        def acquired(result):
            _stop, remove = result
            self.removers.append(remove)

        async def acquire():
            self.guard()
            return await self.api.bluetooth_gatt_start_notify(self.address, char.handle, notify, 10)

        await joined(acquire(), acquired)
        self.guard()
        await self.api.bluetooth_gatt_write_descriptor(
            self.address, char.descriptors[0].handle, b'\x01\x00', 10,
        )
        self.guard()
        self.ready.add(char.uuid)

    async def write_gatt_char(self, char, data, response):
        self.guard()
        char = self.characteristic(char)
        if (char is not self.chars[DRSP] or bytes(data) != START or response is not True
                or self.started or self.ready != set(self.chars)):
            raise ValueError('Only subscribed step START is allowed')
        # The wire has no transaction ID. This is a public-invocation guard,
        # not proof that a delivered notification was caused by this START.
        self.started = True
        await self.api.bluetooth_gatt_write(self.address, char.handle, START, True, 10)
        self.guard()

    def remove_notifications(self):
        try:
            for remove in self.removers:
                remove()
        finally:
            self.removers.clear()

    async def disconnect(self):
        if self.disconnected:
            return
        self.closing = True
        try:
            def confirmed(_result):
                self.disconnected = True

            if self.cleanup_allowed():
                await joined(self.api.bluetooth_device_disconnect(self.address, timeout=5), confirmed)
            else:
                self.disconnected = True
        finally:
            self.remove_notifications()
