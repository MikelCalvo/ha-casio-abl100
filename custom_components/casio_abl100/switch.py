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
        await self.session.save_alarm_draft()
        self.async_write_ha_state()

    async def async_turn_on(self,**kwargs):
        await self._stage(True)

    async def async_turn_off(self,**kwargs):
        await self._stage(False)


class WatchConnectionSwitch(DraftEntity, SwitchEntity):
    """Saved observer preference; the existing entry listener owns reload."""
    def __init__(self, session):
        super().__init__(session, 'watch_connection')
        self.entity_description = SwitchEntityDescription(
            key='watch_connection', translation_key='watch_connection', icon='mdi:bluetooth')

    @property
    def available(self):
        from .alarm_editing import operable
        return operable(self.session)

    @property
    def is_on(self):
        return self.session.entry.options.get('auto_connect_retained') is True

    @property
    def extra_state_attributes(self):
        return None

    async def _set(self, enabled):
        entry = self.session.entry
        options = {**entry.options, 'auto_connect_retained': enabled}
        if enabled:
            options.update(auto_read_steps=False, auto_read_all=False)
        self.hass.config_entries.async_update_entry(entry, options=options)

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)


async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([*(AlarmEnabledDraft(entry.runtime_data,slot) for slot in range(1,6)),
                        AlarmEnabledDraft(entry.runtime_data), WatchConnectionSwitch(entry.runtime_data)])
