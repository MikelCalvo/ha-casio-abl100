# Comprobación de las mejoras de uso

## Tres niveles distintos

1. **Verificación offline:** pruebas del protocolo, del ciclo de vida real de Home Assistant con transporte sintético y de la composición entre funciones. No se conecta al reloj.
2. **Instalación:** sustitución controlada del componente, conservación de las identidades y los datos, y comprobación de carga. Publicar un commit no instala esa versión.
3. **Prueba física coordinada:** el usuario abre CNCT y observa el reloj. Los resultados de los dos niveles anteriores no sustituyen esta prueba.

Las funciones descritas aquí requieren una versión conjunta revisada e instalada antes de la prueba. La verificación offline de su código no equivale a instalación ni a aceptación física. No habilites un modo automático solo porque un ejemplo lo mencione.

## Antes de la primera prueba física

- Confirmar la revisión exacta instalada y las opciones actuales. No reutilizar una señal CNCT ni un proceso de una prueba anterior.
- Conservar las 36 entidades originales, personalizaciones, observaciones y barrera de reconciliación. Un valor restaurado es antiguo, no una lectura nueva.
- Preparar una única ruta BLE: Home Assistant y su proxy ESPHome seleccionado. Cerrar la aplicación del móvil y otros clientes durante la prueba.
- Mantener las escrituras de ajustes fuera de la primera prueba de conexión. Editar un borrador no escribe; Aplicar es otra acción explícita.
- Acordar un tiempo de observación concreto. Quitar los límites de política de HA no demuestra una sesión física ilimitada ni modifica la configuración de desconexión del reloj.

## Prueba inicial: conexión automática y lectura, sin SET

1. Activar únicamente el modo de conexión retenida en la versión ya instalada y revisada. Los otros modos automáticos deben estar desactivados.
2. Comprobar que activar o recargar la integración no conecta por anuncios antiguos y no aplica borradores.
3. Tras una indicación nueva de disponibilidad del sistema, abrir CNCT en el reloj.
4. Verificar una conexión y una lectura inicial de los seis grupos. Comparar los datos observados y su frescura, no solo el resultado HTTP.
5. Mantener el enlace durante el intervalo acordado y pedir una actualización sobre esa misma conexión. Registrar por separado enlace nativo, datos recibidos y pantalla del reloj.
6. Cerrar desde el reloj con una pulsación corta de MODE cuando se indique. Confirmar pantalla principal y presencia o ausencia de ERR. HA puede clasificar el final como `link_lost`; eso no identifica por sí solo la causa radio ni un fallo visible.
7. Comprobar limpieza del propietario original y ausencia de reconexión inmediata al mismo anuncio. Una oportunidad nueva posterior debe tratarse como otra sesión, no como un reintento oculto.
8. Dejar las opciones en el estado acordado. Una sesión correcta no autoriza nuevas escrituras ni demuestra fiabilidad de uso diario o consumo de batería.

El botón de cierre de HA sigue siendo una salida de emergencia. Su resultado no se confunde con el cierre voluntario desde el reloj. No se afirma que esta política elimine universalmente ERR.

## Panel

El panel se genera desde un registro de entidades exportado y una entrada seleccionada. Debe conservar los IDs personalizados, no usar nombres traducidos supuestos ni incluir el otro reloj.

- Las alarmas observadas están separadas de sus borradores.
- Aplicar y Descartar son acciones explícitas. El resultado y la necesidad de reconciliación deben seguir visibles cuando el control no esté disponible.
- Comprobar también la presentación móvil. Las pruebas de estructura y de acciones estándar no son una prueba visual de navegador.
- El gráfico estándar muestra observaciones de Recorder. No representa una importación retroactiva del historial del reloj.

## Historial de pasos

Tras una lectura válida, comprobar fechas, valores desconocidos y procedencia del archivo. Solo se conservan fechas realmente recibidas, con un máximo de 366 entradas; se exponen las 30 más recientes. El recuento actual es provisional. Una lectura posterior de historial puede sustituirlo sin inventar un total al llegar medianoche.

Recargar no debe producir BLE, renovar timestamps ni transformar los datos restaurados en frescos. Después de medianoche, el valor de hoy puede no estar disponible mientras los metadatos del historial siguen visibles. No rellenar huecos con ceros.

## Escrituras que no se incluyen en esa prueba

La preparación pura de autoajuste, zonas y DST no añade controles nativos funcionales. BASIC, temporizador y hora actual siguen teniendo límites de protocolo documentados en [settings-write-boundaries.es.md](settings-write-boundaries.es.md). No probarlas mediante servicios genéricos ni copiando bytes de otros modelos.

La activación de alarmas y SIG requiere otra intención reversible acordada, baseline completo, Aplicar y readback completo. Si una escritura queda incierta, detener las demás y reconciliar solo con GET; nunca repetir ni compensar automáticamente.
