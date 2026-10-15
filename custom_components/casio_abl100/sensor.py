"""Read-only watch sensor entities."""
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass
from homeassistant.core import callback
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
            @callback
            def refresh_date(_now):
                self.async_write_ha_state()

            self.async_on_remove(async_track_time_change(
                self.hass, refresh_date,
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
        return {
            'record_date': self.session.record.record_date.isoformat(),
            'fresh': self.session.fresh,
            'daily_history': {
                day.record_date.isoformat(): day.steps
                for day in self.session.record.daily_history
            },
        }


class LastSyncSensor(WatchSensor):
    entity_description = SensorEntityDescription(key='last_sync', translation_key='last_sync', device_class=SensorDeviceClass.TIMESTAMP, icon='mdi:clock-check-outline')

    @property
    def native_value(self):
        return self.session.last_sync

    @property
    def available(self):
        return self.session.last_sync is not None


class AlarmSensor(WatchSensor):
    """A slot's last observed time, including when its enable bit is off."""

    def __init__(self, session, slot):
        self.entity_description = SensorEntityDescription(
            key=f'alarm_{slot}', translation_key=f'alarm_{slot}', icon='mdi:alarm',
        )
        self.slot = slot
        super().__init__(session)

    @property
    def available(self):
        return self.session.alarm_snapshot is not None

    @property
    def native_value(self):
        if not self.available:
            return None
        alarm = self.session.alarm_snapshot.alarms[self.slot - 1]
        return f'{alarm.hour:02d}:{alarm.minute:02d}'

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        alarm = self.session.alarm_snapshot.alarms[self.slot - 1]
        return {'enabled': alarm.enabled, 'fresh': self.session.alarms_fresh}


class LastAlarmReadSensor(WatchSensor):
    entity_description = SensorEntityDescription(
        key='last_alarm_read', translation_key='last_alarm_read',
        device_class=SensorDeviceClass.TIMESTAMP, icon='mdi:clock-check-outline',
    )

    @property
    def native_value(self):
        return self.session.alarm_last_sync

    @property
    def available(self):
        return self.session.alarm_last_sync is not None


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        StepsSensor(entry.runtime_data), LastSyncSensor(entry.runtime_data),
        *(AlarmSensor(entry.runtime_data, slot) for slot in range(1, 6)),
        LastAlarmReadSensor(entry.runtime_data),
    ])
