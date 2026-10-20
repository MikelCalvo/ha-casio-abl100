# Ajustes: qué está preparado y qué sigue bloqueado

Se ha añadido un módulo **puro y todavía sin conectar a HA** para preparar cambios de configuración conservando todos los bytes ajenos al cambio:

- Configuración de ajuste automático: habilitación y minuto, sin sincronizar la hora actual.
- Offset estándar, incremento DST configurado y código numérico de regla en un slot indexado.
- Bits ON/AUTO del DST principal, sin modificar el estado secundario ni la cola compartida.

Valida el paquete completo, tipos y valores antes de aceptar incluso una operación sin cambios. Rechaza estados desconocidos en lugar de sustituirlos por valores por defecto. Una comparación posterior distingue igualdad completa, diferencia en lo solicitado y alteración de campos que debían preservarse.

**Esto no añade todavía controles de escritura funcionales en HA.** No envía BLE, no aplica borradores y no utiliza las observaciones restauradas como baseline de escritura. Faltan la operación nativa explícita, lectura fresca/conflictos, barrera duradera ante resultado incierto y verificación física. Los límites numéricos son los del byte, no una lista de zonas/reglas admitidas por el reloj.

Continúan bloqueados:

- **Hora actual/sincronización:** no tenemos una lectura validada de ninguno de los dos relojes en marcha para verificar el resultado. Conectar o consultar nunca ajusta la hora como efecto secundario.
- **12/24 h, tono y luz:** hay dos variantes SET13 de diferente longitud, frente a GET13 de 12 bytes. No se rellenan campos desconocidos con ceros.
- **Temporizador:** faltan la correspondencia de cero/60 minutos, el estado de cuenta atrás y la conservación de la cola desconocida.

Las fuentes exactas, APIs y pruebas sintéticas se describen en [settings-write-boundaries.md](settings-write-boundaries.md). No se ha probado ni modificado físicamente ninguno de estos ajustes nuevos.
