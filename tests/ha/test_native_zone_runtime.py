"""Real HA services, registry and installed APIClient, synthetic wire only."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import BluetoothGATTWriteRequest, BluetoothGATTNotifyDataResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from tests.ha.test_native_alarm_runtime import native, TARGET, ADDRESS, spin_until, assert_clean
from tests.protocol.test_zone_transport import QUERIES, FRAMES

BUTTON = 'button.casio_abl_100we_read_time_zones'
KEYS = {'read_time_zones', 'home_standard_utc_offset', 'secondary_standard_utc_offset', 'last_time_zone_read'}


@pytest.fixture
async def zones(native):
    original = native.wire.send_message
    native.frames = list(FRAMES)
    native.zone_failure = None
    def send(request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.data in QUERIES:
            native.wire.calls.append(request)
            if native.wire.hold_at == 'response':
                native.wire.entered.set()
                return
            frame = native.frames[QUERIES.index(request.data)]
            native.wire.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=61, data=frame))
            if native.zone_failure is not None:
                raise native.zone_failure
        else:
            original(request)
    native.wire.send_message = send
    yield native


async def press(zones):
    return await zones.hass.services.async_call('button', 'press', {'entity_id': BUTTON}, blocking=True)


async def armed(zones, direct=False):
    assert zones.hass.states.get(BUTTON) is not None, 'Missing native read_time_zones button'
    task = asyncio.create_task(zones.session.read_time_zones() if direct else press(zones))
    await spin_until(lambda: task.done() or zones.hass.states.get(BUTTON).attributes.get('phase') == 'waiting')
    if task.done():
        task.exception()
    assert zones.session.zone_phase == 'waiting'
    return task


@pytest.mark.asyncio
async def test_public_native_zone_service_three_gets_one_group_atomic_values(zones):
    ids = {e.unique_id.removeprefix(ADDRESS + '_'): e.entity_id for e in er.async_get(zones.hass).entities.values() if e.config_entry_id == zones.watch.entry_id}
    assert KEYS <= ids.keys(), 'Missing four time-zone entities'
    assert len(ids) == 11 + 4 + 4 + 4 + 13 + 1
    assert not zones.wire.calls
    for key in KEYS - {'read_time_zones'}:
        assert zones.hass.states.get(ids[key]).state == 'unavailable'
    task = await armed(zones)
    expiry = datetime.fromisoformat(zones.hass.states.get(BUTTON).attributes['wait_expires_at'])
    assert expiry.tzinfo == timezone.utc
    zones.wire.raw(TARGET - 1)
    await asyncio.sleep(0)
    assert not zones.wire.calls
    zones.wire.raw()
    await task
    await zones.hass.async_block_till_done()
    assert zones.hass.states.get(ids['home_standard_utc_offset']).state == '-330'
    assert zones.hass.states.get(ids['secondary_standard_utc_offset']).state == '345'
    attrs = zones.hass.states.get(ids['home_standard_utc_offset']).attributes
    assert {k: attrs[k] for k in ('configured_dst_increment_minutes', 'dst_rule_code', 'primary_dst_on', 'primary_dst_auto', 'fresh')} == dict(configured_dst_increment_minutes=60, dst_rule_code=254, primary_dst_on=True, primary_dst_auto=True, fresh=True)
    assert attrs['unit_of_measurement'] == 'min'
    assert 'device_class' not in attrs and 'state_class' not in attrs
    assert 'primary_dst_flags' not in attrs
    secondary = zones.hass.states.get(ids['secondary_standard_utc_offset']).attributes
    assert not {'primary_dst_on', 'primary_dst_auto', 'secondary_dst_on', 'effective_offset', 'city', 'iana'} & secondary.keys()
    assert zones.session.zone_last_sync.tzinfo == timezone.utc
    assert datetime.fromisoformat(zones.hass.states.get(ids['last_time_zone_read']).state) == zones.session.zone_last_sync.replace(microsecond=0)
    assert zones.session.record is None and zones.session.alarm_snapshot is None
    assert [type(x).__name__ for x in zones.wire.calls] == ['BluetoothDeviceRequest', 'BluetoothGATTGetServicesRequest', 'BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', *(['BluetoothGATTWriteRequest'] * 3), 'BluetoothDeviceRequest']
    assert [(x.data, x.response) for x in zones.wire.calls if isinstance(x, BluetoothGATTWriteRequest)] == [(q, False) for q in QUERIES]
    assert_clean(zones)


@pytest.mark.asyncio
async def test_failure_retains_last_good_and_unknown_flags_remain_unknown(zones):
    zones.frames[0] = FRAMES[0][:3] + b'\x83' + FRAMES[0][4:]
    task = await armed(zones)
    zones.wire.raw()
    await task
    snap, stamp = zones.session.zone_snapshot, zones.session.zone_last_sync
    assert snap.primary_dst_on is None and snap.primary_dst_auto is None
    zones.session.fresh = zones.session.alarms_fresh = True
    zones.frames[2] = b'\xff'
    task = await armed(zones)
    zones.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'zone_read_failed'
    assert not caught.value.translation_placeholders
    assert zones.session.zone_snapshot is snap and zones.session.zone_last_sync is stamp
    assert not zones.session.zones_fresh and zones.session.fresh and zones.session.alarms_fresh
    await zones.hass.async_block_till_done()
    assert zones.hass.states.get("sensor.casio_abl_100we_home_standard_utc_offset").state == "-330"
    assert zones.hass.states.get("sensor.casio_abl_100we_home_standard_utc_offset").attributes["fresh"] is False
    assert datetime.fromisoformat(zones.hass.states.get("sensor.casio_abl_100we_last_time_zone_read").state) == stamp.replace(microsecond=0)
    assert_clean(zones)
