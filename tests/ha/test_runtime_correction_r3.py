"""Independent real-HA public error check after an already verified Send."""
from unittest.mock import patch
import json
import pytest
from homeassistant.exceptions import HomeAssistantError
from tests.ha.test_native_alarm_runtime import native
from tests.ha.test_unified_sync_runtime import unified
from tests.ha.test_native_alarm_editing_runtime import editing, service
from tests.ha.test_native_interactive_ui_runtime import press, connect_ui


async def test_verified_send_local_clear_failure_must_not_claim_nothing_sent(editing):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._atomic_write

    def fail_clear(path, record):
        if not record['times'] and not record['enabled'] and record['signal'] is None:
            raise OSError('QUALITY_LOCAL_CLEAR_FAILURE')
        return original(path, record)

    with patch.object(module, '_atomic_write', fail_clear):
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
    message = str(caught.value)
    witness = dict(native_result=p.session.alarm_draft.outcome.result,
                   settings_packets=len(p.wire.sets), public_message=message,
                   translation_key=caught.value.translation_key,
                   journal=json.loads(p.session.alarm_safety.path.read_text()))
    print('QUALITY_POST_SEND_ERROR', json.dumps(witness, sort_keys=True))
    assert witness['native_result'] == 'verified'
    assert witness['settings_packets'] == 2
    assert witness['journal']['result'] == 'verified' and witness['journal']['submitted'] is True
    assert 'no changes were sent' not in message.lower(), (
        'Confirmed SET/readback is reported to the user as no changes sent')


@pytest.mark.parametrize('language', ['en', 'es'])
@pytest.mark.parametrize('after_send', [False, True], ids=['edit', 'verified-send'])
async def test_storage_error_is_outcome_neutral_before_and_after_send(editing, language, after_send):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    from homeassistant.helpers import translation
    p = editing
    p.hass.config.language = language
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    if after_send:
        await connect_ui(p)
        await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._atomic_write

    def fail_save(path, record):
        if not after_send or (not record['times'] and not record['enabled'] and record['signal'] is None):
            raise OSError('LOCAL_SAVE_FAULT')
        return original(path, record)

    with patch.object(module, '_atomic_write', fail_save):
        with pytest.raises(HomeAssistantError) as caught:
            if after_send:
                await press(p, 'apply_alarm_draft')
            else:
                await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    assert caught.value.translation_key == 'alarm_draft_save_failed'
    # HA's context-free fallback is English and strips the terminal period.
    assert str(caught.value) == 'Unable to save the local draft'
    catalog = await translation.async_get_translations(
        p.hass, language, 'exceptions', {'casio_abl100'})
    assert catalog['component.casio_abl100.exceptions.alarm_draft_save_failed.message'] == (
        'No se pudo guardar el borrador local.' if language == 'es'
        else 'Unable to save the local draft.')
    assert len(p.wire.sets) == (2 if after_send else 0)
    if after_send:
        journal = json.loads(p.session.alarm_safety.path.read_text())
        assert p.session.alarm_draft.outcome.result == journal['result'] == 'verified'
        assert journal['submitted'] is True and journal['blocked'] is False
        assert p.session.draft_storage.fence['status'] == 'retired'
    else:
        assert not p.wire.calls
