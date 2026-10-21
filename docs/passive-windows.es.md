# Captura pasiva de oportunidades Bluetooth

**Experimental.** `observe_windows` registra anuncios recibidos por el propietario Bluetooth que Home Assistant ya utiliza. No conecta al reloj, no lee sus grupos y no ajusta la hora. La publicación de esta opción no demuestra que esté instalada ni que haya capturado una ventana real.

## Qué sabemos de las cuatro ventanas

El manual del módulo 3565 describe una conexión con el teléfono para corregir la hora alrededor de **00:30, 06:30, 12:30 y 18:30**, dejando el reloj en el modo de indicación de la hora. Es el comportamiento documentado para la aplicación oficial, no una garantía de compatibilidad con esta integración.[1]

La corrección no se ejecuta si está desactivada. Después de intercambiar la hora local y la hora mundial, o de ajustar manualmente la hora del reloj, la corrección automática queda suspendida durante 24 horas.[2]

Estas páginas no concretan tolerancia, duración del anuncio, zona IANA ni reglas UTC/DST. Tampoco demuestran que el campo de minuto leído por la integración sustituya los cuatro `:30` de este reloj. No se programan intentos a esas horas ni se cambia ningún ajuste para provocar una ventana.

## Activar una observación, no una conexión

1. Comprueba que está instalada una versión que incluya `observe_windows`. No borres ni vuelvas a añadir el dispositivo para actualizarlo.
2. En **Ajustes → Dispositivos y servicios → Casio ABL-100WE → Configurar**, selecciona solo la opción de observación pasiva. Las cuatro opciones son mutuamente excluyentes y están desactivadas por defecto.
3. No pulses **Iniciar escucha** para esta captura: ese control selecciona el modo de conexión retenida y desactiva la observación pasiva. El interruptor de conexión indica ese modo retenido, no si la captura pasiva está activa.
4. Deja el reloj al alcance del proxy habitual. No es necesario abrir CNCT ni cambiar hora, emparejamiento o firmware. Una apertura manual de CNCT o actividad FIND puede producir anuncios indistinguibles de los automáticos.
5. **Antes de desactivar, recargar o reiniciar**, descarga los diagnósticos de la entrada desde su menú. Consulta `window_observation`. Los contadores son volátiles y se reinician al recargar.
6. Para terminar, desactiva la opción en Configurar. El control explícito de parar conexión y escucha también desactiva la observación pasiva.

Los modos `auto_read_steps`, `auto_read_all` y `auto_connect_retained` sí pueden iniciar operaciones con el reloj. No son modos de captura pasiva y no deben habilitarse para obtener este registro.

## Cómo interpretar los diagnósticos

- `enabled`, `bound` y `suspended` describen el observador, no el estado de conexión del reloj.
- `observation_started_utc` identifica esta época de observación. `coverage_since_utc` indica el inicio de la cobertura actual sin interrupción, o es nulo si no hay cobertura.
- `coverage_gap_count` cuenta interrupciones de cobertura. Un corte del origen, suspensión o cambio de generación impide afirmar escucha continua.
- `raw_record_count` cuenta registros RAW válidos entregados al observador, **no transmisiones de radio únicas**. Un lote puede contener registros repetidos.
- `burst_count` agrupa esos registros con una separación de silencio de al menos 30 segundos, medida con el reloj monotónico del host. Es una heurística, no un identificador físico de CNCT.
- `recent_bursts` conserva como máximo 16 resúmenes con primera y última recepción UTC y número de registros. Los contadores se saturan; no existe un historial ilimitado.
- Las horas UTC son de **recepción en Home Assistant**, no del reloj ni del origen RF. Un fallo del reloj de medida detiene la cobertura conservadoramente; no se inventan muestras.
- `cause` siempre es `unknown`. Coincidir con las 06:30 no convierte un anuncio en una sincronización automática demostrada.

Solo se cuentan anuncios de la dirección exacta, con tipo público o aleatorio aceptado y un nombre ABL-100 reconocido dentro del propio registro. El nombre recordado por HA no sustituye al nombre ausente en un paquete. Por tanto, cero registros significa **cero anuncios que cumplieron esos filtros durante la cobertura disponible**, no ausencia demostrada de actividad del reloj ni cuatro ventanas perdidas.

La captura reutiliza un callback local de la conexión ESPHome existente: no añade cliente, escáner ni suscripción remota. No crea tickets de admisión, lecturas, reintentos o escrituras GATT. Los resúmenes no incluyen dirección, nombre, payload ni RSSI. No se publican actualizaciones de entidad o Recorder por paquete y se conservan las 37 identidades existentes.

## Límite de aceptación

Hay que distinguir cuatro resultados: **anuncio visto**, **intento de conexión admitido**, **grupo leído** y **hora del reloj corregida**. Esta opción solo recoge el primero. No repara las pérdidas de enlace nativo observadas anteriormente y no valida los refrescos retenidos.

Una prueba física útil debe cubrir los cuatro periodos nominales y conservar sus interrupciones reales de cobertura. Un resultado sintético o un anuncio cercano a una hora prevista no sustituye esa prueba. Los datos deben descargarse antes de detener la captura; no hay persistencia ni recuperación de intervalos no observados.

## Sources

[1] https://support.casio.com/global/en/wat/manual/3565_en/UQIDSYbjsuepmm.html — official-auto
[2] https://support.casio.com/global/en/wat/manual/3565_en/NHCXSYtcslbhqu.html — official-troubleshooting
