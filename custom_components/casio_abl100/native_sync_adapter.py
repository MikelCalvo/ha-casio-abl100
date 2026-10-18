"""Fixed unified allowlist on a borrowed native link; no disconnect helper."""
from .native_alarm_adapter import NativeAlarmAdapter
from .native_step_adapter import NativeStepAdapter
from .native_api import joined
from .transport import DRSP, CONVOY, START


class NativeSyncAdapter(NativeAlarmAdapter):
    """Acquire primary once, resolve step metadata only after alarm commit."""

    def __init__(self, *args):
        super().__init__(*args)
        self.metadata = args[2]
        self.receiver = None
        self.step_receivers = None
        self.step_removers = []
        self.step_chars = None
        self.primary_ready = False
        self.query_index = 0
        self.steps_started = False
        self.closing = False

    def guard(self):
        super().guard()
        if self.closing:
            raise RuntimeError('Unified link closing')

    async def start_primary(self):
        def receive(_char, data):
            if self.receiver is not None and not self.closing:
                self.receiver(data)
        await super().start_notify(self.primary, receive)
        self.guard()
        self.primary_ready = True

    async def query_primary(self, data):
        self.guard()
        queries = (b'\x15', b'\x16', b'\x1d\x00', b'\x1e\x00', b'\x1e\x01', b'\x13', b'\x11', b'\x18')
        if (not self.primary_ready or self.query_index >= len(queries)
                or data != queries[self.query_index]
                or (self.query_index >= 2 and not self.steps_started)):
            raise ValueError('Unexpected unified query')
        self.query_index += 1
        await self.api.bluetooth_gatt_write(self.address, self.query.handle, data, False, 10)
        self.guard()

    async def start_step_notifications(self):
        self.guard()
        # Reuse accepted metadata validation, not the disconnecting step reader.
        resolved = NativeStepAdapter(self.api, self.address, self.metadata, self.connected)
        self.step_chars = resolved.chars
        step_handles = {h for char in self.step_chars.values()
                        for h in (char.handle, char.descriptors[0].handle)}
        if self.query.handle in step_handles or any(
                service.handle == self.query.handle for service in self.metadata.services):
            raise ValueError('Aliased unified GATT handles')
        for uuid, index in ((DRSP, 0), (CONVOY, 1)):
            char = self.step_chars[uuid]
            def receive(_handle, data, index=index):
                if self.step_receivers is not None and not self.closing:
                    self.step_receivers[index](data)
            def acquired(result):
                self.step_removers.append(result[1])
            async def acquire():
                self.guard()
                return await self.api.bluetooth_gatt_start_notify(self.address, char.handle, receive, 10)
            await joined(acquire(), acquired)
            self.guard()
            await self.api.bluetooth_gatt_write_descriptor(
                self.address, char.descriptors[0].handle, b'\x01\x00', 10,
            )
            self.guard()

    async def start_steps(self):
        self.guard()
        if self.steps_started or self.query_index != 2 or self.step_chars is None:
            raise ValueError('Unexpected unified START')
        self.steps_started = True
        await self.api.bluetooth_gatt_write(self.address, self.step_chars[DRSP].handle, START, True, 10)
        self.guard()

    def remove_notifications(self):
        self.closing = True
        self.receiver = self.step_receivers = None
        for remove in self.step_removers:
            remove()
        self.step_removers.clear()
        if self.remove_notify is not None:
            remove, self.remove_notify = self.remove_notify, None
            remove()
