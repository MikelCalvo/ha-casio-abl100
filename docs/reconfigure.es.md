# Reconfiguración de la dirección Bluetooth

Esta función está instalada en el piloto. Se conservó la entrada y sus 11 identidades de entidades al cambiar a la dirección posterior al reset, expresamente confirmada por el usuario. La lectura de alarmas posterior se documenta [por separado](native-alarm-validation.md). Esto no demuestra que se haya resuelto `ERR` ni explica los errores de conexión anteriores.

Si el **mismo reloj físico** anuncia una dirección nueva tras borrar su emparejamiento:

1. En **Ajustes → Dispositivos y servicios → Casio ABL-100WE → Configurar**, desactiva **Leer pasos al detectar el reloj** y **Leer todos los datos al detectar el reloj** (la segunda opción es nueva en el candidato local, aún no instalado ni aceptado físicamente). La reconfiguración se rechaza mientras cualquiera esté activada o tenga un valor no booleano, incluso si cambia con el formulario abierto. No se cambian opciones sin avisar.
2. Abre **⋮ → Reconfigurar** en la entrada existente. No elimines y vuelvas a añadir la integración; no edites los archivos internos de Home Assistant.
3. Confirma que la dirección nueva corresponde a tu reloj. Selecciona expresamente el reloj detectado o escribe su dirección MAC completa con caracteres ASCII. La dirección escrita tiene prioridad. El nombre del modelo y la detección no demuestran la propiedad del dispositivo, y nunca se cambia la dirección automáticamente por coincidir el nombre.
4. Envía el formulario. Si el anuncio seleccionado ha desaparecido, vuelve a seleccionarlo o escribe la dirección exacta. La dirección actual y la identidad conservada de cualquier otra entrada están reservadas: no se pueden reutilizar. Una entrada sin identidad estable se rechaza, sin migraciones implícitas.

Solo cambia `data.address` de la entrada seleccionada. Se conservan su ID, `unique_id`, título, opciones, el ID del dispositivo y las 11 entidades existentes. La recarga nativa de esa entrada descarta las observaciones en memoria y limpia sus lecturas y suscripciones. Ni el formulario ni la carga establecen una conexión BLE. La lectura automática sigue desactivada, o ausente con valor predeterminado desactivado; podrás activarla por separado cuando corresponda.

Una lectura manual posterior usa la dirección nueva, pero una reconfiguración completada no equivale a una lectura física satisfactoria. No cambia emparejamiento, ajustes del reloj, protocolo, firmware ni parámetros de conexión. La lectura automática sigue siendo una función requerida; desactivarla temporalmente aísla este cambio, no convierte la integración en una solución solo manual.
