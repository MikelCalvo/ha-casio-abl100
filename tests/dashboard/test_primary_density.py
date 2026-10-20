import json
from tests.dashboard.test_streamlined import generator, modern_registry, ENTRY, walk


def test_primary_action_buttons_are_compact_and_local_changes_are_explained():
    panel = generator().build_dashboard(modern_registry(), ENTRY)
    main = panel["views"][0]
    buttons = [n for n in walk(main) if n.get("type") == "button"]
    assert buttons and all(n.get("show_icon") is False for n in buttons)
    assert all(n.get("show_name") is True for n in buttons)
    rendered = json.dumps(main, ensure_ascii=False)
    assert "Borrador" in rendered or "cambios" in rendered
    assert "CNCT" in rendered
    assert "sin conexión" in rendered.lower()
