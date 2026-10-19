"""Explicit read button; no scheduled polling or watch control."""
import asyncio

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from . import DOMAIN, MODEL


class _InteractiveError(HomeAssistantError):
    """Fixed catalog error with a safe fallback outside HA's context."""

    def __str__(self):
        try:
            return super().__str__()
        except HomeAssistantError:
            return 'Interactive watch action unavailable or failed'


def _error(key):
    return _InteractiveError(translation_domain=DOMAIN, translation_key=key)


def _cancelled():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def _reserved(session):
    controller = session.interactive_session
    return controller is not None and (
        controller.active or (controller.root is not None and not controller.root.done())
    )


def _admit_one_shot(session):
    _cancelled()
    if _reserved(session):
        raise _error('interactive_busy')


class ReadStepsButton(ButtonEntity):
    _attr_has_entity_name = True
    entity_description = ButtonEntityDescription(key='read_steps', translation_key='read_steps', icon='mdi:sync')

    def __init__(self, session):
        self.session = session
        self._attr_unique_id = f'{session.identity}_read_steps'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, session.identity)}, name='Casio ABL-100WE', model=MODEL, manufacturer='Casio')

    @property
    def available(self):
        return not _reserved(self.session)

    @property
    def extra_state_attributes(self):
        expiry = self.session.step_wait_expires_at
        return {'phase': self.session.step_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))

    async def async_press(self):
        _admit_one_shot(self.session)
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
    def available(self):
        return not _reserved(self.session)

    @property
    def extra_state_attributes(self):
        expiry = self.session.alarm_wait_expires_at
        return {'phase': self.session.alarm_phase,
                'wait_expires_at': expiry.isoformat() if expiry is not None else None}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))

    async def async_press(self):
        _admit_one_shot(self.session)
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
        _admit_one_shot(self.session)
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
        _admit_one_shot(self.session)
        await self.session.read_all()


class InteractiveButton(ReadAlarmsButton):
    """One explicit native action; close never takes the lifetime mutex."""

    def __init__(self, session, action):
        super().__init__(session)
        self.action = action
        key = f'{action}_interactive'
        self.entity_description = ButtonEntityDescription(
            key=key, translation_key=key,
            icon={'connect': 'mdi:bluetooth-connect', 'read': 'mdi:sync',
                  'close': 'mdi:bluetooth-off'}[action],
        )
        self._attr_unique_id = f'{session.identity}_{key}'

    def _operable(self):
        return (not self.session.closed and self.session.entry is not None
                and self.session.entry.state is ConfigEntryState.LOADED)

    @property
    def available(self):
        controller = self.session.interactive_session
        if self.action == 'close':
            # Cleanup remains callable while unload has revoked new work.
            return _reserved(self.session)
        if not self._operable():
            return False
        if self.action == 'connect':
            return not (_reserved(self.session) or self.session.tasks or self.session.lock.locked())
        return controller is not None and bool(controller.usable)

    @property
    def extra_state_attributes(self):
        return None

    async def async_press(self):
        _cancelled()
        if self.action != 'close':
            if not self._operable():
                raise _error(f'interactive_{self.action}_failed')
            if self.action == 'connect':
                if not self.available:
                    raise _error('interactive_busy')
            else:
                controller = self.session.interactive_session
                if controller is None or not controller.active or controller.close_reason is not None:
                    raise _error('interactive_not_connected')
                if controller.phase == 'reading':
                    raise _error('interactive_busy')
                if not controller.usable:
                    raise _error('interactive_not_connected')
        try:
            await getattr(self.session, f'{self.action}_interactive')()
        except asyncio.CancelledError:
            _cancelled()
            raise _error(f'interactive_{self.action}_failed') from None
        except Exception:
            _cancelled()
            raise _error(f'interactive_{self.action}_failed') from None


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReadStepsButton(entry.runtime_data), ReadAlarmsButton(entry.runtime_data),
                        ReadTimeZonesButton(entry.runtime_data), ReadAllButton(entry.runtime_data),
                        *(InteractiveButton(entry.runtime_data, action)
                          for action in ('connect', 'read', 'close'))])
