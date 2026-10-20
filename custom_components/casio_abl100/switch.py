"""Local enable/SIG drafts, independent of the read-only observed entities."""
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from .alarm_editing import DraftEntity


class AlarmEnabledDraft(DraftEntity,SwitchEntity):
    def __init__(self,session,slot=None):
        key = f'alarm_{slot}_enabled_draft' if slot else 'hourly_signal_draft'
        super().__init__(session,key,'enabled' if slot else 'signal',slot)
        self.entity_description = SwitchEntityDescription(key=key,translation_key=key)

    @property
    def is_on(self):
        return self.session.alarm_draft.value(self.field,self.slot,self.session.alarm_snapshot)

    async def _stage(self,value):
        if self.slot:
            self.session.stage_alarm_enabled(self.slot,value)
        else:
            self.session.stage_hourly_signal(value)
        self.async_write_ha_state()

    async def async_turn_on(self,**kwargs):
        await self._stage(True)

    async def async_turn_off(self,**kwargs):
        await self._stage(False)


async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([*(AlarmEnabledDraft(entry.runtime_data,slot) for slot in range(1,6)),
                        AlarmEnabledDraft(entry.runtime_data)])
