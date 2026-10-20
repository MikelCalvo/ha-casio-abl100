"""The primary view must distinguish waiting, disabled listening and busy IO."""

import json
from tests.dashboard.test_streamlined import generator, modern_registry, ENTRY


def test_main_status_describes_listening_and_saved_local_draft_without_raw_fields():
    main = generator().build_dashboard(modern_registry(), ENTRY)["views"][0]
    text = json.dumps(main, ensure_ascii=False)
    assert "Escucha parada" in text
    assert "Esperando al reloj" in text
    assert "Leyendo el reloj" in text
    assert "draft_saved" in text and "draft_conflicted" in text
    assert "Guardado en Home Assistant" in text
    assert "Se actualizará" not in text
