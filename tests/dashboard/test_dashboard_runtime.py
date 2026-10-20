"""Real HA registries + generator; only external native IO is synthetic.

Run separately from the repository's stub-based suites. This is NOT visual E2E.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN

# Import fixtures exactly; no reimplementation of integration or HA behavior.
from tests.ha.test_native_alarm_runtime import SOURCE, native as native
from tests.ha.test_unified_sync_runtime import unified as unified, drive, assert_clean
from tests.dashboard.test_generator import (
    DOMAINS,
    generator,
    references,
    attribute_rows,
    cli,
)


@pytest.mark.parametrize("language", ["es", "en"])
async def test_real_registry_renames_two_entries_retained_identity_and_public_attributes(
    unified,  # noqa: F811 - imported pytest fixture dependency
    language,
    tmp_path,
):
    p = unified
    registry = er.async_get(p.hass)

    def selected():
        return [
            e
            for e in registry.entities.values()
            if e.config_entry_id == p.watch.entry_id
        ]

    expected_domains = {**DOMAINS, "watch_connection": "switch"}
    assert len(selected()) == 37
    keys = {e.unique_id.removeprefix(p.watch.unique_id + "_"): e for e in selected()}
    assert set(keys) == set(expected_domains)
    assert {key: e.domain for key, e in keys.items()} == expected_domains

    # Populate real public state through HA service and installed native client.
    task = asyncio.create_task(
        p.hass.services.async_call(
            "button", "press", {"entity_id": keys["read_all"].entity_id}, blocking=True
        )
    )
    await drive(task, p)
    await p.hass.async_block_till_done()
    assert p.session.sync_result == "success"

    other = MockConfigEntry(
        domain=DOMAIN,
        title="Other watch",
        unique_id="AA:BB:CC:DD:EE:01",
        data={"address": "AA:BB:CC:DD:EE:01"},
    )
    other.add_to_hass(p.hass)  # after initial domain setup: no duplicate setup
    with patch(
        "custom_components.casio_abl100.bluetooth.async_ble_device_from_address",
        side_effect=lambda _hass, address, **kw: SimpleNamespace(
            address=address, name="ABL-100WE", details={"source": SOURCE}
        ),
    ):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        await p.hass.async_block_till_done()

        for index, entry in enumerate(sorted(selected(), key=lambda e: e.unique_id)):
            registry.async_update_entity(
                entry.entity_id, new_entity_id=f"{entry.domain}.my_watch_{index}"
            )
        await p.hass.async_block_till_done()
        before_ids = {e.entity_id for e in selected()}
        # Reconfigure preserves immutable identity; transport is deliberately different.
        p.hass.config.language = language
        p.hass.config_entries.async_update_entry(
            p.watch, data={"address": "AA:BB:CC:DD:EE:02"}
        )
        assert await p.hass.config_entries.async_reload(p.watch.entry_id)
        p.session = p.watch.runtime_data
        await p.hass.async_block_till_done()
        assert p.session.address != p.session.identity
        assert {e.entity_id for e in selected()} == before_ids

        # Changing transport invalidates cached observations by design. Obtain a
        # fresh synthetic native read at the new address before checking fields.
        keys = {
            e.unique_id.removeprefix(p.watch.unique_id + "_"): e for e in selected()
        }
        task = asyncio.create_task(
            p.hass.services.async_call(
                "button",
                "press",
                {"entity_id": keys["read_all"].entity_id},
                blocking=True,
            )
        )
        for _ in range(1000):
            if task.done():
                break
            p.wire.raw(int(p.session.address.replace(":", ""), 16))
            await asyncio.sleep(0)
        try:
            assert task.done(), "Synthetic read did not finish"
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await p.hass.async_block_till_done()
        assert p.session.sync_result == "success"
        assert await p.hass.config_entries.async_reload(p.watch.entry_id)
        p.session = p.watch.runtime_data
        await p.hass.async_block_till_done()

        # HA's actual WebSocket/list row formatter, not invented registry dictionaries.
        rows = json.loads(
            json.dumps([entry.as_partial_dict for entry in registry.entities.values()])
        )
        wire_count = len(p.wire.calls)
        dashboard = generator().build_dashboard(
            rows, p.watch.entry_id, language=language
        )
        assert len(p.wire.calls) == wire_count
        source = tmp_path / "registry.json"
        await p.hass.async_add_executor_job(source.write_text, json.dumps(rows))
        result = await p.hass.async_add_executor_job(
            cli, source, "--entry-id", p.watch.entry_id, "--language", language
        )
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout) == dashboard
        assert set(references(dashboard)) == before_ids
        foreign_ids = {
            e.entity_id
            for e in registry.entities.values()
            if e.config_entry_id == other.entry_id
        }
        assert len(foreign_ids) == 37 and not set(references(dashboard)) & foreign_ids
        assert all(
            p.hass.states.get(entity_id) is not None
            for entity_id in references(dashboard)
        )

        # Attribute rows point to real producer fields, including capability attrs
        # on unavailable Apply. Restored observations are stale, never invented.
        for row in attribute_rows(dashboard):
            state = p.hass.states.get(row["entity"])
            assert row["attribute"] in state.attributes, (
                row["entity"],
                row["attribute"],
            )
        assert not p.hass.states.get(
            next(
                e.entity_id for e in selected() if e.unique_id.endswith("_steps_today")
            )
        ).attributes["fresh"]
        assert await p.hass.config_entries.async_unload(other.entry_id)
    assert_clean(p)
