# Conexión automática retenida (experimental)

**Estado, 2026-10-21:** `e9a3855` está instalado con 37 identidades, incluido el interruptor de escucha. Dos conexiones manuales completaron la lectura de los seis grupos, pero la escucha automática y la estabilidad retenida siguen sin aceptación. Un control sin segunda lectura perdió el enlace 64,517 segundos después de la primera, antes del límite de HA; la causa sigue abierta y la pantalla final no se observó. Los tres modos automáticos quedaron desactivados. Una versión nueva publicada no equivale a una nueva instalación ni autoriza otra prueba física.

La nueva opción `observe_windows` es independiente y excluyente: **solo observa anuncios**, no conecta ni refresca. No uses Iniciar escucha para esa captura, porque selecciona el modo retenido y desactiva la observación pasiva. Consulta la [guía de captura de ventanas](passive-windows.es.md) para descargar los resúmenes antes de recargar o parar.

## Cuatro opciones, una como máximo

En **Configurar** hay cuatro booleanos, todos desactivados por defecto:

- `auto_read_steps`: lectura puntual de pasos/historial; conserva su significado.
- `auto_read_all`: lectura puntual de los seis grupos y cierre; conserva su significado.
- `auto_connect_retained`: conecta automáticamente ante una oportunidad RAW admisible, hace **una lectura completa inicial**, y conserva el propietario nativo original para refrescos periódicos y acciones explícitas.
- `observe_windows`: captura pasiva de anuncios en diagnósticos, sin conectar ni leer.

Los valores malformados o combinaciones incompatibles impiden la configuración. Guardar recarga solo la entrada seleccionada y conserva las 36 identidades anteriores y el nuevo interruptor de escucha, 37 en total. Desactiva las cuatro opciones antes de cambiar la dirección Bluetooth. La carga, recarga o restauración de observaciones antiguas no inicia una lectura por sí sola.

## Qué se conserva y qué no

El modo retenido observa la conexión ESPHome que ya pertenece a HA. Solo un anuncio RAW vivo de la dirección exacta, con nombre ABL-100 compatible y tipo público/aleatorio válido, puede aportar un permiso de un solo uso. Se comprueban propietario, entrada cargada, fuente y generación originales, cancelación y edad no futura de como máximo cinco segundos antes de mutar estado y al consumirlo. No hay segundo escáner, cliente, conexión ni espera de otro anuncio. El nombre es una heurística, no una identificación de CNCT: otras ventanas pueden coincidir.

Tras la lectura inicial, el mismo propietario vuelve a leer los seis grupos cada 30 segundos desde el final de la operación anterior. Si está ocupado, no solapa ni encola lecturas. No hay keepalive, aplicación automática de borradores ni escritura de hora. **Actualizar reloj conectado** reutiliza el enlace; **Aplicar borrador de alarmas** sigue exigiendo una intención explícita, su diario de seguridad y lectura de comprobación. Una edición local por sí sola no escribe. Las seis observaciones siguen siendo progresivas: un fallo posterior no borra grupos válidos anteriores ni fabrica ceros o datos nuevos.

Solo este modo suprime las políticas HA de inactividad (120 segundos) y duración absoluta (300 segundos); sus atributos de caducidad son `null`. No promete que el reloj mantenga físicamente el enlace. Se mantienen las adquisiciones y consultas finitas, la caducidad de 60 segundos de Actualizar/Aplicar, la comprobación periódica de generación y los cierres definitivos por pérdida nativa. Un Aplicar incierto conserva la barrera de reconciliación; no se reintenta al reconectar.

El botón manual **Conectar reloj** sin argumentos sigue siendo acotado y no lee datos de aplicación. El permiso automático no puede reutilizarse ni convertirse en una espera manual al invalidarse; tampoco puede pedirse retención ilimitada manual sin permiso.

El interruptor **Escuchar al reloj** guarda esta preferencia entre recargas y reinicios. El panel muestra una sola acción contextual de inicio/parada; no despierta un reloj que no anuncia. Véase el [flujo de uso diario](streamlined-connection.es.md).

## Cierre y nueva oportunidad

**Cerrar conexión del reloj**, descarga de la entrada, parada del observador y STOP de HA revocan el trabajo y esperan la limpieza del propietario original, incluso si la pequeña tarea de lectura inicial ya terminó. No se desconecta una generación ESPHome nueva para limpiar la antigua. La intención de cerrar no demuestra que la limpieza acabó: comprueba resultado, estado de enlace y pantalla por separado. Un backend no cooperativo aún puede retrasar la finalización; no se promete terminación forzada.

Una ráfaga se consume incluso estando ocupado, suspendido o tras un fallo. No queda trabajo automático en cola. Al terminar realmente la limpieza se establece una nueva marca de silencio: hacen falta al menos 30 segundos desde esa marca o el último RAW observado, lo que sea posterior. Los paquetes durante ocupación o posteriores al cierre pueden prolongarla. Así, un paquete antiguo entregado al cerrar no reconecta inmediatamente. Una oportunidad genuinamente posterior puede conectar otra vez. La medida es tiempo monotónico de recepción local, no una marca RF del reloj.

## Límites de la evidencia

Las pruebas atraviesan HA real y los métodos de la API nativa instalada con transporte externo sintético, duraciones reducidas y almacenamiento de pruebas. No son pruebas de radio. El código ya está instalado y el modo automático se intentó durante una prueba coordinada, pero falló antes de leer grupos. Las dos lecturas completas posteriores fueron manuales; no validan el disparador automático ni los refrescos periódicos.

Existe una muestra manual confirmada por el operador que incluyó una pulsación MODE; es **una observación**, no un experimento causal. No demuestra que MODE sea un keepalive, que una conexión deba durar indefinidamente o que esta política corrija ERR. Los ERR históricos, los resultados de datos y la limpieza nativa son observaciones distintas. La causa de ERR sigue abierta.
