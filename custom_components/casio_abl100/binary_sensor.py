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
        self._attr_unique_id = f'{session.identity}_hourly_signal'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, session.identity)}, name='Casio ABL-100WE',
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


class InteractiveConnectionSensor(HourlySignalSensor):
    """Observation of this entry's native link, not a watch-screen claim."""

    entity_description = BinarySensorEntityDescription(
        key='interactive_connected', translation_key='interactive_connected',
        icon='mdi:bluetooth-connect',
    )

    def __init__(self, session):
        super().__init__(session)
        self._attr_unique_id = f'{session.identity}_interactive_connected'

    @property
    def available(self):
        # Unknown must remain available so HA's formatter retains explanations.
        return True

    @property
    def is_on(self):
        controller = self.session.interactive_session
        link = controller.link_state if controller is not None else 'unknown'
        return {'connected': True, 'disconnected': False, 'unknown': None}[link]

    @property
    def extra_state_attributes(self):
        controller = self.session.interactive_session
        if controller is None:
            from .native_sync import GROUPS
            return dict(phase='idle', link_state='unknown', usable=False,
                        wait_expires_at=None, idle_expires_at=None, session_expires_at=None,
                        operation_result='never', group_results=dict.fromkeys(GROUPS, 'not_attempted'),
                        cleanup_result='not_attempted', close_reason=None,
                        failure_kind=None, failure_stage=None, native_status=None)
        attrs = {key: getattr(controller, key) for key in (
            'phase', 'link_state', 'operation_result', 'cleanup_result', 'close_reason')}
        fault = controller.native_fault
        attrs.update(failure_kind='native_link_loss' if fault is not None else None,
                     failure_stage=fault.stage if fault is not None else None,
                     native_status=fault.status if fault is not None else None)
        attrs['usable'] = bool(controller.usable)
        attrs['group_results'] = dict(controller.group_results)
        for key in ('wait_expires_at', 'idle_expires_at', 'session_expires_at'):
            value = getattr(controller, key)
            attrs[key] = value.isoformat() if value is not None else None
        return attrs


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([HourlySignalSensor(entry.runtime_data),
                        InteractiveConnectionSensor(entry.runtime_data)])
