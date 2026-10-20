"""Offline full-stack composition; only external native wire is synthetic."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceConnectionResponse,
    BluetoothGATTWriteRequest,
)
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from tests.ha.test_native_alarm_runtime import (
    native as native,
    spin_until,
    SOURCE,
    TARGET,
)
from tests.ha.test_unified_sync_runtime import unified as unified, QUERIES
from tests.ha.test_auto_unified_admission_runtime import producer as producer


def dashboard_api():
    path = Path(__file__).parents[2] / "tools/generate_dashboard.py"
    assert path.is_file(), "Composed dashboard generator absent"
    spec = importlib.util.spec_from_file_location("composed_dashboard_generator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "entity" and isinstance(child, str):
                yield child
            elif key == "entities" and isinstance(child, list):
                for row in child:
                    if isinstance(row, str):
                        yield row
                    else:
                        yield from refs(row)
            else:
                yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


@pytest.fixture
async def other_watch(producer):  # noqa: F811 - imported pytest fixture
    """A real second entry exposes accidental cross-watch panel selection."""
    p = producer
    other = MockConfigEntry(
        domain=DOMAIN,
        title="Other watch",
        unique_id="AA:BB:CC:DD:EE:01",
        data={"address": "AA:BB:CC:DD:EE:01"},
    )
    other.add_to_hass(p.hass)
    with patch(
        "custom_components.casio_abl100.bluetooth.async_ble_device_from_address",
        side_effect=lambda _hass, address, **kw: SimpleNamespace(
            address=address, name="ABL-100WE", details={"source": SOURCE}
        ),
    ):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        await p.hass.async_block_till_done()
    try:
        yield other
    finally:
        assert await p.hass.config_entries.async_unload(other.entry_id)


@pytest.mark.parametrize("language", ["es", "en"])
async def test_retained_read_archive_dashboard_release_and_reload_compose(
    producer,  # noqa: F811 - imported pytest fixture
    other_watch,
    language,
):
    p = producer
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "auto_read_steps": False,
            "auto_read_all": False,
            "auto_connect_retained": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    observer = p.session.auto_reader
    assert observer is not None and observer.mode == "retained", (
        "Retained option not composed"
    )
    now = [monotonic_time_coarse() + 1]
    with (
        patch(
            "custom_components.casio_abl100.auto_read.monotonic_time_coarse",
            side_effect=lambda: now[0],
        ),
        patch(
            "custom_components.casio_abl100.native_sync.monotonic_time_coarse",
            side_effect=lambda: now[0],
        ),
    ):
        p.wire.raw()
        await spin_until(lambda: observer._task is None)
        controller = p.session.interactive_session
        assert controller is not None and controller.usable
        assert controller.idle_expires_at is controller.session_expires_at is None
        assert controller.root in p.session.tasks and p.session.lock.locked()
        requests = [x for x in p.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]
        assert [bytes(x.data) for x in requests] == list(QUERIES)
        assert len([x for x in p.wire.calls if p.wire.stage(x) == "connect"]) == 1
        # DRSP START is acknowledged ATT traffic, not a settings SET. GET
        # requests use response=False; request bytes above are the full allowlist.
        assert [x.response for x in requests] == [False, False, True] + [False] * 6
        await p.hass.async_block_till_done()
        registry = er.async_get(p.hass)
        entries = [
            e
            for e in registry.entities.values()
            if e.config_entry_id == p.watch.entry_id
        ]
        assert len(entries) == 37
        for index, entry in enumerate(sorted(entries, key=lambda e: e.unique_id)):
            registry.async_update_entity(
                entry.entity_id, new_entity_id=f"{entry.domain}.composed_watch_{index}"
            )
        await p.hass.async_block_till_done()
        selected = [
            e
            for e in registry.entities.values()
            if e.config_entry_id == p.watch.entry_id
        ]
        steps = next(
            e.entity_id for e in selected if e.unique_id.endswith("_steps_today")
        )
        attributes = p.hass.states.get(steps).as_dict()["attributes"]
        assert (
            attributes["archived_day_count"] == 8
            and attributes["archive_restored"] is False
        )
        archived = dict(attributes["archived_daily_steps"])
        rows = [e.as_partial_dict for e in registry.entities.values()]
        panel = dashboard_api().build_dashboard(
            rows, p.watch.entry_id, language=language
        )
        assert set(refs(panel)) == {e.entity_id for e in selected}
        foreign = {
            e.entity_id
            for e in registry.entities.values()
            if e.config_entry_id == other_watch.entry_id
        }
        assert len(foreign) == 37 and not set(refs(panel)) & foreign
        for entity_id in refs(panel):
            assert p.hass.states.get(entity_id) is not None
        refresh = next(
            e.entity_id for e in selected if e.unique_id.endswith("_read_interactive")
        )
        await p.hass.services.async_call(
            "button", "press", {"entity_id": refresh}, blocking=True
        )
        assert controller is p.session.interactive_session and controller.usable
        assert len([x for x in p.wire.calls if p.wire.stage(x) == "connect"]) == 1
        assert [
            bytes(x.data)
            for x in p.wire.calls
            if isinstance(x, BluetoothGATTWriteRequest)
        ] == list(QUERIES) * 2
        p.wire.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False))
        await controller._join()
        assert controller.phase == "closed" and controller.close_reason == "link_lost"
        assert controller.cleanup_result == "success" and not p.session.tasks
        now[0] += 1
        p.wire.raw()  # Queued old closing burst never recaptures the watch.
        await asyncio.sleep(0)
        assert len([x for x in p.wire.calls if p.wire.stage(x) == "connect"]) == 1
        now[0] += 31
        p.wire.raw()  # A genuinely later quiet-separated window is admitted.
        await spin_until(lambda: observer._task is None)
        assert (
            p.session.interactive_session is not controller
            and p.session.interactive_session.usable
        )
        assert len([x for x in p.wire.calls if p.wire.stage(x) == "connect"]) == 2
        await p.hass.async_block_till_done()
        archived = dict(p.hass.states.get(steps).attributes["archived_daily_steps"])
        original_last_sync = p.session.last_sync
        assert original_last_sync is not None
        close = next(
            e.entity_id for e in selected if e.unique_id.endswith("_close_interactive")
        )
        await p.hass.services.async_call(
            "button", "press", {"entity_id": close}, blocking=True
        )
    # An unresolved safety record is separate from disposable drafts and archive
    # storage. An options reload must neither clear it nor trigger native IO.
    safety = p.session.alarm_safety
    await safety.async_pending()
    saved_barrier = safety.path.read_bytes()
    wire_calls = list(p.wire.calls)
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    await p.hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "auto_read_steps": False,
            "auto_read_all": False,
            "auto_connect_retained": False,
        },
    )
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.session.auto_reader is None and p.session.interactive_session is None
    assert (
        not p.session.alarm_draft.pending and p.session.alarm_draft.needs_reconciliation
    )
    assert (
        p.session.alarm_safety.blocked
        and p.session.alarm_safety.path.read_bytes() == saved_barrier
    )
    assert p.wire.calls == wire_calls
    assert p.session.last_sync == original_last_sync
    assert not p.session.tasks and not p.session.lock.locked()
    assert not p.runtime.disconnect_callbacks
    p.bleak.assert_not_called()
    state = p.hass.states.get(steps).as_dict()
    assert state["attributes"]["archive_restored"] is True
    assert state["attributes"]["archived_daily_steps"] == archived
    assert state["attributes"]["fresh"] is False
    assert {
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == p.watch.entry_id
    } == {e.entity_id for e in selected}
