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
    if not isinstance(value, str) or not value.isascii():
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
            address, errors = self._selected_address(user_input)
            if errors:
                return self._form(errors)
            if self._address_in_use(address):
                return self.async_abort(reason='already_configured')
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title='Casio ABL-100WE', data={'address': address})
        return self._form({})

    async def async_step_reconfigure(self, user_input=None):
        """Explicitly replace only the selected entry's BLE transport address."""
        entry = self._get_reconfigure_entry()
        if not entry.unique_id:
            return self.async_abort(reason='missing_identity')
        if any(entry.options.get(key, False) is not False for key in ('auto_read_steps', 'auto_read_all', 'auto_connect_retained')):
            return self.async_abort(reason='auto_read_enabled')
        if user_input is not None:
            address, errors = self._selected_address(user_input)
            if errors:
                return self._form(errors, step_id='reconfigure')
            if self._address_in_use(address, exclude_entry=entry):
                return self.async_abort(reason='already_configured')
            # HA 2026.9 invokes our existing update listener to reload. Do not
            # also schedule a second reload with async_update_reload_and_abort.
            if entry.update_listeners:
                return self.async_update_and_abort(entry, data_updates={'address': address})
            return self.async_update_reload_and_abort(entry, data_updates={'address': address})
        return self._form({}, step_id='reconfigure')

    def _address_in_use(self, address, exclude_entry=None):
        """Reserve both immutable identities and current transport addresses."""
        return any(
            address in (normalize_address(entry.unique_id), normalize_address(entry.data.get('address')))
            for entry in self._async_current_entries(include_ignore=True) if entry is not exclude_entry
        )

    def _selected_address(self, user_input):
        """Typed MAC takes precedence; discovery requires explicit selection."""
        if user_input.get('address'):
            address = normalize_address(user_input['address'])
            return address, {} if address else {'address': 'invalid_address'}
        if user_input.get('discovered_address'):
            address = normalize_address(user_input['discovered_address'])
            if address and address in self._discovered_watches():
                return address, {}
            return None, {'discovered_address': 'invalid_selection'}
        return None, {'address': 'invalid_address'}

    def _discovered_watches(self):
        found = {}
        for info in bluetooth.async_discovered_service_info(self.hass):
            address = normalize_address(info.address)
            if address and watch_name(info.name):
                found[address] = f'{info.name.rstrip(chr(0))} ({address})'
        return found

    def _form(self, errors, step_id='user'):
        found = self._discovered_watches()
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
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors,
                                    description_placeholders=description_placeholders)


class CasioOptionsFlow(config_entries.OptionsFlow):
    """Configure optional automatic reads without changing watch identity."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            for key in ('auto_read_steps', 'auto_read_all', 'auto_connect_retained'):
                if type(user_input.get(key)) is not bool:
                    errors[key] = 'invalid_boolean'
            if not errors and sum(user_input[key] for key in (
                    'auto_read_steps', 'auto_read_all', 'auto_connect_retained')) > 1:
                errors['base'] = 'automatic_modes_conflict'
            if not errors:
                return self.async_create_entry(title='', data={
                    **self.config_entry.options, **user_input,
                })
        return self.async_show_form(
            step_id='init',
            data_schema=vol.Schema({
                vol.Required('auto_read_steps', default=self.config_entry.options.get(
                    'auto_read_steps', False,
                )): bool,
                vol.Required('auto_read_all', default=self.config_entry.options.get(
                    'auto_read_all', False,
                )): bool,
                vol.Required('auto_connect_retained', default=self.config_entry.options.get(
                    'auto_connect_retained', False,
                )): bool,
            }),
            errors=errors,
        )
