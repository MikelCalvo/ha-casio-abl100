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
        self._attr_unique_id = f'{session.identity}_{self.entity_description.key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, session.identity)}, name='Casio ABL-100WE', model=MODEL, manufacturer='Casio')

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


class ZoneOffsetSensor(WatchSensor):
    """Last observed standard offset; never a present effective offset."""

    def __init__(self, session, secondary=False):
        self.secondary = secondary
        key = 'secondary_standard_utc_offset' if secondary else 'home_standard_utc_offset'
        self.entity_description = SensorEntityDescription(
            key=key, translation_key=key, native_unit_of_measurement='min',
            icon='mdi:map-clock-outline',
        )
        super().__init__(session)

    @property
    def available(self):
        return self.session.zone_snapshot is not None

    @property
    def native_value(self):
        snapshot = self.session.zone_snapshot
        if snapshot is None:
            return None
        configuration = snapshot.secondary if self.secondary else snapshot.home
        return configuration.standard_utc_offset_minutes

    @property
    def extra_state_attributes(self):
        snapshot = self.session.zone_snapshot
        if snapshot is None:
            return None
        configuration = snapshot.secondary if self.secondary else snapshot.home
        attributes = {
            'configured_dst_increment_minutes': configuration.configured_dst_increment_minutes,
            'dst_rule_code': configuration.dst_rule_code,
            'fresh': self.session.zones_fresh,
        }
        if not self.secondary:
            attributes['primary_dst_on'] = snapshot.primary_dst_on
            attributes['primary_dst_auto'] = snapshot.primary_dst_auto
        return attributes


class LastTimeZoneReadSensor(WatchSensor):
    entity_description = SensorEntityDescription(
        key='last_time_zone_read', translation_key='last_time_zone_read',
        device_class=SensorDeviceClass.TIMESTAMP, icon='mdi:clock-check-outline',
    )

    @property
    def native_value(self):
        return self.session.zone_last_sync

    @property
    def available(self):
        return self.session.zone_last_sync is not None


class SettingsSensor(WatchSensor):
    """Independent last-good configuration, restored stale from private storage."""
    snapshot_key = None

    @property
    def snapshot(self):
        return getattr(self.session, self.snapshot_key + '_snapshot')

    @property
    def available(self):
        return self.snapshot is not None and self.native_value is not None

    @property
    def extra_state_attributes(self):
        timestamp = getattr(self.session, self.snapshot_key + '_last_sync')
        attributes = {
            'fresh': getattr(self.session, self.snapshot_key + '_fresh'),
            'last_read': timestamp.isoformat() if timestamp is not None else None,
        }
        attributes.update(self.observed_attributes())
        return attributes

    def observed_attributes(self):
        return {}


class TimeFormatSensor(SettingsSensor):
    snapshot_key = 'basic_settings'
    entity_description = SensorEntityDescription(key='time_format', translation_key='time_format')

    @property
    def native_value(self):
        return self.snapshot.time_format if self.snapshot is not None else None

    def observed_attributes(self):
        if self.snapshot is None:
            return {}
        return {'button_tone_enabled': self.snapshot.button_tone_enabled,
                'light_duration_mode': self.snapshot.light_duration_mode}


class AutoTimeSensor(SettingsSensor):
    snapshot_key = 'auto_time'
    entity_description = SensorEntityDescription(
        key='automatic_time_adjustment', translation_key='automatic_time_adjustment',
        device_class=SensorDeviceClass.ENUM, options=['enabled', 'disabled'],
    )

    @property
    def available(self):
        return self.snapshot is not None

    @property
    def native_value(self):
        if self.snapshot is None or self.snapshot.enabled is None:
            return None
        return 'enabled' if self.snapshot.enabled else 'disabled'

    def observed_attributes(self):
        return {'adjustment_minute': self.snapshot.adjustment_minute if self.snapshot is not None else None}


class TimerEncodedSensor(SettingsSensor):
    snapshot_key = 'timer'
    entity_description = SensorEntityDescription(
        key='timer_encoded_duration', translation_key='timer_encoded_duration',
        native_unit_of_measurement='s',
    )

    @property
    def native_value(self):
        return self.snapshot.encoded_duration_seconds if self.snapshot is not None else None

    def observed_attributes(self):
        if self.snapshot is None:
            return {}
        return {'encoded_hours': self.snapshot.hours,
                'encoded_minutes': self.snapshot.minutes,
                'encoded_seconds': self.snapshot.seconds}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        StepsSensor(entry.runtime_data), LastSyncSensor(entry.runtime_data),
        *(AlarmSensor(entry.runtime_data, slot) for slot in range(1, 6)),
        LastAlarmReadSensor(entry.runtime_data),
        ZoneOffsetSensor(entry.runtime_data), ZoneOffsetSensor(entry.runtime_data, True),
        LastTimeZoneReadSensor(entry.runtime_data),
        TimeFormatSensor(entry.runtime_data), AutoTimeSensor(entry.runtime_data),
        TimerEncodedSensor(entry.runtime_data),
    ])
