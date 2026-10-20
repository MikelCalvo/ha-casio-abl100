# Editar una alarma desde Home Assistant — piloto experimental

## Qué está confirmado

El componente instalado es `badfe909c74f63a2b2d81c69b3b872df596e1b1c`:
34 archivos y 36 identidades de entidad, conservando las 23 anteriores.
Esta guía no instala nada ni autoriza otra prueba física.

Se cambió **solo la hora de la alarma 5, desactivada**, de `00:00` a `00:01`
y después a `00:00`. La lectura nativa verificó ambos resultados y conservó
el resto de alarmas, SIG y la desactivación de la alarma 5. En la pantalla de
12 horas se confirmó **`12:01` y luego `12:00`**; no son lecturas del reloj en
marcha. El cierre explícito y las comprobaciones posteriores fueron correctos,
pero el reloj mostró **ERR las dos veces**. Su causa sigue sin conocerse.
Los aproximadamente 20 segundos son una estimación del operador, no una medida.

Activar/desactivar alarmas o SIG y recuperar una escritura incierta siguen
validados solo con IO sintética. No hay garantía de fiabilidad cotidiana.
Mantén **ambos modos automáticos desactivados**.

## Flujo manual para un cambio acordado

1. Acerca el reloj al proxy ESPHome conectable y evita que el teléfono u otros
   clientes se conecten. No desemparejes ni reinicies el reloj. Anota el valor
   original y el cambio/restauración previstos antes de empezar.
2. Pulsa **Conectar reloj** una vez. En **Conexión interactiva**, espera
   `phase: waiting` y un `wait_expires_at` futuro. Entonces mantén **C/MODE,
   abajo a la izquierda, al menos tres segundos** para abrir CNCT. Conectar no
   lee los datos de aplicación ni escribe ajustes.
3. Espera `on`, `link_state: connected` y `usable: true`. Pulsa **Actualizar
   reloj conectado** y revisa las cinco alarmas, SIG y su frescura. Durante esta
   reserva no uses los botones de lectura de una sola sesión.
4. Cambia únicamente **Borrador de hora de alarma 5** si ese es el campo acordado.
   Las horas se expresan en formato de 24 horas y precisión de minuto (`HH:MM`
   o `HH:MM:00`); los segundos distintos de cero se rechazan. No toques los
   borradores de activación o señal horaria para un cambio solo de hora.
5. Editar un borrador **no escribe el reloj**. Revisa `pending`, `observed_value`,
   `observed_at` y `observed_fresh`; pulsa **Aplicar borrador de alarmas** una vez.
   No repitas la pulsación por demora o por ver ERR. Comprueba el resultado
   según la tabla siguiente y compara la pantalla de alarma del reloj.
6. Termina con **Cerrar conexión del reloj**, espera su finalización y revisa
   `cleanup_result`. Mira la pantalla terminal por separado: éxito nativo y
   ausencia de ERR no son equivalentes. Restaurar el valor original exige otro
   cambio explícito de borrador, Aplicar y lectura; no hay reversión automática.

Los nombres llevan el prefijo del dispositivo; los ID concretos dependen del
idioma y de tus cambios de nombre. Los atributos del borrador/Aplicar son más
útiles que la marca de tiempo del botón.

## Cómo interpretar el resultado

| `apply_result` | Qué hacer |
| --- | --- |
| `verified` | La nueva lectura coincide con el cambio solicitado y la conservación comprobada. El borrador aplicado se limpia; compara también la pantalla. No certifica el cierre ni corrige ERR. |
| `unchanged` | La lectura nueva ya coincide; no se necesitó SET y el borrador se limpia. |
| `conflict` | El estado cambió respecto al conocido al empezar el borrador. Revisa la observación, descarta y prepara de nuevo solo tras decidir el cambio. |
| `mismatch` / `preservation_mismatch` | Hay una lectura válida, pero no la coincidencia esperada. Conserva el resultado y detente; no es un éxito ni justifica repetir automáticamente. |
| `unknown` | Puede haberse enviado una escritura sin resultado verificable. No vuelvas a Aplicar: requiere reconciliación por lectura explícita. |
| `failed_before_write` | La operación falló antes de la posible escritura; revisa el estado y el cierre, sin reintento automático. |

Con `needs_reconciliation: true`, solo una **Actualizar reloj conectado**
explícita que obtenga el grupo completo de alarmas puede desbloquear una nueva
aplicación. Si ya no hay enlace utilizable, primero cierra y coordina otra
conexión; no fuerces Aplicar ni borres archivos internos. La lectura no convierte
el resultado histórico `unknown` en `verified`. Esta recuperación no se ha
probado físicamente. **Descartar borrador de alarmas** elimina cambios locales,
no deshace una escritura ni borra la incertidumbre. Recargar/reiniciar descarta
borradores, pero conserva la barrera de seguridad si existe.

## Límites que siguen vigentes

La sesión es acotada, no permanente. Aunque el borrador sea de un solo campo,
la escritura usa SET15 y SET16 del grupo completo; no promete conservar bytes
opacos arbitrarios. Las notificaciones carecen de ID de transacción, por lo
que una respuesta antigua idéntica puede ser indistinguible de una nueva.
No se sincroniza la hora, no hay otros escritores de ajustes, END, reinicio,
reconexión ni compensación automática para resolver ERR.

Detalles: [contrato de edición](native-alarm-editing.md),
[sesión interactiva](native-interactive-session.md) y
[validación actual e histórica](validation.md).
