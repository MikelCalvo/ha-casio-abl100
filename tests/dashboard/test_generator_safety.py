"""Public generator validation regressions; only owned local files/IO."""

import json
import os
import stat
import subprocess
import sys

import pytest

from tests.dashboard.test_generator import (
    ENTRY,
    SCRIPT,
    by_key,
    generator,
    references,
    registry,
)

INVALID_IDS = [
    ("steps_today", "sensor._quality"),
    ("connect_interactive", "button.quality_"),
    ("alarm_1_time_draft", "time._"),
    ("alarm_1_enabled_draft", "switch.__"),
]


def run_cli(*args):
    try:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "CLI did not refuse the nonregular path within the failure detector"
        )


@pytest.mark.parametrize(
    "key,entity_id", INVALID_IDS, ids=["leading", "trailing", "single", "double"]
)
def test_api_rejects_ha_invalid_entity_object_boundaries(key, entity_id):
    rows = registry()
    by_key(rows, key)["entity_id"] = entity_id
    with pytest.raises(ValueError) as error:
        generator().build_dashboard(rows, ENTRY)
    assert (
        str(error.value)
        == "Invalid entity identifier or wrong domain for a required entity."
    )


@pytest.mark.parametrize(
    "key,entity_id", INVALID_IDS, ids=["leading", "trailing", "single", "double"]
)
def test_cli_rejects_ha_invalid_entity_object_boundaries(tmp_path, key, entity_id):
    rows = registry()
    by_key(rows, key)["entity_id"] = entity_id
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(rows))
    output = tmp_path / "not-created.yaml"
    result = run_cli(source, "--entry-id", ENTRY, "--output", output)
    assert result.returncode == 2 and not result.stdout
    assert (
        result.stderr
        == "Invalid entity identifier or wrong domain for a required entity.\n"
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "object_id", ["q", "0", "quality_name", "quality__name", "quality_1"]
)
def test_valid_custom_entity_object_boundaries_are_preserved(tmp_path, object_id):
    rows = registry()
    entity_id = "sensor." + object_id
    by_key(rows, "steps_today")["entity_id"] = entity_id
    assert entity_id in references(generator().build_dashboard(rows, ENTRY))
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(rows))
    result = run_cli(source, "--entry-id", ENTRY)
    assert result.returncode == 0 and not result.stderr
    assert entity_id in references(json.loads(result.stdout))


@pytest.mark.parametrize("boundary", ["input", "output", "forced_output"])
def test_cli_refuses_fifo_without_waiting_for_a_partner(tmp_path, boundary):
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    original = source.read_bytes()
    fifo = tmp_path / "PRIVATE_FIFO_CANARY"
    os.mkfifo(fifo)
    output = tmp_path / "not-created.yaml"
    if boundary == "input":
        args = (fifo, "--entry-id", ENTRY, "--output", output)
        expected = "Cannot read a valid registry JSON export.\n"
    else:
        args = (source, "--entry-id", ENTRY, "--output", fifo)
        if boundary == "forced_output":
            args += ("--force",)
        expected = "Cannot write output; existing files require --force. Check the destination.\n"
    result = run_cli(*args)
    assert result.returncode == 2 and not result.stdout and result.stderr == expected
    assert source.read_bytes() == original and not output.exists()
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_cli_refuses_forced_fifo_even_when_a_reader_allows_open(tmp_path):
    """Coverage of descriptor fstat rejection, not just FIFO's ENXIO open."""
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    before = source.read_bytes()
    fifo = tmp_path / "output.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        result = run_cli(source, "--entry-id", ENTRY, "--output", fifo, "--force")
        assert result.returncode == 2 and not result.stdout
        assert result.stderr == (
            "Cannot write output; existing files require --force. Check the destination.\n"
        )
        assert os.read(reader, 65536) == b""
    finally:
        os.close(reader)
    assert source.read_bytes() == before and stat.S_ISFIFO(fifo.lstat().st_mode)


@pytest.mark.parametrize("boundary", ["input", "output", "forced_output"])
def test_cli_refuses_directory_paths_with_fixed_errors(tmp_path, boundary):
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    directory = tmp_path / "PRIVATE_DIRECTORY_CANARY"
    directory.mkdir()
    if boundary == "input":
        args = (directory, "--entry-id", ENTRY)
        expected = "Cannot read a valid registry JSON export.\n"
    else:
        args = (source, "--entry-id", ENTRY, "--output", directory)
        if boundary == "forced_output":
            args += ("--force",)
        expected = "Cannot write output; existing files require --force. Check the destination.\n"
    result = run_cli(*args)
    assert result.returncode == 2 and not result.stdout and result.stderr == expected
    assert directory.is_dir()


@pytest.mark.parametrize("boundary", ["input", "output", "forced_output"])
@pytest.mark.parametrize(
    "dangling", [False, True], ids=["regular_target", "missing_target"]
)
def test_cli_refuses_symlinks_even_with_force_without_touching_target(
    tmp_path, boundary, dangling
):
    source = tmp_path / "registry.json"
    source.write_text(json.dumps(registry()))
    target = tmp_path / "target.json"
    if not dangling:
        target.write_bytes(source.read_bytes())
    before = target.read_bytes() if target.exists() else None
    link = tmp_path / "PRIVATE_LINK_CANARY"
    link.symlink_to(target)
    if boundary == "input":
        args = (link, "--entry-id", ENTRY)
        expected = "Cannot read a valid registry JSON export.\n"
    else:
        args = (source, "--entry-id", ENTRY, "--output", link)
        if boundary == "forced_output":
            args += ("--force",)
        expected = "Cannot write output; existing files require --force. Check the destination.\n"
    result = run_cli(*args)
    assert result.returncode == 2 and not result.stdout and result.stderr == expected
    assert link.is_symlink()
    assert (target.read_bytes() if target.exists() else None) == before
