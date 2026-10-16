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

    async def async_press(self):
        await self.session.read_alarms()


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReadStepsButton(entry.runtime_data), ReadAlarmsButton(entry.runtime_data)])
