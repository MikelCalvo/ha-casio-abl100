"""Real HA options manager contract; no Bluetooth or installed HA access."""
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import translation
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN

ADDRESS = 'AA:BB:CC:DD:EE:FF'
OPTION = 'auto_read_steps'


def entry_for(hass, options):
    entry = MockConfigEntry(
        domain=DOMAIN, title='Casio ABL-100WE', source='user',
        data={'address': ADDRESS}, unique_id=ADDRESS, options=options,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize('options,expected', [({}, False), ({OPTION: True}, True)])
async def test_options_form_uses_current_boolean_or_manual_default(
    hass, enable_custom_integrations, options, expected,
):
    entry = entry_for(hass, options)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result['type'] is FlowResultType.FORM
        assert result['step_id'] == 'init'
        assert result['data_schema']({}) == {OPTION: expected, 'auto_read_all': False, 'auto_connect_retained': False}
        assert entry.options == options
        connect.assert_not_called()


@pytest.mark.parametrize('enabled', [True, False])
async def test_options_save_preserves_unknown_options_and_watch_identity(
    hass, enable_custom_integrations, enabled,
):
    other = {'future_option': {'keep': ['unchanged']}}
    entry = entry_for(hass, {**other, OPTION: not enabled})
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], {OPTION: enabled},
        )
        assert result['type'] is FlowResultType.CREATE_ENTRY
        assert result['data'] == {**other, OPTION: enabled, 'auto_read_all': False, 'auto_connect_retained': False}
        assert entry.options == {**other, OPTION: enabled, 'auto_read_all': False, 'auto_connect_retained': False}
        assert entry.data == {'address': ADDRESS}
        assert entry.unique_id == ADDRESS
        connect.assert_not_called()


@pytest.mark.parametrize('option', [OPTION, 'auto_read_all', 'auto_connect_retained'])
@pytest.mark.parametrize('invalid', ['true', 'false', 0, 1, None, [], {}])
async def test_options_reject_non_booleans_without_mutating_entry(
    hass, enable_custom_integrations, invalid, option,
):
    entry = entry_for(hass, {'future_option': 'retained'})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        result['data_schema']({option: invalid})
    with pytest.raises(InvalidData) as rejected:
        await hass.config_entries.options.async_configure(
            result['flow_id'], {option: invalid},
        )
    assert rejected.value.path == [option]
    assert entry.options == {'future_option': 'retained'}
    assert entry.data == {'address': ADDRESS}
    assert entry.unique_id == ADDRESS


@pytest.mark.parametrize('language,label', [
    ('en', 'Read steps when watch becomes available'),
    ('es', 'Leer pasos al detectar el reloj'),
])
async def test_options_translations_available_through_real_ha(
    hass, enable_custom_integrations, language, label,
):
    entry = entry_for(hass, {})
    await hass.config_entries.options.async_init(entry.entry_id)
    strings = await translation.async_get_translations(hass, language, 'options', {DOMAIN})
    prefix = f'component.{DOMAIN}.options'
    assert strings[f'{prefix}.step.init.data.{OPTION}'] == label
    assert strings[f'{prefix}.step.init.data.auto_read_all'] == (
        'Read all watch data when watch becomes available' if language == 'en' else
        'Leer todos los datos al detectar el reloj')
    assert strings[f'{prefix}.step.init.data.auto_connect_retained'] == (
        'Connect and retain when watch becomes available' if language == 'en' else
        'Conectar y mantener al detectar el reloj')
    assert f'{prefix}.error.automatic_modes_conflict' in strings
    description = strings[f'{prefix}.step.init.description'].lower()
    assert 'experimental' in description
    assert 'cnct' in description
    assert ('advertising' in description if language == 'en' else 'anuncios' in description)
    assert f'{prefix}.error.invalid_boolean' in strings
