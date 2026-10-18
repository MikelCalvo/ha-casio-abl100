"""Explicit read button; no scheduled polling or watch control."""
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.helpers.device_registry import DeviceInfo
from . import DOMAIN, MODEL


class ReadStepsButton(ButtonEntity):
    _attr_has_entity_name = True
    entity_description = ButtonEntityDescription(key='read_steps', translation_key='read_steps', icon='mdi:sync')

    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.identity}_read_steps'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, session.identity)}, name='Casio ABL-100WE', model=MODEL, manufacturer='Casio')

    @property
    def extra_state_attributes(self):
        expiry = self.session.step_wait_expires_at
        return {'phase': self.session.step_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))

    async def async_press(self):
        await self.session.read()


class ReadAlarmsButton(ButtonEntity):
    """Explicitly initiate one complete read; never write watch settings."""
    _attr_has_entity_name = True
    entity_description = ButtonEntityDescription(
        key='read_alarms', translation_key='read_alarms', icon='mdi:sync',
    )

    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.identity}_read_alarms'
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, session.identity)}, name='Casio ABL-100WE',
            model=MODEL, manufacturer='Casio',
        )

    @property
    def extra_state_attributes(self):
        expiry = self.session.alarm_wait_expires_at
        return {'phase': self.session.alarm_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))

    async def async_press(self):
        await self.session.read_alarms()


class ReadTimeZonesButton(ReadAlarmsButton):
    """One manual configuration group, native ESPHome only."""
    entity_description = ButtonEntityDescription(
        key='read_time_zones', translation_key='read_time_zones', icon='mdi:sync',
    )

    def __init__(self, session):
        super().__init__(session)
        self._attr_unique_id = f'{session.identity}_read_time_zones'

    @property
    def extra_state_attributes(self):
        expiry = self.session.zone_wait_expires_at
        return {'phase': self.session.zone_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None}

    async def async_press(self):
        await self.session.read_time_zones()


class ReadAllButton(ReadAlarmsButton):
    """One native connection for all supported read groups."""
    entity_description = ButtonEntityDescription(
        key='read_all', translation_key='read_all', icon='mdi:sync',
    )

    def __init__(self, session):
        super().__init__(session)
        self._attr_unique_id = f'{session.identity}_read_all'

    @property
    def extra_state_attributes(self):
        expiry = self.session.sync_wait_expires_at
        return {'phase': self.session.sync_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None,
                'result': self.session.sync_result,
                'group_results': dict(self.session.sync_group_results),
                'cleanup_result': self.session.sync_cleanup_result}

    async def async_press(self):
        await self.session.read_all()


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReadStepsButton(entry.runtime_data), ReadAlarmsButton(entry.runtime_data),
                        ReadTimeZonesButton(entry.runtime_data), ReadAllButton(entry.runtime_data)])
