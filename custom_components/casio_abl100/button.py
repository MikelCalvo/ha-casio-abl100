"""Explicit read button; no scheduled polling or watch control."""
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.helpers.device_registry import DeviceInfo
from . import DOMAIN, MODEL


class ReadStepsButton(ButtonEntity):
    _attr_has_entity_name = True
    entity_description = ButtonEntityDescription(key='read_steps', translation_key='read_steps', icon='mdi:sync')

    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.address}_read_steps'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, session.address)}, name='Casio ABL-100WE', model=MODEL, manufacturer='Casio')

    async def async_press(self):
        await self.session.read()


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReadStepsButton(entry.runtime_data)])
