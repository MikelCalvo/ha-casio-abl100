"""Manual configuration flow: a selected discovered address or exact typed MAC."""
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.helpers import selector
from . import DOMAIN

MAC = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')


def normalize_address(value):
    if not isinstance(value, str):
        return None
    result = value.strip().upper()
    return result if MAC.fullmatch(result) else None


def watch_name(name):
    return (name or '').rstrip('\x00 \t\r\n') in (
        'ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE'
    )


class CasioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            address = normalize_address(user_input.get('address') or user_input.get('discovered_address'))
            if address is None:
                return self._form({'address': 'invalid_address'})
            if any(address == (entry.unique_id or entry.data.get('address', '')).upper()
                   for entry in self._async_current_entries()):
                return self.async_abort(reason='already_configured')
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title='Casio ABL-100WE', data={'address': address})
        return self._form({})

    def _form(self, errors):
        found = {}
        for info in bluetooth.async_discovered_service_info(self.hass):
            address = normalize_address(info.address)
            if address and watch_name(info.name):
                found[address] = f'{info.name.rstrip(chr(0))} ({address})'
        if found:
            schema = vol.Schema({
                vol.Optional('discovered_address'): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[{'value': address, 'label': label} for address, label in found.items()], mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional('address'): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            })
            description_placeholders = {'discovered': ', '.join(found.values())}
        else:
            schema = vol.Schema({vol.Required('address'): str})
            description_placeholders = {'discovered': '—'}
        return self.async_show_form(step_id='user', data_schema=schema, errors=errors,
                                    description_placeholders=description_placeholders)
