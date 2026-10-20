# Panel Casio con tarjetas estándar (sin conexión)

[English](dashboard.md)

Genera un panel Lovelace completo con `entities`, `button`, `markdown` e
`history-graph`: sin HACS, JavaScript personalizado, credenciales ni red. El
programa no consulta ni modifica Home Assistant, ESPHome ni el reloj. Abrir el
panel no pulsa botones. La instalación del panel es una decisión manual aparte.

## Generar y usar

Python 3.10+ y biblioteca estándar; no hace falta instalar HA para generar.
Desde la raíz del repositorio:

```sh
python3 tools/generate_dashboard.py registry.json \
  --entry-id 'ID_DE_LA_ENTRADA_SELECCIONADA' --output dashboard.yaml
# Español por defecto; inglés con --language en. Sin --output: stdout.
```

La salida es **JSON indentado, que también es YAML 1.2 válido**. La extensión
`.yaml` no implica una dependencia de PyYAML. Se puede pegar el documento completo
en el editor de configuración sin procesar de un panel Lovelace nuevo que usted
cree manualmente. No pegarlo en la configuración de una sola tarjeta. Revise las
referencias antes de guardar. No se instala ningún panel automáticamente.

No sobrescribe archivos existentes, tampoco la entrada. `--force` permite
explícitamente reemplazar el destino, **incluso el archivo de entrada**; evítelo
con la única copia de un registro. El éxito devuelve 0; un rechazo devuelve 2 con
un mensaje fijo que no imprime rutas, identificadores privados ni contenido del
registro. La creación sin `--force` es exclusiva, también ante alias/symlinks.

Ejemplo ejecutable con **dos relojes ficticios y nombres ya personalizados**:

```sh
python3 tools/generate_dashboard.py examples/dashboard-registry.synthetic.json \
  --entry-id selected-entry --output mi-ejemplo.yaml
```

`examples/dashboard.synthetic.es.yaml` es la salida realmente generada de esa
entrada. Sus entidades ficticias no funcionarán en su HA: genere su propio panel
con su exportación. No cambie nombres por traducciones supuestas.

## Entrada exacta y selección segura

Use una copia/exportación local del **registro de entidades**, no el registro de
dispositivos, estados, diagnósticos, `secrets.yaml` ni `core.config_entries`.
Este programa no obtiene una exportación en vivo ni necesita tokens. Si ya tiene
una copia de `.storage/core.entity_registry`, trabaje con esa copia, no la edite.
Trátela como privada y no la suba al repositorio.

Formas JSON admitidas:

1. Lista de filas: `[{...}, {...}]` (por ejemplo, resultado de lista del registro).
2. Envoltorio de almacenamiento: `{"data": {"entities": [{...}]}, ...}`.
3. Respuesta WebSocket ya exportada:
   `{"type": "result", "success": true, "result": [{...}], ...}`.

No se admite `{"entities": [...]}` sin `data`, ni respuestas fallidas, ni JSON
con claves duplicadas. Cada entidad requerida de la entrada seleccionada tiene
que incluir `config_entry_id`, `platform`, `unique_id`, `entity_id` y
`disabled_by` (`null` significa habilitada). Otros campos son ignorados.

Elija explícitamente el `config_entry_id` de las filas que correspondan a su
reloj y a `platform: "casio_abl100"`; no use la MAC como sustituto del ID de
entrada. Se requieren las **36 entidades actuales**, todas habilitadas. El
programa no habilita ninguna por usted: si se rechaza una entidad deshabilitada,
revise primero si realmente desea habilitarla y vuelva a exportar. No confunda
habilitada en el registro con disponible: una entidad puede estar
`unknown`/`unavailable` hasta una lectura o una conexión válida.

Se empareja entrada inmutable + plataforma + sufijo del `unique_id`. Se exige
el mismo prefijo de identidad en todas las entidades requeridas. Tras una
reconfiguración la dirección de transporte puede cambiar y el prefijo antiguo
permanecer: no se necesita la dirección actual. Se conservan exactamente los
`entity_id` personalizados; no se deducen del idioma ni del nombre del reloj.
Faltantes, duplicados, identidad mezclada, dominios incorrectos, tipos inválidos
e identificadores malformados se rechazan. Las demás entradas se ignoran. Las
entidades futuras no reconocidas no se añaden al panel.

La API pura importable es:

```python
from tools.generate_dashboard import build_dashboard
panel = build_dashboard(exportacion, entry_id, language="es")
```

Devuelve un diccionario nuevo, determinista incluso si se reordenan las filas;
no modifica la entrada. Los rechazos lanzan `ValueError` con mensajes fijos.
La salida copia solo los IDs seleccionados, no MAC/unique-ID/metadatos internos.
**No anonimiza los propios entity IDs**: si usted puso información privada en
ellos, seguirá en las referencias. Ni la salida ni la exportación se deben
publicar sin revisión.

## Qué significa el panel

- **Conexión:** `interactive_connected`, `phase`, `link_state`, `usable`,
  `wait_expires_at`, `idle_expires_at`, `session_expires_at`, `operation_result`,
  `group_results`, `cleanup_result`, `close_reason` y diagnóstico público de fallo.
  Conectar / Actualizar / Cerrar son acciones explícitas separadas. Conectar puede
  estar acotado por los temporizadores del modo instalado. La retención larga es
  un candidato de producto aparte, no se declara instalada ni aceptada aquí.
- **Leer todo — una vez:** distinto de Actualizar. Inicia una única operación de
  lectura, no una suscripción ni un sondeo automático; puede estar no disponible
  cuando una sesión reserva la conexión. También hay lecturas de grupo separadas.
- **Alarmas observadas:** cinco sensores de hora, con `enabled` y `fresh`, más la
  señal horaria observada y última lectura correcta. Son solo observaciones.
- **Borradores locales:** cinco `time` y cinco `switch`, más SIG, separados de los
  sensores observados. Cambiarlos **no toca el reloj**. Los campos `pending`,
  `observed_value`, `observed_at`, `observed_fresh` aclaran esa diferencia.
  Aplicar es el paso explícito de escritura y verificación; Descartar no deshace
  una escritura. `draft_pending`, revisiones, `apply_phase`, `apply_result`,
  `write_submitted`, `needs_reconciliation`, `verified_at` y `cleanup_result`
  proceden de los atributos reales de los controles. La barrera duradera ante
  escritura incierta puede exigir una lectura de reconciliación. Descartar no
  elimina esa barrera; no reintente a ciegas. Activación/SIG físicamente no
  verificados. Véase [el contrato del editor](alarm-editing.es.md).
- **Pasos:** `steps_today`, `record_date`, `fresh`, última lectura correcta. El
  gráfico estándar representa observaciones de **Recorder de HA**, **NO** el
  historial diario importado del reloj ni muestras retroactivas. Recorder debe
  incluir la entidad. No se fabrican fechas ni ceros. `daily_history` no se usa
  como si fuese una serie temporal de Recorder.
- **Zonas/ajustes/temporizador:** valores observados, fechas de última lectura y
  frescura donde el productor los ofrece. Desfase estándar/DST configurado no es
  necesariamente el desfase efectivo actual. Duración codificada **no es una
  cuenta atrás en marcha**. No se promete reloj en vivo ni sincronización de hora;
  no hay servicio genérico de escritura. `fresh` no es garantía de edad máxima.

Sin datos, HA puede mostrar desconocido, no disponible o `—` en atributos que
el productor todavía no publica. Eso no significa cero ni lectura correcta.
Los estados y atributos se actualizan desde HA, no con relojes inventados.

**Automático:** elija el modo automático en las opciones de la integración solo
si lo desea y después de instalar una versión probada. Este generador/panel no
lo activa ni cambia opciones silenciosamente.

## Verificación y límites

Ejecute las suites en procesos separados para no mezclar stubs con HA real:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider -q \
  tests/dashboard/test_generator.py
# Entorno de desarrollo con HA 2026.9.4 y su plugin de pruebas:
env -u PYTHONOPTIMIZE -u PYTEST_DISABLE_PLUGIN_AUTOLOAD \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='' python -B -m pytest \
  -p no:cacheprovider -o asyncio_mode=auto -q tests/dashboard/test_dashboard_runtime.py
```

La composición usa las fixtures nativas existentes y el formateador real del
registro HA, dos entradas, renombra las 36 entidades, cambia transporte sin
cambiar identidad, recarga y llama API/CLI. Solo el intercambio externo es
sintético: no hay BLE/ESP real ni instalación de panel.

Se comprobaron los setters `setConfig` de las tarjetas y de filas de atributo
extraídos de **frontend 20260826.7**, commit
`380e9b5a81ada29a1d187b4123c54fb3d6fbcc89` (versión fijada por HA 2026.9.4).
La puerta opcional `tests/dashboard/check_frontend.cjs` usa TypeScript y una
copia de los archivos upstream de `tests/dashboard/frontend-manifest.json`,
verifica sus SHA-256 y ejecuta esos métodos y la cadena real de acción de botón:

```sh
node tests/dashboard/check_frontend.cjs /ruta/a/frontend examples/dashboard.synthetic.es.yaml
# TYPESCRIPT_PATH puede indicar el módulo typescript si no es resoluble por Node.
```

Esto es **validación estructural y composición de acciones, no E2E visual**.
No se ha renderizado el panel en navegador ni medido su disposición móvil;
no se han realizado escrituras o validaciones físicas del reloj. No se ofrece
compatibilidad automática con versiones futuras que cambien los productores.
