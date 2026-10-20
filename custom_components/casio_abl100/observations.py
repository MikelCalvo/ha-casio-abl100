"""Private semantic last-good observations; no device or session authority."""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import logging
import re

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store
from .protocol import DailySteps, StepRecord
from .settings import AutoTimeSettings, TimerConfiguration, ZoneConfiguration
from .step_history import decode_step_history, encode_step_history, merge_step_history

_LOGGER = logging.getLogger(__name__)
_OWNERS = 'casio_abl100.observation_owners'
_DESTINATIONS = {
    'alarms': ('alarm_snapshot', 'alarm_last_sync', 'alarms_fresh'),
    'steps': ('record', 'last_sync', 'fresh'),
    'zones': ('zone_snapshot', 'zone_last_sync', 'zones_fresh'),
    'basic_settings': ('basic_settings_snapshot', 'basic_settings_last_sync', 'basic_settings_fresh'),
    'auto_time': ('auto_time_snapshot', 'auto_time_last_sync', 'auto_time_fresh'),
    'timer': ('timer_snapshot', 'timer_last_sync', 'timer_fresh'),
}


@dataclass(frozen=True)
class _Alarm:
    slot: int
    hour: int
    minute: int
    enabled: bool


@dataclass(frozen=True)
class _Alarms:
    alarms: tuple[_Alarm, ...]
    hourly_signal: bool


@dataclass(frozen=True)
class _Zones:
    primary_dst_on: bool | None
    primary_dst_auto: bool | None
    home: ZoneConfiguration
    secondary: ZoneConfiguration


@dataclass(frozen=True)
class _Basic:
    time_format: str
    button_tone_enabled: bool
    light_duration_mode: str


def storage_key(entry_id):
    return 'casio_abl100.observations.' + entry_id


def address_binding(entry_id, address):
    if (type(entry_id) is not str or not entry_id.isascii() or not entry_id
            or type(address) is not str
            or re.fullmatch(r'[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}', address) is None):
        raise ValueError('Invalid observation binding')
    return hashlib.sha256(b'casio_abl100.observations.v1\0' + entry_id.encode('ascii')
                          + b'\0' + address.upper().encode('ascii')).hexdigest()


def _zone(value):
    return {k: getattr(value, k) for k in ('index', 'standard_utc_offset_minutes',
                                         'configured_dst_increment_minutes', 'dst_rule_code')}


def encode_group(group, value, observed_at):
    if group == 'steps':
        data = {'record_date': value.record_date.isoformat(), 'steps': value.steps,
                'daily_history': [{'record_date': d.record_date.isoformat(), 'steps': d.steps} for d in value.daily_history]}
    elif group == 'alarms':
        data = {'alarms': [{k: getattr(a, k) for k in ('slot', 'hour', 'minute', 'enabled')} for a in value.alarms],
                'hourly_signal': value.hourly_signal}
    elif group == 'zones':
        data = {'primary_dst_on': value.primary_dst_on, 'primary_dst_auto': value.primary_dst_auto,
                'home': _zone(value.home), 'secondary': _zone(value.secondary)}
    elif group == 'basic_settings':
        data = {k: getattr(value, k) for k in ('time_format', 'button_tone_enabled', 'light_duration_mode')}
    elif group == 'auto_time':
        data = {k: getattr(value, k) for k in ('enabled', 'adjustment_minute')}
    elif group == 'timer':
        data = {k: getattr(value, k) for k in ('hours', 'minutes', 'seconds', 'encoded_duration_seconds')}
    else:
        raise ValueError('Invalid observation group')
    result = {'observed_at': observed_at.isoformat(), 'value': data}
    _decode_group(group, result)
    return result


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError('Invalid observation fields')


def _integer(value, low, high, multiple=1):
    if type(value) is not int or not low <= value <= high or value % multiple:
        raise ValueError('Invalid observation integer')


def _boolean(value, nullable=False):
    if type(value) is not bool and not (nullable and value is None):
        raise ValueError('Invalid observation boolean')


def _date(value):
    if type(value) is not str or len(value) != 10:
        raise ValueError('Invalid observation date')
    day = date.fromisoformat(value)
    if day.isoformat() != value or not 2000 <= day.year <= 2099:
        raise ValueError('Invalid observation date')
    return day


def _validate(group, raw):
    _keys(raw, 'observed_at value')
    text = raw['observed_at']
    if type(text) is not str or len(text) > 40:
        raise ValueError('Invalid observation timestamp')
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError('Invalid observation timestamp')
    v = raw['value']
    if group == 'steps':
        _keys(v, 'record_date steps daily_history')
        day = _date(v['record_date'])
        _integer(v['steps'], 0, 99999999)
        if type(v['daily_history']) is not list or len(v['daily_history']) != 7:
            raise ValueError('Invalid observation history')
        for i, d in enumerate(v['daily_history'], 1):
            _keys(d, 'record_date steps')
            if _date(d['record_date']) != day - timedelta(days=i):
                raise ValueError('Invalid observation history')
            if d['steps'] is not None:
                _integer(d['steps'], 0, 99999999)
    elif group == 'alarms':
        _keys(v, 'alarms hourly_signal')
        _boolean(v['hourly_signal'])
        if type(v['alarms']) is not list or len(v['alarms']) != 5:
            raise ValueError('Invalid observation slots')
        for i, a in enumerate(v['alarms'], 1):
            _keys(a, 'slot hour minute enabled')
            _integer(a['slot'], i, i)
            _integer(a['hour'], 0, 23)
            _integer(a['minute'], 0, 59)
            _boolean(a['enabled'])
    elif group == 'zones':
        _keys(v, 'primary_dst_on primary_dst_auto home secondary')
        _boolean(v['primary_dst_on'], True)
        _boolean(v['primary_dst_auto'], True)
        if (v['primary_dst_on'] is None) != (v['primary_dst_auto'] is None):
            raise ValueError('Invalid observation flags')
        for i, key in enumerate(('home', 'secondary')):
            z = v[key]
            _keys(z, 'index standard_utc_offset_minutes configured_dst_increment_minutes dst_rule_code')
            _integer(z['index'], i, i)
            _integer(z['standard_utc_offset_minutes'], -1920, 1905, 15)
            _integer(z['configured_dst_increment_minutes'], 0, 3825, 15)
            _integer(z['dst_rule_code'], 0, 255)
    elif group == 'basic_settings':
        _keys(v, 'time_format button_tone_enabled light_duration_mode')
        if type(v['time_format']) is not str or v['time_format'] not in ('12h', '24h'):
            raise ValueError('Invalid observation format')
        if type(v['light_duration_mode']) is not str or v['light_duration_mode'] not in ('short', 'long'):
            raise ValueError('Invalid observation light')
        _boolean(v['button_tone_enabled'])
    elif group == 'auto_time':
        _keys(v, 'enabled adjustment_minute')
        _boolean(v['enabled'], True)
        if v['adjustment_minute'] is not None:
            _integer(v['adjustment_minute'], 0, 59)
    elif group == 'timer':
        _keys(v, 'hours minutes seconds encoded_duration_seconds')
        for key in ('hours', 'minutes', 'seconds'):
            _integer(v[key], 0, 255)
        _integer(v['encoded_duration_seconds'], 0, 255 * 3661)
        if v['encoded_duration_seconds'] != 3600*v['hours'] + 60*v['minutes'] + v['seconds']:
            raise ValueError('Invalid observation duration')
    else:
        raise ValueError('Invalid observation group')


def _decode_group(group, raw):
    _validate(group, raw)
    stamp = datetime.fromisoformat(raw['observed_at'])
    v = raw['value']
    if group == 'steps':
        value = StepRecord(date.fromisoformat(v['record_date']), v['steps'], tuple(
            DailySteps(date.fromisoformat(d['record_date']), d['steps']) for d in v['daily_history']))
    elif group == 'alarms':
        value = _Alarms(tuple(_Alarm(**a) for a in v['alarms']), v['hourly_signal'])
    elif group == 'zones':
        value = _Zones(v['primary_dst_on'], v['primary_dst_auto'], ZoneConfiguration(**v['home']), ZoneConfiguration(**v['secondary']))
    elif group == 'basic_settings':
        value = _Basic(**v)
    elif group == 'auto_time':
        value = AutoTimeSettings(**v)
    elif group == 'timer':
        value = TimerConfiguration(**v)
    else:
        raise ValueError('Invalid observation group')
    return value, stamp


def decode_payload(payload, entry_id, address):
    try:
        binding = address_binding(entry_id, address)
        if (type(payload) is not dict or set(payload) != {'schema', 'binding', 'groups'}
                or type(payload['schema']) is not int or payload['schema'] != 1
                or payload['binding'] != binding or type(payload['groups']) is not dict):
            return {}
    except (ValueError, TypeError):
        return {}
    result = {}
    for group in _DESTINATIONS:
        if group not in payload['groups']:
            continue
        try:
            result[group] = _decode_group(group, payload['groups'][group])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return result


class ObservationStore(Store):
    async def _async_migrate_func(self, major, minor, data):
        # Raising NotImplementedError here would permit same-major migration.
        raise ValueError('Unsupported observation storage version')


async def async_remove_entry_observations(hass, entry):
    """Reach the original Store after HA has deleted entry.runtime_data."""
    observations = hass.data.get(_OWNERS, {}).get(entry.entry_id)
    if observations is None:
        observations = EntryObservations(hass, entry.entry_id, entry.data['address'], entry)
    await observations.async_remove()


class EntryObservations:
    """One non-BLE coalescing writer; captures never await or touch disk."""

    def __init__(self, hass, entry_id, address, entry):
        self.hass, self.entry, self.entry_id, self.address = hass, entry, entry_id, address
        owners = hass.data.setdefault(_OWNERS, {})
        previous = owners.get(entry_id)
        if previous is not None and not previous._closed:
            raise ValueError('Observation owner is still active')
        # Normal close joins all writes. Only STOP may leave a valid save in
        # Store's FINAL_WRITE callback. Transfer that exact Store, not a list of
        # old sessions, across any reload before FINAL_WRITE or remove.
        self._deferred = previous is not None and previous._deferred
        self.store = previous.store if self._deferred else ObservationStore(
            hass, 1, storage_key(entry_id), private=True,
            minor_version=1, max_readable_version=1)
        self._groups = {}
        self.step_archive = ()
        self.archive_restored = False
        self._pending = None
        self._writer = None
        self._closed = False
        self._close_retry_used = False
        self._removed = False
        self._writable = previous._writable if self._deferred else True
        try:
            self._binding = address_binding(entry_id, address)
        except (ValueError, TypeError):
            self._binding = None
            self._disable_writes()
        # One owner per HA/key survives successful unload; replaced closed
        # generations are released, and remove releases the final owner.
        owners[entry_id] = self

    def _disable_writes(self):
        self._writable = False
        self.store.make_read_only()

    async def async_load_into(self, session):
        try:
            payload = await self.store.async_load()
        except (Exception, asyncio.CancelledError):
            self._disable_writes()
            _LOGGER.warning('Observation load failed')
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError from None
            return
        if payload is None:
            return
        if type(payload) is dict and type(payload.get('schema')) is int and payload['schema'] > 1:
            self._disable_writes()
            return
        if (self._binding is not None and type(payload) is dict
                and type(payload.get('binding')) is str and payload['binding'] != self._binding):
            try:
                await self.store.async_remove()
                # Public remove cancels FINAL_WRITE, but Store retains its
                # in-memory payload. A new binding must not load that payload
                # again if reconfigured back before a new validated capture.
                self.store = ObservationStore(
                    self.hass, 1, storage_key(self.entry_id), private=True,
                    minor_version=1, max_readable_version=1)
                self._deferred = False
            except (Exception, asyncio.CancelledError):
                _LOGGER.warning('Observation removal failed')
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError from None
            return
        restored = decode_payload(payload, self.entry_id, self.address)
        for group, (value, stamp) in restored.items():
            self._groups[group] = encode_group(group, value, stamp)
            destination, timestamp, freshness = _DESTINATIONS[group]
            setattr(session, destination, value)
            setattr(session, timestamp, stamp)
            setattr(session, freshness, False)
        if restored:
            raw_archive = payload['groups'].get('step_archive')
            try:
                if raw_archive is not None:
                    self.step_archive = decode_step_history(raw_archive)
                elif 'steps' in restored:
                    value, stamp = restored['steps']
                    self.step_archive = merge_step_history((), value, stamp)
            except (ValueError, TypeError, KeyError, OverflowError):
                # A malformed optional archive never invalidates six last-good groups.
                self.step_archive = ()
            if self.step_archive:
                self._groups['step_archive'] = encode_step_history(self.step_archive)
                self.archive_restored = True

    def capture(self, group, value, observed_at):
        if self._closed or self._removed or not self._writable:
            return
        try:
            encoded = encode_group(group, value, observed_at)
            groups = {**self._groups, group: encoded}
            if group == 'steps':
                archive = merge_step_history(self.step_archive, value, observed_at)
                groups['step_archive'] = encode_step_history(archive)
                self.step_archive = archive
                self.archive_restored = False
            self._groups = groups
            # A writer owns this immutable-by-convention snapshot. Subsequent
            # captures replace dictionaries; they never mutate offered payloads.
            self._pending = {'schema': 1, 'binding': self._binding, 'groups': self._groups}
            self._start_writer()
        except (Exception, asyncio.CancelledError):
            _LOGGER.warning('Observation capture failed')

    def _start_writer(self):
        if self._writer is not None and not self._writer.done():
            return
        coroutine = self._write()
        try:
            self._writer = self.entry.async_create_task(
                self.hass, coroutine, 'Casio observation save', eager_start=False)
        except (Exception, asyncio.CancelledError):
            coroutine.close()
            _LOGGER.warning('Observation scheduling failed')

    async def _write(self):
        while self._pending is not None:
            payload = self._pending
            self._pending = None
            try:
                await self.store.async_save(payload)
                self._deferred = getattr(self.hass, 'state', None) is CoreState.stopping
            except (Exception, asyncio.CancelledError):
                # Do not retry a persistent fault in a loop. A later valid
                # capture or one close attempt may offer the latest payload.
                if self._pending is None:
                    self._pending = payload
                _LOGGER.warning('Observation save failed')
                return

    async def async_close(self):
        self._closed = True
        cancelled = False
        while True:
            if self._pending is not None and (self._writer is None or self._writer.done()):
                if self._close_retry_used:
                    break
                self._close_retry_used = True
                self._start_writer()
            writer = self._writer
            if writer is None:
                break
            while not writer.done():
                try:
                    await asyncio.wait({writer})
                except asyncio.CancelledError:
                    cancelled = True
            # Always retrieve the owned task, including unexpected cancellation.
            try:
                writer.result()
            except (Exception, asyncio.CancelledError):
                _LOGGER.warning('Observation writer failed')
            if self._pending is None:
                break
        if cancelled:
            raise asyncio.CancelledError

    async def async_remove(self):
        self._removed = True
        cancelled = False
        try:
            await self.async_close()
        except asyncio.CancelledError:
            cancelled = True
        try:
            await self.store.async_remove()
        except (Exception, asyncio.CancelledError):
            _LOGGER.warning('Observation removal failed')
            if asyncio.current_task().cancelling():
                cancelled = True
        self._pending = None
        owners = self.hass.data.get(_OWNERS, {})
        if owners.get(self.entry_id) is self:
            del owners[self.entry_id]
        if cancelled:
            raise asyncio.CancelledError
