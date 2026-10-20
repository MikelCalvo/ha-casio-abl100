"""Streamlined primary view; existing raw information remains in a subview."""

import json
from tests.dashboard.test_generator import (
    generator,
    registry,
    ENTRY,
    IDENTITY,
    by_key,
    references,
)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def modern_registry():
    return registry() + [
        dict(
            config_entry_id=ENTRY,
            platform="casio_abl100",
            unique_id=f"{IDENTITY}_watch_connection",
            entity_id="switch.custom_watch_listening",
            disabled_by=None,
        )
    ]


def test_primary_view_is_small_friendly_and_diagnostics_are_separate():
    panel = generator().build_dashboard(registry(), ENTRY)
    assert len(panel["views"]) == 2, "Information must be outside the primary view"
    main, details = panel["views"]
    assert main["path"] == "watch" and details["subview"] is True
    assert details["path"] == "information"
    assert len(main["cards"]) == 1 and main["cards"][0]["type"] == "vertical-stack"
    assert len(main["cards"][0]["cards"]) <= 6
    serialized = json.dumps(main, ensure_ascii=False)
    for raw in (
        "wait_expires_at",
        "group_results",
        "native_status",
        "Lectura única",
        "Editor por etapas",
        "Borradores locales",
        "read_all",
    ):
        assert raw not in serialized
    assert "Conecta el reloj" in serialized
    assert "Enviar cambios" in serialized
    assert "Casio" in serialized
    assert "No conecta" not in serialized
    assert set(references(panel)) >= {
        e["entity_id"] for e in registry() if e["config_entry_id"] == ENTRY
    }
    assert not any("foreign" in r for r in references(panel))
    send = next(
        c
        for c in walk(main)
        if c.get("type") == "button"
        and c.get("entity") == by_key(registry(), "apply_alarm_draft")["entity_id"]
    )
    assert send["tap_action"]["confirmation"]["text"]
    assert send["tap_action"]["action"] == "toggle"
    assert send["hold_action"]["action"] == "none"
    assert "Observar" not in serialized


def test_modern_listening_control_is_contextual_and_custom_id_is_preserved():
    rows = modern_registry()
    panel = generator().build_dashboard(rows, ENTRY)
    main = panel["views"][0]
    refs = set(references(main))
    assert "switch.custom_watch_listening" in refs
    assert by_key(rows, "connect_interactive")["entity_id"] not in refs
    conditions = [c for c in walk(main) if c.get("type") == "conditional"]
    assert any(
        c.get("conditions")
        == [
            {
                "condition": "state",
                "entity": "switch.custom_watch_listening",
                "state": "off",
            }
        ]
        and c["card"]["name"] == "Activar escucha"
        for c in conditions
    )
    assert any(
        c.get("conditions")
        == [
            {
                "condition": "state",
                "entity": "switch.custom_watch_listening",
                "state": "on",
            }
        ]
        and c["card"]["name"] == "Parar conexión y escucha"
        for c in conditions
    )
    assert "cada 30" in json.dumps(main, ensure_ascii=False)


def test_legacy_primary_keeps_only_safe_manual_connect_fallback():
    rows = registry()
    main = generator().build_dashboard(rows, ENTRY)["views"][0]
    assert by_key(rows, "connect_interactive")["entity_id"] in set(references(main))
    assert by_key(rows, "close_interactive")["entity_id"] in set(references(main))
    assert "cada 30" not in json.dumps(main, ensure_ascii=False)


def test_optional_listening_entity_uses_same_validation_as_required_controls():
    import pytest

    for patch in (
        {"disabled_by": "user"},
        {"entity_id": "sensor.invalid"},
        {"entity_id": "switch.bad__id"},
        {"unique_id": "foreign_watch_connection"},
    ):
        rows = modern_registry()
        rows[-1].update(patch)
        with pytest.raises(ValueError):
            generator().build_dashboard(rows, ENTRY)
