"""Periodic reads and explicit Apply share one real HA native owner."""

import asyncio
from datetime import datetime
from unittest.mock import patch

from tests.ha.test_native_alarm_runtime import native as native
from tests.ha.test_unified_sync_runtime import unified as unified
from tests.ha.test_auto_unified_admission_runtime import admission
from tests.ha.test_native_alarm_editing_runtime import editing as editing
from tests.ha.test_native_alarm_editing_runtime import service, spin_until
from tests.ha.test_native_interactive_ui_runtime import press


async def test_periodic_due_during_apply_waits_for_original_owner(editing, monkeypatch):  # noqa: F811
    # Imported fixtures must remain module-visible for pytest dependency discovery.
    from custom_components.casio_abl100 import native_interactive as module

    p = editing
    monkeypatch.setattr(module, "PERIODIC_SECONDS", 0.02)
    with patch(
        "custom_components.casio_abl100.dt_util.now",
        return_value=datetime(2026, 10, 18),
    ):
        await service(p, "alarm_2_time_draft", "set_value", "01:02")
        ticket = admission(p)
        await p.session.connect_interactive(
            admission=ticket, retain_until_disconnect=True
        )
        await p.session.read_interactive()
        controller = p.session.interactive_session
        p.wire.hold_readback = True
        task = asyncio.create_task(press(p, "apply_alarm_draft"))
        try:
            await spin_until(p.wire.entered.is_set)
            assert controller.phase == "applying" and len(p.wire.sets) == 2
            frozen = list(p.wire.calls)
            await asyncio.sleep(0.06)  # periodic interval passes with readback held.
            assert p.wire.calls == frozen and controller.operation_caller is not None
            assert controller is p.session.interactive_session
            assert len([q for q in p.wire.calls if p.wire.stage(q) == "connect"]) == 1
            p.wire.release_readback()
            await task
            cycles = controller._cycles
            await spin_until(lambda: controller._cycles > cycles)
            assert controller.usable and not p.session.alarm_draft.pending
            assert len(p.wire.sets) == 2  # only the explicit Apply, not periodic IO.
            assert len([q for q in p.wire.calls if p.wire.stage(q) == "connect"]) == 1
        finally:
            p.wire.release_readback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await p.session.close_interactive()
        before = list(p.wire.calls)
        await asyncio.sleep(0.04)
        assert p.wire.calls == before and controller.root.done()
