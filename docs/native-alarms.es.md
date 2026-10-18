# Lectura nativa de alarmas (candidato local)

Este cambio usa la conexión API que Home Assistant ya mantiene con ESPHome.
No instala nada, reinicia servicios ni actúa sobre dispositivos durante las pruebas.
La aceptación física de esta ruta compartida con HA sigue pendiente.

## Uso

1. Mantener desactivada la lectura automática durante una prueba coordinada.
2. Pulsar **Leer alarmas** una sola vez.
3. Comprobar los atributos de ese mismo botón: `phase: waiting` y
   `wait_expires_at` en UTC, todavía en el futuro. No hay entidades ni servicios
   de diagnóstico nuevos.
4. Solo después abrir `CNCT` en el reloj, manteniendo C/MODE (inferior izquierdo)
   al menos tres segundos. Evitar que el teléfono u otro cliente se conecte.
5. Esperar al resultado. La espera del siguiente anuncio exacto dura como máximo
   360 segundos. Después se realiza un intento nativo, sin reintento automático.
   `reading` indica la lectura; `idle`, su finalización. Al descargar la entrada
   se limpia el estado a `closed` y se borra el vencimiento.

El botón sigue en curso durante la espera y lectura. Un cliente HTTP que lance
la operación debe admitir ese presupuesto; perder la respuesta HTTP no demuestra
que HA haya cancelado la operación. No pulsar otra vez para sondear el estado.
La lectura de pasos conserva su procedimiento anterior y comparte el bloqueo.

## Selección y seguridad

Se utiliza el origen Bluetooth exacto del dispositivo almacenado cuando existe,
y se contrasta con la identidad Bluetooth del ESPHome cargado. Sin origen
almacenado tras un reinicio, solo se admite un proxy disponible e inequívoco,
capaz de conexiones activas, caché remota y anuncios RAW. Siempre se espera un
anuncio nuevo del destino exacto. Un origen perdido o ambiguo, un destino ya
asignado/seguido o la falta de capacidad provocan rechazo, no selección arbitraria.
Un escáner identificado como no ESPHome conserva la ruta Bleak anterior.

La operación mantiene el `WatchSession`, sus tareas, bloqueo y publicación
existentes. Las 11 identidades de entidades no cambian. No se construye otro
`APIClient`, no se deshabilita ESPHome y no se envía ninguna suscripción de
anuncios: el observador es local a la conexión ya abierta.

`native_api.py` toma prestados los métodos BLE instalados de aioesphomeapi,
pero resuelve todas sus llamadas internas sobre la generación capturada. Esto
incluye la desconexión interna del método de conexión al cancelarse; una guarda
solo alrededor de la limpieza externa no bastaría. El mapa de notificaciones
es propio de la operación y la limpieza elimina únicamente sus callbacks
originales. No se modifica ni desconecta el cliente API compartido.

La adquisición de notificaciones se protege de la cancelación del llamante y
se espera hasta terminar, con el timeout nativo de 10 segundos. Se recupera el
resultado o fallo incluso tras cancelaciones repetidas, se retira el callback
y se conserva la cancelación del llamante. No quedan futuros `shield` huérfanos.
La limpieza depende de que el backend respete sus límites; no puede forzarse
la terminación de un backend arbitrariamente no cooperativo.

## Protocolo y publicación

Conexión nativa con timeout de 30 segundos, desconexión de cinco, sin caché y
con el tipo de dirección del anuncio RAW. Solo se continúa con
`connected=true,error=0`, después servicios (10 segundos), notificaciones,
CCCD `0100` y las consultas GET15/GET16 del transporte existente (etapas de
12 segundos). No se usa el límite externo Bleak de 16 segundos para esta ruta.

La publicación de las cinco alarmas, SIG y última lectura ocurre tras una
lectura completa validada, limpieza y comprobación final de propiedad. Un fallo
retiene los valores y fecha buenos con `fresh: false`. No cambia la frescura
ni la fecha de pasos. No se consultan hora/zonas, ni se añaden escrituras de
ajustes, emparejamiento, AppInfo, END o cambios de modo del escáner.

## Archivos

- `__init__.py`: selección de ruta dentro de la sesión y estado acotado del botón.
- `button.py`: escucha de la sesión y atributos públicos seguros.
- `native_alarms.py`: selección, espera manual y vida de la lectura nativa.
- `native_api.py`: fachada de generación fija y espera protegida con JOIN.
- `native_alarm_adapter.py`: adaptación al transporte de alarmas ya existente.
- `test_native_alarm_runtime.py`: HA real; solo el intercambio nativo externo es
  sintético. Incluye servicios, registro, publicación, retención, cancelación,
  descarga, generación API, selección y coexistencia con pasos.
- `test_native_alarm_adapter.py`: pruebas del adaptador y protocolo.
- `test_runtime.py`: ajuste preciso de nueve a diez listeners por el botón;
  se conservan las comprobaciones de limpieza y las 11 identidades.
- `README.md`: semántica de armado manual y comandos de prueba actualizados.

Las pruebas usan Home Assistant 2026.9.4, aioesphomeapi 46.2.0,
bleak-esphome 4.0.0 y habluetooth 6.26.11. Las pruebas sintéticas no prueban
aceptación física de la conexión compartida, fiabilidad cotidiana ni la pantalla
terminal del reloj. Los éxitos nativos independientes anteriores son otra capa.
