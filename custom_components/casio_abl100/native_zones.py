"""Manual zone group on the unchanged shared native generation owner."""
from .native_alarms import NativeRead
from .native_zone_adapter import NativeZoneAdapter
from .zone_transport import read_zone_settings


class NativeZoneRead(NativeRead):
    def set_phase(self, phase, expiry=None):
        self.session.set_zone_phase(phase, expiry)

    def make_adapter(self, metadata):
        return NativeZoneAdapter(
            self.api, self.target, metadata,
            lambda: self.valid() and self.link, self.generation_valid,
        )

    async def transfer(self, adapter):
        return await read_zone_settings(adapter, timeout=12)

    def remove_notifications(self):
        if self.adapter:
            self.adapter.remove_notifications()
