"""Last observed hourly-signal setting, not a ringing indication."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN, MODEL


class HourlySignalSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description = BinarySensorEntityDescription(
        key='hourly_signal', translation_key='hourly_signal', icon='mdi:clock-alert',
    )

    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.address}_hourly_signal'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, session.address)}, name='Casio ABL-100WE',
            model=MODEL, manufacturer='Casio',
        )

    async def async_added_to_hass(self):
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))

    @property
    def available(self):
        return self.session.alarm_snapshot is not None

    @property
    def is_on(self):
        if not self.available:
            return None
        return self.session.alarm_snapshot.hourly_signal

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        return {'fresh': self.session.alarms_fresh}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([HourlySignalSensor(entry.runtime_data)])
