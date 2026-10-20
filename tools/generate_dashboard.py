#!/usr/bin/env python3
"""Generate stock Lovelace JSON (also valid YAML) from an offline registry export.

No Home Assistant imports, credentials, network or registry writes. Public API:
    build_dashboard(registry, entry_id, language="es") -> dict
Invalid input raises ValueError with a fixed, private-safe message.
"""

import argparse
import json
from pathlib import Path
import re
import sys


_REQUIRED = {
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
_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+", re.ASCII)
ERROR_MESSAGES = (
    "Invalid registry export; use a row list, data.entities storage export or successful WebSocket result.",
    "Select a nonempty config entry ID explicitly.",
    "Unsupported language; choose es or en.",
    "Invalid selected registry row; export complete entity registry rows.",
    "Missing required entities for the selected entry; check the installed integration and export.",
    "Duplicate or ambiguous required mapping; resolve the registry export before generating.",
    "Required entities have inconsistent identity prefixes; check the selected entry export.",
    "Invalid entity identifier or wrong domain for a required entity.",
    "A required entity is disabled; review and enable it intentionally before exporting again.",
)


def _rows(registry):
    if type(registry) is dict:
        if type(registry.get("data")) is dict and "entities" in registry["data"]:
            registry = registry["data"]["entities"]
        elif (
            registry.get("type") == "result"
            and registry.get("success") is True
            and "result" in registry
        ):
            registry = registry["result"]
        else:
            raise ValueError(ERROR_MESSAGES[0])
    if type(registry) is not list or any(type(row) is not dict for row in registry):
        raise ValueError(ERROR_MESSAGES[0])
    return registry


def _resolve(registry, entry_id):
    """Match immutable entry + platform + unique-ID prefix/suffix, never names."""
    mapping = {}
    identities = set()
    for row in _rows(registry):
        if (
            row.get("config_entry_id") != entry_id
            or row.get("platform") != "casio_abl100"
        ):
            continue
        unique_id = row.get("unique_id")
        if type(unique_id) is not str or not unique_id:
            raise ValueError(ERROR_MESSAGES[3])
        for key, domain in _REQUIRED.items():
            suffix = "_" + key
            if not unique_id.endswith(suffix):
                continue
            prefix = unique_id[: -len(suffix)]
            if not prefix:
                raise ValueError(ERROR_MESSAGES[3])
            if key in mapping:
                raise ValueError(ERROR_MESSAGES[5])
            entity_id = row.get("entity_id")
            if (
                type(entity_id) is not str
                or not _ENTITY_ID.fullmatch(entity_id)
                or entity_id.split(".", 1)[0] != domain
            ):
                raise ValueError(ERROR_MESSAGES[7])
            if "disabled_by" not in row or (
                row["disabled_by"] is not None and type(row["disabled_by"]) is not str
            ):
                raise ValueError(ERROR_MESSAGES[3])
            if row["disabled_by"] is not None:
                raise ValueError(ERROR_MESSAGES[8])
            if entity_id in mapping.values():
                raise ValueError(ERROR_MESSAGES[5])
            identities.add(prefix)
            mapping[key] = entity_id
            break
    if set(mapping) != set(_REQUIRED):
        raise ValueError(ERROR_MESSAGES[4])
    if len(identities) != 1:
        raise ValueError(ERROR_MESSAGES[6])
    return mapping


def build_dashboard(registry, entry_id, language="es"):
    """Return a deterministic stock dashboard mapping; never mutate the input.

    All 36 current integration entities must be present and enabled. Identity is
    derived consistently from their unique IDs, not from a mutable BLE address.
    Only selected entity IDs are copied into the returned mapping.
    """
    if type(entry_id) is not str or not entry_id.strip():
        raise ValueError(ERROR_MESSAGES[1])
    if type(language) is not str or language not in ("es", "en"):
        raise ValueError(ERROR_MESSAGES[2])
    entities = _resolve(registry, entry_id)

    def text(es, en):
        return es if language == "es" else en

    def state(key, name):
        return {"entity": entities[key], "name": name}

    def attributes(key, names):
        # Names are actual public producer keys, not template evaluations.
        return [
            {
                "type": "attribute",
                "entity": entities[key],
                "attribute": name,
                "name": name,
            }
            for name in names
        ]

    def card(title, rows):
        return {
            "type": "entities",
            "title": title,
            "show_header_toggle": False,
            "entities": rows,
        }

    def markdown(content):
        return {"type": "markdown", "content": content}

    def button(key, name):
        # Stock toggle dispatches button.press only on a user's tap. No render
        # actions, service templates or generic write service are configured.
        return {
            "type": "button",
            "entity": entities[key],
            "name": name,
            "tap_action": {"action": "toggle"},
            "hold_action": {"action": "none"},
            "double_tap_action": {"action": "none"},
        }

    cards = [
        markdown(
            text(
                "## Reloj: observaciones y acciones explícitas\n"
                "Este panel no conecta ni escribe al abrirse. Conectar puede estar limitado por los temporizadores "
                "del modo instalado; Actualizar usa la conexión y Cerrar solicita su cierre. "
                "El modo de retención larga es un candidato de producto separado: no se supone instalado ni aceptado. "
                "Elija el modo automático en las opciones de la integración solo si lo desea y después de instalar "
                "una versión probada; este panel no lo activa.",
                "## Watch: observations and explicit actions\n"
                "Opening this dashboard neither connects nor writes. Connect may be bounded by the installed mode's "
                "timers; Refresh uses the connection and Close requests cleanup. Long retained mode is a separate "
                "product candidate, not assumed installed or accepted. Choose automatic mode in the integration "
                "options only if wanted and after installing a tested release; this dashboard never enables it.",
            )
        ),
        card(
            text("Conexión y sesión acotada", "Connection and bounded session"),
            [
                state(
                    "interactive_connected",
                    text("Conexión interactiva", "Interactive connection"),
                )
            ]
            + attributes(
                "interactive_connected",
                (
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
                ),
            ),
        ),
        button("connect_interactive", text("Conectar reloj", "Connect watch")),
        button(
            "read_interactive", text("Actualizar conexión", "Refresh connected watch")
        ),
        button("close_interactive", text("Cerrar conexión", "Close watch connection")),
        markdown(
            text(
                "### Lectura única, separada de la sesión\nLeer todo abre una operación de lectura única; "
                "no es Actualizar ni una suscripción. No está disponible mientras una sesión reserva la conexión.",
                "### One-shot read, separate from the session\nRead All starts a one-shot read; "
                "it is not Refresh or a subscription. It is unavailable while a session reserves the connection.",
            )
        ),
        button("read_all", text("Leer todo — una vez", "Read All — one shot")),
        card(
            text("Resultado de lectura única", "One-shot read result"),
            attributes(
                "read_all",
                (
                    "phase",
                    "wait_expires_at",
                    "result",
                    "group_results",
                    "cleanup_result",
                ),
            ),
        ),
    ]
    observed = [
        state(
            "last_alarm_read",
            text("Última lectura correcta: alarmas", "Last successful alarm read"),
        )
    ]
    drafts = []
    for slot in range(1, 6):
        key = f"alarm_{slot}"
        observed += [
            state(key, text(f"Alarma {slot} observada", f"Observed alarm {slot}"))
        ]
        observed += attributes(key, ("enabled", "fresh"))
        for suffix, es, en in (
            ("time_draft", "Hora (borrador)", "Time (draft)"),
            ("enabled_draft", "Activación (borrador)", "Enabled (draft)"),
        ):
            draft_key = f"{key}_{suffix}"
            drafts += [state(draft_key, f"{slot}: {text(es, en)}")]
            drafts += attributes(
                draft_key,
                ("pending", "observed_value", "observed_at", "observed_fresh"),
            )
    observed += [
        state(
            "hourly_signal", text("Señal horaria observada", "Observed hourly signal")
        )
    ]
    observed += attributes("hourly_signal", ("fresh",))
    drafts += [
        state(
            "hourly_signal_draft",
            text("Señal horaria (borrador)", "Hourly signal (draft)"),
        )
    ]
    drafts += attributes(
        "hourly_signal_draft",
        ("pending", "observed_value", "observed_at", "observed_fresh"),
    )
    cards += [
        card(
            text("Alarmas observadas — solo lectura", "Observed alarms — read only"),
            observed,
        ),
        markdown(
            text(
                "### Editor por etapas\nCambiar un borrador NO toca el reloj. Aplicar envía cambios explícitos "
                "y verifica por lectura. Descartar solo borra el borrador, no deshace escrituras. "
                "La barrera duradera ante escritura incierta puede exigir una lectura de reconciliación: "
                "revise needs_reconciliation, write_submitted y apply_result antes de reintentar. "
                "La activación de alarmas y SIG sigue sin verificación física.",
                "### Staged editor\nChanging a draft does NOT touch the watch. Apply explicitly submits changes "
                "and verifies by reading. Discard only clears the draft; it does not undo writes. "
                "The durable uncertain-write barrier may require a reconciliation read: check "
                "needs_reconciliation, write_submitted and apply_result before retrying. "
                "Alarm enabling and SIG remain physically unverified.",
            )
        ),
        card(
            text(
                "Borradores locales — no son observaciones",
                "Local drafts — not observations",
            ),
            drafts,
        ),
        button("apply_alarm_draft", text("Aplicar borrador", "Apply draft")),
        button("discard_alarm_draft", text("Descartar borrador", "Discard draft")),
        card(
            text(
                "Estado del borrador y reconciliación",
                "Draft and reconciliation status",
            ),
            attributes(
                "apply_alarm_draft",
                (
                    "draft_pending",
                    "draft_revision",
                    "apply_revision",
                    "apply_phase",
                    "apply_result",
                    "write_submitted",
                    "needs_reconciliation",
                    "verified_at",
                    "cleanup_result",
                ),
            ),
        ),
        card(
            text("Pasos — última observación", "Steps — last observation"),
            [
                state("steps_today", text("Pasos de hoy", "Steps today")),
                state(
                    "last_sync",
                    text(
                        "Última lectura correcta: pasos", "Last successful steps read"
                    ),
                ),
            ]
            + attributes("steps_today", ("record_date", "fresh")),
        ),
        markdown(
            text(
                "El gráfico muestra observaciones de Recorder de HA, NO el historial diario importado del reloj "
                "ni muestras retroactivas. Requiere Recorder con esta entidad incluida. Sin datos no se inventan fechas ni ceros.",
                "This graph shows HA Recorder observations, NOT imported watch daily history or backfilled samples. "
                "Recorder must include this entity. Missing data is not replaced by invented dates or zeroes.",
            )
        ),
        {
            "type": "history-graph",
            "title": text("Pasos registrados por HA", "Steps recorded by HA"),
            "hours_to_show": 24,
            "entities": [entities["steps_today"]],
        },
    ]
    zones = [
        state(
            "last_time_zone_read",
            text("Última lectura correcta: zonas", "Last successful time-zone read"),
        )
    ]
    for key, es, en in (
        (
            "home_standard_utc_offset",
            "Desfase UTC estándar principal",
            "Home standard UTC offset",
        ),
        (
            "secondary_standard_utc_offset",
            "Desfase UTC estándar secundario",
            "Secondary standard UTC offset",
        ),
    ):
        zones += [state(key, text(es, en))] + attributes(
            key, ("configured_dst_increment_minutes", "dst_rule_code", "fresh")
        )
        if key == "home_standard_utc_offset":
            zones += attributes(key, ("primary_dst_on", "primary_dst_auto"))
    settings = []
    for key, es, en, extra in (
        (
            "time_format",
            "Formato horario observado",
            "Observed time format",
            ("button_tone_enabled", "light_duration_mode"),
        ),
        (
            "automatic_time_adjustment",
            "Ajuste automático configurado",
            "Configured automatic adjustment",
            ("adjustment_minute",),
        ),
        (
            "timer_encoded_duration",
            "Duración codificada del temporizador",
            "Encoded timer duration",
            ("encoded_hours", "encoded_minutes", "encoded_seconds"),
        ),
    ):
        settings += [state(key, text(es, en))] + attributes(
            key, ("last_read", "fresh", *extra)
        )
    cards += [
        markdown(
            text(
                "### Configuración observada, solo lectura\nDesfases estándar y DST configurado no son el desfase efectivo actual. "
                "La duración codificada NO es una cuenta atrás en marcha. No hay relojes en vivo ni sincronización de hora "
                "ni servicio genérico de escritura en este panel. fresh es evidencia del productor, no una garantía de vigencia por edad.",
                "### Observed configuration, read only\nStandard offsets and configured DST are not the current effective offset. "
                "Encoded duration is NOT a running countdown. This dashboard provides no live clocks, time synchronization "
                "or generic write service. fresh is producer evidence, not an age-based freshness guarantee.",
            )
        ),
        card(text("Zonas horarias observadas", "Observed time zones"), zones),
        card(
            text("Ajustes y temporizador observados", "Observed settings and timer"),
            settings,
        ),
    ]
    for key, es, en in (
        ("read_steps", "Leer pasos — una vez", "Read steps — one shot"),
        ("read_alarms", "Leer alarmas — una vez", "Read alarms — one shot"),
        ("read_time_zones", "Leer zonas — una vez", "Read time zones — one shot"),
    ):
        cards += [
            button(key, text(es, en)),
            card(text(es, en), attributes(key, ("phase", "wait_expires_at"))),
        ]
    return {
        "title": "Casio ABL-100WE",
        "views": [
            {
                "title": text("Reloj", "Watch"),
                "path": "watch",
                "type": "masonry",
                "cards": cards,
            }
        ],
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes attacker-controlled argv values.
        self.exit(2, "Invalid command line; use --help.\n")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Invalid registry JSON.")
        result[key] = value
    return result


def main(argv=None):
    """CLI exit: 0 success, 2 safe refusal; output is JSON and valid YAML 1.2."""
    parser = _Parser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline entity registry JSON export")
    parser.add_argument(
        "--entry-id", required=True, help="Explicit selected config_entry_id"
    )
    parser.add_argument("--language", default="es", choices=("es", "en"))
    parser.add_argument(
        "-o", "--output", type=Path, help="Output JSON/YAML path (default: stdout)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Explicitly permit replacing existing output, including input",
    )
    args = parser.parse_args(argv)
    try:
        source = args.input.read_text(encoding="utf-8")
        registry = json.loads(source, object_pairs_hook=_json_object)
    except (OSError, UnicodeError, ValueError, RecursionError):
        print("Cannot read a valid registry JSON export.", file=sys.stderr)
        return 2
    try:
        dashboard = build_dashboard(registry, args.entry_id, args.language)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    result = json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n"
    try:
        if args.output is None:
            sys.stdout.write(result)
        else:
            # Exclusive create refuses aliases/symlinks/existing input too. Input
            # was fully read and validated before an explicit --force overwrite.
            with args.output.open(
                "w" if args.force else "x", encoding="utf-8"
            ) as output:
                output.write(result)
    except (OSError, UnicodeError, ValueError):
        print(
            "Cannot write output; existing files require --force. Check the destination.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
