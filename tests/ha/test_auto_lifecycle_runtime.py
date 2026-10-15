"""Native entry integration boundaries; real HA, synthetic Bluetooth only."""
import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from habluetooth import get_manager
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from custom_components.casio_abl100.auto_read import AutoStepReader
from tests.ha.test_auto_runtime import ADDRESS, advertisement
from tests.protocol.test_transport import FakeClient


async def checkpoint():
    """Flush preceding ready callbacks once, without a timed delay."""
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_mode', ['preentry', 'repeated'])
async def test_session_shutdown_keeps_reader_cleanup_owned_through_caller_cancel(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth, cancel_mode,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    session = entry.runtime_data
    # Attach the already reviewed real observer explicitly. This exercises the
    # missing shutdown boundary rather than stopping at an owner-presence check.
    reader = AutoStepReader(hass, entry, session)
    session.auto_reader = reader
    writing, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
    client = FakeClient()
    client.block = asyncio.Event()
    original_write = client.write_gatt_char

    async def held_write(*args, **kwargs):
        writing.set()
        await original_write(*args, **kwargs)

    async def held_disconnect():
        cleaning.set()
        await release.wait()
        client.disconnected = True
        cleaned.set()

    async def shutdown():
        if cancel_mode == 'preentry':
            asyncio.current_task().cancel()
        await session.shutdown()

    client.write_gatt_char = held_write
    client.disconnect = held_disconnect
    stopper = None
    with patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ):
        reader.start()
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        await asyncio.wait_for(writing.wait(), 2)
        owned = reader._task
        try:
            stopper = asyncio.create_task(shutdown())
            await asyncio.wait_for(cleaning.wait(), 2)
            if cancel_mode == 'repeated':
                stopper.cancel()
                await checkpoint()
                stopper.cancel()
            await checkpoint()
            held = not owned.done() and not stopper.done()
        finally:
            release.set()
            results = await asyncio.gather(stopper, return_exceptions=True)
            await reader.stop()
            await hass.async_block_till_done()
        assert held, 'Shutdown waiter cancellation interrupted cooperative disconnect'
        assert cleaned.is_set() and client.disconnected
        assert isinstance(results[0], asyncio.CancelledError)
        assert session.closed and not session.tasks and not session.listeners
        assert owned.done() and reader._stopped and reader._cancel_timer is None
        assert session.record is None and session.last_sync is None and not session.fresh
        assert connect.call_count == 1
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timer', 'listener', 'on_unload', 'platform'])
async def test_failed_setup_finalizes_session_and_partial_platform_resources(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth, failure,
):
    from custom_components.casio_abl100 import PLATFORMS
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    forward = hass.config_entries.async_forward_entry_setups

    async def forward_then_fail(*args):
        await forward(*args)
        raise RuntimeError('SETUP_FAILURE')

    target, kwargs = {
        'timer': ('custom_components.casio_abl100.auto_read.async_track_time_interval',
                  {'side_effect': RuntimeError('SETUP_FAILURE')}),
        'listener': ('pytest_homeassistant_custom_component.common.MockConfigEntry.add_update_listener',
                     {'side_effect': RuntimeError('SETUP_FAILURE')}),
        'on_unload': ('pytest_homeassistant_custom_component.common.MockConfigEntry.async_on_unload',
                      {'side_effect': RuntimeError('SETUP_FAILURE')}),
        'platform': (None, {}),
    }[failure]
    failure_patch = (patch(target, **kwargs) if target else
                     patch.object(hass.config_entries, 'async_forward_entry_setups',
                                  side_effect=forward_then_fail))
    with patch('custom_components.casio_abl100.establish_connection') as connect, failure_patch:
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
        session = entry.runtime_data
        assert session.closed and not session.listeners and not session.tasks
        if session.auto_reader is not None:
            assert session.auto_reader._stopped
            assert session.auto_reader._cancel_callback is None
            assert session.auto_reader._cancel_timer is None
        assert not entry.update_listeners
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(hass)
        assert len([entity for entity in registry.entities.values()
                    if entity.config_entry_id == entry.entry_id]) == 11
        states = [state for state in hass.states.async_all()
                  if 'casio_abl_100we' in state.entity_id]
        assert len(states) == 11
        assert all(state.state == 'unavailable' and state.attributes.get('restored')
                   for state in states)  # HA preserves registry placeholders.
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        await hass.async_block_till_done()
        connect.assert_not_called()


@pytest.mark.asyncio
async def test_unload_before_scheduled_reader_enters_session_never_connects(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    from custom_components.casio_abl100 import async_unload_entry
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        session = entry.runtime_data
        reader = getattr(session, 'auto_reader', None)
        assert reader is not None, 'Enabled entry must own its native reader'
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        owned = reader._task
        assert owned is not None and not session.tasks
        # Direct entry boundary starts synchronously: suspend precedes the first
        # platform-unload yield, while the real HA background task is only queued.
        assert await async_unload_entry(hass, entry)
        await hass.async_block_till_done()
        assert owned.done() and session.closed and not session.tasks
        assert session.record is None and session.last_sync is None
        connect.assert_not_called()
        # Direct unload above already removed the real platforms. This patch is
        # harness bookkeeping only, after the production outcome assertions.
        with patch.object(hass.config_entries, 'async_unload_platforms', return_value=True):
            assert await hass.config_entries.async_unload(entry.entry_id)
