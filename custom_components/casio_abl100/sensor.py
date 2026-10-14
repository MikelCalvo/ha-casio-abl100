"""Read-only watch sensor entities."""
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from . import DOMAIN, MODEL


class WatchSensor(SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.address}_{self.entity_description.key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, session.address)}, name='Casio ABL-100WE', model=MODEL, manufacturer='Casio')

    async def async_added_to_hass(self):
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))
        if isinstance(self, StepsSensor):
            self.async_on_remove(async_track_time_change(
                self.hass, lambda _now: self.async_write_ha_state(),
                hour=0, minute=0, second=0,
            ))


class StepsSensor(WatchSensor):
    entity_description = SensorEntityDescription(key='steps_today', translation_key='steps_today', native_unit_of_measurement='steps', icon='mdi:walk')

    @property
    def native_value(self):
        if self.session.record is None or self.session.record.record_date != dt_util.now().date():
            return None
        return self.session.record.steps

    @property
    def available(self):
        return self.session.record is not None and self.session.record.record_date == dt_util.now().date()

    @property
    def extra_state_attributes(self):
        if self.session.record is None:
            return None
        return {'record_date': self.session.record.record_date.isoformat(), 'fresh': self.session.fresh}


class LastSyncSensor(WatchSensor):
    entity_description = SensorEntityDescription(key='last_sync', translation_key='last_sync', device_class=SensorDeviceClass.TIMESTAMP, icon='mdi:clock-check-outline')

    @property
    def native_value(self):
        return self.session.last_sync

    @property
    def available(self):
        return self.session.last_sync is not None


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([StepsSensor(entry.runtime_data), LastSyncSensor(entry.runtime_data)])
