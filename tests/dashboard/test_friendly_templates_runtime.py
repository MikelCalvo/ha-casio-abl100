"""Real HA template rendering, with synthetic states and no device client."""

from homeassistant.helpers.template import Template
from tests.dashboard.test_streamlined import generator, modern_registry, ENTRY, by_key


async def test_friendly_heading_renders_read_only_states_and_uncertainty(hass):
    rows = modern_registry()
    panel = generator().build_dashboard(rows, ENTRY)
    heading = panel["views"][0]["cards"][0]["cards"][0]["content"]
    connection = by_key(rows, "interactive_connected")["entity_id"]
    send = by_key(rows, "apply_alarm_draft")["entity_id"]
    control = "switch.custom_watch_listening"
    template = Template(heading, hass)
    hass.states.async_set(connection, "unknown", {"phase": "idle"})
    hass.states.async_set(control, "off")
    hass.states.async_set(send, "unknown", {"draft_pending": False})
    assert "Escucha parada" in template.async_render()
    hass.states.async_set(control, "on")
    assert "Esperando al reloj" in template.async_render()
    hass.states.async_set(connection, "on", {"phase": "reading"})
    assert "Leyendo el reloj" in template.async_render()
    hass.states.async_set(connection, "on", {"phase": "connected"})
    assert "**Conectado**" in template.async_render()
    hass.states.async_set(send, "unknown", {"draft_pending": True, "draft_saved": True})
    assert "Guardado en Home Assistant" in template.async_render()
    hass.states.async_set(
        send, "unknown", {"draft_pending": True, "draft_conflicted": True}
    )
    assert "El reloj cambió" in template.async_render()
    hass.states.async_set(
        send, "unknown", {"draft_pending": True, "needs_reconciliation": True}
    )
    assert "envío sin confirmar" in template.async_render()
    assert not any("{{" in str(template.async_render()) for _ in [0])
