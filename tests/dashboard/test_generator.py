"""Offline public API/CLI contract; no HA imports or stubs in this process."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/generate_dashboard.py"
ENTRY = "selected-entry"
IDENTITY = "AA:BB:CC:DD:EE:FF"
# Independent inventory from the actual producers; not imported from generator.
DOMAINS = {
    **dict.fromkeys(
        (
            "steps_today",
            "last_sync",
            "last_alarm_read",
            "home_standard_utc_offset",
            "secondary_standard_utc_offset",
            "last_time_zone_read",
            "time_format",
            "automatic_time_adjustment",
            "timer_encoded_duration",
            *(f"alarm_{i}" for i in range(1, 6)),
        ),
        "sensor",
    ),
    **dict.fromkeys(("hourly_signal", "interactive_connected"), "binary_sensor"),
    **dict.fromkeys(
        (
            "read_steps",
            "read_alarms",
            "read_time_zones",
            "read_all",
            "connect_interactive",
            "read_interactive",
            "close_interactive",
            "apply_alarm_draft",
            "discard_alarm_draft",
        ),
        "button",
    ),
    **dict.fromkeys((f"alarm_{i}_time_draft" for i in range(1, 6)), "time"),
    **dict.fromkeys(
        ("hourly_signal_draft", *(f"alarm_{i}_enabled_draft" for i in range(1, 6))),
        "switch",
    ),
}


def registry():
    selected = [
        dict(
            config_entry_id=ENTRY,
            platform="casio_abl100",
            unique_id=f"{IDENTITY}_{key}",
            entity_id=f"{domain}.renamed_{index}",
            disabled_by=None,
        )
        for index, (key, domain) in enumerate(DOMAINS.items())
    ]
    foreign = [
        dict(
            row,
            config_entry_id="other-entry",
            unique_id=row["unique_id"].replace(IDENTITY, "11:22:33:44:55:66"),
            entity_id=row["entity_id"].replace("renamed", "foreign"),
        )
        for row in selected
    ]
    return selected + foreign


def generator():
    assert SCRIPT.is_file(), "Missing offline dashboard generator public API"
    spec = importlib.util.spec_from_file_location("dashboard_generator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def references(value):
    """Collect all entity references, independent of generator implementation."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "entity":
                yield item
            elif key == "entities" and isinstance(item, list):
                for row in item:
                    if isinstance(row, str):
                        yield row
                    else:
                        yield from references(row)
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def attribute_rows(dashboard):
    return [
        row
        for view in dashboard["views"]
        for card in view["cards"]
        if card["type"] == "entities"
        for row in card["entities"]
        if isinstance(row, dict) and row.get("type") == "attribute"
    ]


def by_key(rows, key):
    return next(
        row
        for row in rows
        if row["config_entry_id"] == ENTRY and row["unique_id"] == f"{IDENTITY}_{key}"
    )


def cli(*args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_public_api_produces_deterministic_selected_only_stock_cards():
    module = generator()
    rows = registry()
    before = copy.deepcopy(rows)
    dashboard = module.build_dashboard(rows, ENTRY)
    assert dashboard == module.build_dashboard(
        list(reversed(rows)), ENTRY, language="es"
    )
    assert rows == before
    assert dashboard["title"] == "Casio ABL-100WE"
    assert len(dashboard["views"]) == 2
    assert dashboard["views"][1]["subview"] is True
    # Original detailed consumer contract remains on the Information subview.
    cards = dashboard["views"][1]["cards"]
    assert {c["type"] for c in cards} == {
        "markdown",
        "entities",
        "button",
        "history-graph",
    }
    expected = {r["entity_id"] for r in rows if r["config_entry_id"] == ENTRY}
    assert set(references(dashboard)) == expected
    rendered = json.dumps(dashboard, ensure_ascii=False)
    assert all(
        private not in rendered
        for private in (IDENTITY, ENTRY, "foreign", "unique_id", "config_entry_id")
    )
    assert "Recorder" in rendered and "NO" in rendered
    assert "automatic" in rendered.lower() or "automátic" in rendered.lower()
    assert "needs_reconciliation" in rendered
    assert not any(
        key in rendered
        for key in (
            "call-service",
            "perform-action",
            "custom:",
            "javascript:",
            "button.press",
        )
    )
    for card in cards:
        if card["type"] == "button":
            assert card["tap_action"] == {"action": "toggle"}
            assert card["hold_action"] == {"action": "none"}
            assert card["double_tap_action"] == {"action": "none"}
        if card["type"] == "entities":
            assert card["show_header_toggle"] is False
    assert any(
        card["type"] == "history-graph"
        and card["entities"] == [by_key(rows, "steps_today")["entity_id"]]
        for card in cards
    )
    observed = next(
        c
        for c in cards
        if c["type"] == "entities"
        and by_key(rows, "alarm_1")["entity_id"] in set(references(c))
    )
    drafts = next(
        c
        for c in cards
        if c["type"] == "entities"
        and by_key(rows, "alarm_1_time_draft")["entity_id"] in set(references(c))
    )
    assert observed is not drafts
    assert {by_key(rows, f"alarm_{i}")["entity_id"] for i in range(1, 6)} <= set(
        references(observed)
    )
    assert {
        by_key(rows, f"alarm_{i}_time_draft")["entity_id"] for i in range(1, 6)
    } <= set(references(drafts))


def test_attributes_match_public_producer_contract():
    rows = registry()
    attrs = attribute_rows(generator().build_dashboard(rows, ENTRY))

    def keys(key):
        return {
            r["attribute"]
            for r in attrs
            if r["entity"] == by_key(rows, key)["entity_id"]
        }

    assert keys("interactive_connected") == {
        "phase",
        "link_state",
        "usable",
        "wait_expires_at",
        "idle_expires_at",
        "session_expires_at",
        "operation_result",
        "group_results",
        "cleanup_result",
        "close_reason",
        "failure_kind",
        "failure_stage",
        "native_status",
    }
    assert keys("apply_alarm_draft") == {
        "draft_pending",
        "draft_revision",
        "apply_revision",
        "apply_phase",
        "apply_result",
        "write_submitted",
        "needs_reconciliation",
        "verified_at",
        "cleanup_result",
    }
    for i in range(1, 6):
        assert keys(f"alarm_{i}") == {"enabled", "fresh"}
        assert {"pending", "observed_value", "observed_at", "observed_fresh"} <= keys(
            f"alarm_{i}_time_draft"
        )
    assert keys("steps_today") == {"fresh", "record_date"}
    for key in ("time_format", "automatic_time_adjustment", "timer_encoded_duration"):
        assert {"fresh", "last_read"} <= keys(key)
    assert {
        "phase",
        "wait_expires_at",
        "result",
        "group_results",
        "cleanup_result",
    } == keys("read_all")


@pytest.mark.parametrize("form", ["list", "storage", "websocket"])
def test_supported_registry_export_forms(form):
    module = generator()
    rows = registry()
    wrapped = {
        "list": rows,
        "storage": {
            "version": 1,
            "key": "core.entity_registry",
            "data": {"entities": rows},
        },
        "websocket": {"id": 1, "type": "result", "success": True, "result": rows},
    }[form]
    assert module.build_dashboard(wrapped, ENTRY) == module.build_dashboard(rows, ENTRY)


def test_identity_is_not_current_transport_or_localized_entity_name():
    rows = registry()
    for row in rows[: len(DOMAINS)]:
        row["unique_id"] = row["unique_id"].replace(IDENTITY, "retained_identity_v1")
        row["original_name"] = "PRIVATE_DISPLAY_CANARY"
        row["address"] = "99:88:77:66:55:44"
    rendered = json.dumps(generator().build_dashboard(rows, ENTRY))
    assert (
        "retained_identity" not in rendered and "PRIVATE_DISPLAY_CANARY" not in rendered
    )
    assert "99:88:77" not in rendered


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "mixed_identity",
        "wrong_domain",
        "disabled",
        "bad_entity",
        "entity_newline",
        "entity_template",
        "unique_type",
        "entity_type",
        "disabled_type",
        "id_collision",
        "wrong_platform",
    ],
)
def test_rejects_invalid_required_mapping_with_fixed_private_safe_error(case):
    module = generator()
    rows = registry()
    row = by_key(rows, "connect_interactive")
    if case == "missing":
        rows.remove(row)
    elif case == "duplicate":
        rows.append(dict(row))
    elif case == "mixed_identity":
        row["unique_id"] = "PRIVATE_ID_CANARY_connect_interactive"
    elif case == "wrong_domain":
        row["entity_id"] = "switch.private_canary"
    elif case == "disabled":
        row["disabled_by"] = "user"
    elif case == "bad_entity":
        row["entity_id"] = "button.PRIVATE_ID_CANARY"
    elif case == "entity_newline":
        row["entity_id"] += "\nPRIVATE_ID_CANARY"
    elif case == "entity_template":
        row["entity_id"] = "button.{{PRIVATE_ID_CANARY}}"
    elif case == "unique_type":
        row["unique_id"] = ["PRIVATE_ID_CANARY"]
    elif case == "entity_type":
        row["entity_id"] = 12
    elif case == "disabled_type":
        row["disabled_by"] = False
    elif case == "id_collision":
        row["entity_id"] = by_key(rows, "close_interactive")["entity_id"]
    elif case == "wrong_platform":
        row["platform"] = "another_integration"
    with pytest.raises(ValueError) as error:
        module.build_dashboard(rows, ENTRY)
    assert str(error.value) in module.ERROR_MESSAGES
    assert "PRIVATE_ID_CANARY" not in str(error.value) and IDENTITY not in str(
        error.value
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        False,
        {},
        {"entities": []},
        {"data": {"entities": {}}},
        {"type": "result", "success": False, "result": []},
        [None],
        [True],
        [],
    ],
    ids=[
        "none",
        "boolean",
        "empty_mapping",
        "unwrapped_entities",
        "bad_storage",
        "failed_ws",
        "null_row",
        "bool_row",
        "no_entry",
    ],
)
def test_rejects_malformed_export(payload):
    with pytest.raises(ValueError):
        generator().build_dashboard(payload, ENTRY)


@pytest.mark.parametrize(
    "entry,language",
    [
        (None, "es"),
        (False, "es"),
        ("", "es"),
        ([], "es"),
        (ENTRY, False),
        (ENTRY, None),
        (ENTRY, "fr"),
        ("absent-entry", "es"),
    ],
    ids=[
        "null_entry",
        "bool_entry",
        "empty_entry",
        "list_entry",
        "bool_language",
        "null_language",
        "unknown_language",
        "absent_entry",
    ],
)
def test_strict_selection_and_language(entry, language):
    with pytest.raises(ValueError):
        generator().build_dashboard(registry(), entry, language=language)


def test_unrelated_bad_rows_and_future_keys_do_not_leak_or_change_output():
    module = generator()
    rows = registry()
    expected = module.build_dashboard(rows, ENTRY)
    rows.extend(
        [
            dict(
                config_entry_id="foreign",
                platform="casio_abl100",
                unique_id=None,
                entity_id="private",
            ),
            dict(config_entry_id=ENTRY, platform="foreign", unique_id=None),
            dict(
                config_entry_id=ENTRY,
                platform="casio_abl100",
                unique_id=f"{IDENTITY}_future_entity",
                entity_id="sensor.future",
            ),
        ]
    )
    assert module.build_dashboard(rows, ENTRY) == expected


def test_cli_stdout_file_languages_and_force(tmp_path):
    generator()
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    before = source.read_bytes()
    es = cli(source, "--entry-id", ENTRY)
    assert es.returncode == 0 and not es.stderr
    assert json.loads(es.stdout) == generator().build_dashboard(registry(), ENTRY)
    output = tmp_path / "dashboard.yaml"
    en = cli(source, "--entry-id", ENTRY, "--language", "en", "--output", output)
    assert en.returncode == 0 and en.stdout == "" and en.stderr == ""
    assert json.loads(output.read_text()) == generator().build_dashboard(
        registry(), ENTRY, language="en"
    )
    assert output.read_text() != es.stdout
    refused = cli(source, "--entry-id", ENTRY, "--output", output)
    assert refused.returncode == 2 and not refused.stdout
    assert source.read_bytes() == before
    assert (
        cli(source, "--entry-id", ENTRY, "--output", output, "--force").returncode == 0
    )
    assert output.read_text() == es.stdout
    assert cli(source, "--entry-id", ENTRY, "--output", source).returncode == 2
    assert source.read_bytes() == before
    assert (
        cli(source, "--entry-id", ENTRY, "--output", source, "--force").returncode == 0
    )
    assert json.loads(source.read_text()) == json.loads(es.stdout)


def test_cli_failures_are_fixed_and_do_not_write_or_echo_paths(tmp_path):
    generator()
    source = tmp_path / "PRIVATE_PATH_CANARY.json"
    output = tmp_path / "not-created.yaml"
    for contents in (
        "not json PRIVATE_INPUT_CANARY",
        "[]",
        json.dumps({"data": {"entities": []}}),
    ):
        source.write_text(contents)
        result = cli(source, "--entry-id", "PRIVATE_ENTRY_CANARY", "--output", output)
        assert result.returncode == 2 and not result.stdout and not output.exists()
        assert all(
            s not in result.stderr
            for s in ("PRIVATE_PATH", "PRIVATE_INPUT", "PRIVATE_ENTRY", "Traceback")
        )
    result = cli(tmp_path / "PRIVATE_MISSING_CANARY", "--entry-id", ENTRY)
    assert result.returncode == 2 and "PRIVATE_MISSING" not in result.stderr


@pytest.mark.parametrize("key", sorted(DOMAINS))
def test_every_required_entity_must_be_enabled(key):
    rows = registry()
    by_key(rows, key)["disabled_by"] = "integration"
    with pytest.raises(ValueError, match="required entity is disabled"):
        generator().build_dashboard(rows, ENTRY)


def test_cli_alias_refusal_invalid_arguments_duplicate_json_and_stdout_repeatability(
    tmp_path,
):
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    before = source.read_bytes()
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    hardlink = tmp_path / "hardlink.json"
    hardlink.hardlink_to(source)
    for target in (alias, hardlink):
        result = cli(source, "--entry-id", ENTRY, "--output", target)
        assert result.returncode == 2 and not result.stdout
        assert source.read_bytes() == before
    assert (
        cli(source, "--entry-id", ENTRY).stdout
        == cli(source, "--entry-id", ENTRY).stdout
    )
    for extra in (
        ("--language", "PRIVATE_ARGUMENT_CANARY"),
        ("--PRIVATE_ARGUMENT_CANARY",),
    ):
        result = cli(source, "--entry-id", ENTRY, *extra)
        assert result.returncode == 2 and "PRIVATE_ARGUMENT_CANARY" not in result.stderr
    source.write_text('{"data": {}, "data": {"entities": []}}')
    result = cli(source, "--entry-id", ENTRY)
    assert (
        result.returncode == 2
        and not result.stdout
        and "Traceback" not in result.stderr
    )


def test_generated_example_is_actual_deterministic_output():
    source = ROOT / "examples/dashboard-registry.synthetic.json"
    example = ROOT / "examples/dashboard.synthetic.es.yaml"
    result = cli(source, "--entry-id", ENTRY)
    assert result.returncode == 0 and result.stdout == example.read_text()
