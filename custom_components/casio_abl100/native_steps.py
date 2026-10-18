"""Manually armed native step transfer using the shared generation owner."""
from .native_alarms import NativeRead
from .native_step_adapter import NativeStepAdapter
from .transport import read_record, ReadError


class NativeStepRead(NativeRead):
    def set_phase(self, phase, expiry=None):
        self.session.set_step_phase(phase, expiry)

    def make_adapter(self, metadata):
        return NativeStepAdapter(
            self.api, self.target, metadata,
            lambda: self.valid() and self.link, self.generation_valid,
        )

    async def transfer(self, adapter):
        record = await read_record(adapter, timeout=12)
        # The frozen transport checks framing before disconnect. The adapter
        # additionally rejects pre-START or cleanup-time notifications.
        if adapter.invalid:
            raise ReadError('Invalid native step transaction')
        return record

    def remove_notifications(self):
        if self.adapter:
            self.adapter.remove_notifications()
