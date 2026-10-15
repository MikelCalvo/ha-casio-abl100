"""Manual configuration flow: a selected discovered address or exact typed MAC."""
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the standard HA options flow for this entry."""
        return CasioOptionsFlow()

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


class CasioOptionsFlow(config_entries.OptionsFlow):
    """Configure optional automatic reads without changing watch identity."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if type(user_input.get('auto_read_steps')) is not bool:
                errors['auto_read_steps'] = 'invalid_boolean'
            else:
                return self.async_create_entry(title='', data={
                    **self.config_entry.options,
                    'auto_read_steps': user_input['auto_read_steps'],
                })
        return self.async_show_form(
            step_id='init',
            data_schema=vol.Schema({
                vol.Required('auto_read_steps', default=self.config_entry.options.get(
                    'auto_read_steps', False,
                )): bool,
            }),
            errors=errors,
        )
