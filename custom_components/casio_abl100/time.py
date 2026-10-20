"""Local minute-semantic alarm drafts, never direct watch writes."""
from homeassistant.components.time import TimeEntity, TimeEntityDescription
from .alarm_editing import DraftEntity


class AlarmTimeDraft(DraftEntity,TimeEntity):
    def __init__(self,session,slot):
        key = f'alarm_{slot}_time_draft'
        super().__init__(session,key,'time',slot)
        self.entity_description = TimeEntityDescription(key=key,translation_key=key)

    @property
    def native_value(self):
        return self.session.alarm_draft.value('time',self.slot,self.session.alarm_snapshot)

    async def async_set_value(self,value):
        self.session.stage_alarm_time(self.slot,value)
        self.async_write_ha_state()


async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([AlarmTimeDraft(entry.runtime_data,slot) for slot in range(1,6)])
