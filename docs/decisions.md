# Decisiones y errores

Las demás páginas describen un sistema que funciona. Esta va de cómo llegó hasta ahí, que fue bastante menos limpio.

Está dividida en dos mitades. Arriba, decisiones que aguantaron, cada una con la condición bajo la cual dejaría de ser correcta. Abajo, ocho errores, con lo que costaron y lo que cambió.

---

## Primeras Decisiones

Cada una lleva su rollback, porque **una decisión sin pensar en el rollback es un error**.

### El agente corre nativo, no en un contenedor

El agente gestiona el runtime de contenedores: lista contenedores, lee sus logs, los reinicia. Meterlo dentro de aquello que gestiona crea una dependencia circular.

El modo de fallo es el peor de todos: **solo aparece cuando algo ya ha ido mal**. Mientras Docker funciona, no notas nada. El día que Docker tiene un problema, el agente que podría diagnosticarlo está caído exactamente por ese problema, y además no puede reiniciarse a sí mismo porque el mecanismo de reinicio es el que está roto.

Es el mismo principio por el que un sistema de monitorización no debe depender de la infraestructura que vigila.

*Rollback cuando* el agente deje de tocar el runtime de contenedores.

### SQLite en vez de Prometheus

Un nodo, intervalo de cinco minutos, unas pocas decenas de series.

En ese régimen, SQLite significa: ningún contenedor extra que mantener, ninguna configuración de scraping, ningún subsistema de retención que aprender, un lenguaje de consulta que ya conozco, y un fichero que entra en el backup nocturno sin hacer nada especial.

Prometheus y Grafana traerían descubrimiento automático de objetivos, un lenguaje de consulta pensado para series temporales, y alertas expresadas sobre las propias métricas. Todo eso vale mucho con muchos nodos. Con uno, son dos servicios más que **a su vez necesitan monitorización**, para resolver un problema que hoy cabe en un fichero de 3 MB.

*Rollback en torno a* diez nodos, o la primera vez que quiera reglas de alerta calculadas sobre las series en lugar de sobre las comprobaciones en crudo.

### Modelo local para las tareas programadas, cloud para lo interactivo

El trabajo programado corre sin supervisión, a menudo, y arrastra contexto. Su coste tiene que ser cero o crece mientras nadie mira. El interactivo tiene a un humano esperando, así que manda la calidad y la latencia.

*Rollback por tarea* — que es exactamente lo que pasó con las multi-tool, contado más abajo. La regla no es "local siempre"; es "local por defecto, y se sube lo que demuestre que lo necesita".

### Los monitores nunca lanzan excepciones

Todas las funciones de las capas de persistencia y de latidos capturan sus propias excepciones y devuelven un valor neutro.

En código de aplicación esto sería mala práctica y de las peores: tragarse excepciones esconde bugs y hace imposible depurar. En un monitor la ecuación se invierte. Una excepción no capturada mata el proceso, y eso significa **dejar de enterarte de todo lo demás** hasta que alguien lo mire. Perder una muestra de métricas es un daño trivial; perder la monitorización entera porque falló una escritura a disco es un daño grande.

El contrato está verificado en los propios tests: hay aserciones que apuntan la base de datos a rutas donde no se puede escribir y comprueban que la función devuelve un valor neutro en lugar de propagar.

*Rollback* en ningún caso. La alternativa es un monitor que se cae sin que nos enteremos.

### Agregar antes de borrar

La retención comprime el detalle a medias horarias antes de purgarlo, y la purga solo toca horas que **ya existen** en el agregado. Si la agregación falla, la purga no borra nada.

La red de seguridad vive en la propia consulta SQL y no en el código que la llama. Es deliberado: una comprobación en el llamante se puede saltar invocando las funciones en el orden equivocado, o desde un sitio nuevo que no la conocía. Una condición dentro de la consulta no se puede esquivar.

*Rollback* nunca. Es un seguro barato contra la clase de error que solo se comete una vez, porque la primera vez ya te has quedado sin los datos.

### La auditoría es un script, no un documento

El documento era el entregable que pedí. El script es el artefacto que sigue teniendo valor un año después, porque se puede volver a ejecutar y produce un número comparable con el anterior.

Un informe describe el día que lo escribiste. Un script describe hoy. Y los sistemas derivan.

---

## Errores

### El relay que se robaba el puerto a sí mismo

**Qué monté.** Un relay `socat` para reenviar `0.0.0.0:PUERTO` a `127.0.0.1:PUERTO`, con el objetivo de que un panel que solo escuchaba en el loopback fuera accesible desde la red local. El mismo número de puerto a los dos lados.

**Qué pasaba en realidad.** El panel ya estaba escuchando en `127.0.0.1:PUERTO`. Y el relay, como servicio del sistema, arrancaba **antes** en el proceso de inicio.

Así que en cada reinicio de la máquina la secuencia era: el relay se levanta y ocupa el puerto en todas las interfaces, incluida la de loopback. El panel intenta arrancar, encuentra el puerto ocupado, falla con `address already in use` y se rinde. Y el relay, que sigue vivo y haciendo su trabajo, reenvía diligentemente todo el tráfico entrante **hacia sí mismo**.

**Cómo se manifestaba.** El panel simplemente no estaba. El log del panel repetía la misma línea de error indefinidamente. Y el síntoma —"address already in use"— apunta mentalmente a un proceso zombi o a otra aplicación que se ha quedado el puerto, no al componente cuya única función es hacer accesible ese mismo panel.

Duró semanas, reapareciendo en cada reinicio, hasta que leí el fichero de configuración del relay con atención y vi el mismo número dos veces.

**Qué cambió.** El relay se eliminó, y el panel se configuró para escuchar directamente en la interfaz de red — que era lo que había que hacer desde el principio. El fichero del servicio está archivado con una nota que dice *no recrear*, precisamente porque el síntoma invita a volver a montar un relay.

**La lección no es "revisa tus puertos".** Es que un componente cuyo trabajo entero es la indirección apuntará encantado hacia sí mismo, y **nada en las herramientas va a objetar**. `socat` no tiene forma de saber que el destino es él mismo, ni motivo para comprobarlo. Cualquier capa de indirección —un proxy, un reenvío, un alias de DNS, una redirección— admite ciclos que solo se detectan leyendo la configuración con ojos frescos.

---

### Un escritor de métricas que nadie echó de menos

**Qué pasó.** Se desmanteló un componente que, entre otras cosas, escribía métricas de contenedores en una tabla SQLite. Nada más leía esa tabla.

**Cómo lo descubrí.** Setenta días después fui a buscar datos históricos para tener números que enseñar. Encontré 265.365 filas y una fecha máxima de diez semanas antes.

La primera hipótesis fue la evidente: algo se rompió. Fui a buscar el código que escribía ahí para ver por qué fallaba, y un `grep` sobre todo el repositorio devolvió **cero resultados**. No es que el escritor fallara: no existía. Lo habían borrado junto con su dueño.

**Por qué nadie se enteró.** Porque no había nada que se enterara. Ningún panel mostraba esos datos, ninguna alerta dependía de ellos, ninguna tarea los leía. Una tabla que deja de recibir escrituras no genera ningún síntoma; simplemente se queda quieta, y quieta se parece mucho a normal.

**Qué cambió.** El monitor que ya estaba corriendo ganó persistencia, y la tarea programada que mantiene esos datos ganó un latido, de modo que una segunda desaparición sí se notaría — porque ahora hay algo que espera activamente noticias suyas.

**Lección:** los datos que nadie lee dejan de producirse, y te enteras el día que los necesitas. Y no hay alerta posible para la desaparición de algo de lo que no depende nada, así que la única defensa es que algo dependa de ello a propósito.

---

### Un 100% de éxito que medía códigos de salida

**Qué reportaba.** El sistema de recuperación automática decía que sus 83 intervenciones habían tenido éxito. Las 83.

**Qué medía.** Registraba éxito cuando `docker restart` devolvía código 0, cosa que ocurre en cuanto el daemon **acepta** la orden. No espera a que el contenedor arranque, no comprueba que siga vivo, y no sabe nada de lo que pasa después.

**La evidencia.** Durante dos horas de abril, un contenedor estuvo a cero de memoria y cero de CPU en veinticuatro muestras consecutivas — es decir, parado. En esas mismas dos horas el healer anotó cuatro reinicios exitosos.

**Qué cambió.** Los reinicios esperan diez segundos y después consultan `docker inspect` para saber si el contenedor está realmente corriendo. `success` significa ahora *observado en pie diez segundos después*. Y un cortacircuitos detiene el bucle tras tres intentos en seis horas, escalando a un humano con los logs del contenedor adjuntos.

**Coste.** Un contenedor consumió el 59% de todas las intervenciones automáticas del sistema durante siete semanas **sin levantar una sola alerta**, porque el healer funcionaba y cada recuperación se notificaba como una buena noticia aislada.

Desarrollo completo, con los datos y el análisis, en [Fiabilidad](case-studies/reliability.md).

---

### Un control que reportaba lo contrario de la realidad

**Qué creía.** SSH estaba configurado para autenticación solo por clave. El fichero lo decía en dos sitios.

**Qué decía el control.** `sshd -T`, que es el volcado de configuración efectiva del propio demonio y en teoría la fuente de verdad, reportaba que la autenticación por contraseña estaba activa.

**Qué pasaba en realidad.** La autenticación por contraseña funcionaba. Con PAM en el camino de autenticación, PAM puede autenticar al margen de esa directiva.

Tres respuestas distintas a la misma pregunta, y la que importaba era la que nadie había hecho: **qué ocurre cuando alguien lo intenta**. Un solo intento de login con contraseña habría dado la respuesta en cinco minutos, en lugar de una cadena de hipótesis cada vez más rebuscadas sobre bloques `Match` y orden de includes.

**Qué cambió.** `AuthenticationMethods publickey`, que declara el requisito en lugar de deshabilitar una opción, y por tanto cierra también los caminos alternativos. Verificado intentando un login con contraseña y siendo rechazado.

**Y la parte que escuece:** mi script de auditoría tenía el mismo bug que mi configuración. Comprobaba `sshd -T`, así que **tras el arreglo seguía reportando un fallo** — porque `sshd -T` sigue diciendo lo mismo, simplemente ahora ya no importa. El control estaba equivocado antes y siguió equivocado después, en la dirección contraria. Hubo que corregirlo junto con el sistema.

Desarrollo completo en [Auditoría de seguridad](case-studies/security.md).

---

### "Nunca reiniciar" se convirtió calladamente en "nunca mirar"

**Qué quería.** Hay un contenedor deliberadamente apagado. Lo añadí a una lista `NEVER_RESTART` para que el monitor no lo levantara cada cinco minutos.

**Cómo está implementado.** El monitor se salta ese contenedor por completo, con un `continue` antes de ejecutar ninguna comprobación. Es la forma más directa de conseguir lo que pedí.

**La consecuencia.** Un contenedor que debería estar apagado puede estar corriendo días sin que nada lo reporte. Que es lo que pasó: cuatro días en pie, sano, invisible, hasta que leí una salida de `docker ps` en crudo por un motivo que no tenía nada que ver.

Nunca supe qué lo arrancó. Y ese es el punto: **no hay ningún registro, porque nada estaba mirando.**

**Qué cambió.** De momento solo la documentación, que ahora dice explícitamente que ese contenedor no tiene monitorización de ningún tipo y que conviene verificar su estado de vez en cuando. El arreglo bueno —una categoría que signifique *observar pero nunca actuar*, separada de *ignorar por completo*— está identificado y no construido.

**Lección:** las listas de exclusión confunden dos intenciones distintas. **"No actúes sobre esto" y "no mires esto" no son la misma instrucción**, y colapsarlas crea un punto ciego que además crece: cada elemento que añades a la lista por el primer motivo hereda gratis el segundo, y nadie lo nota porque la lista sigue teniendo el nombre del primero.

---

### El hardening rompió la monitorización, y la monitorización estaba mal

**Qué pasó.** Activar el modo sigiloso del firewall dejó al host sin responder a ICMP, que es exactamente su propósito. El monitor de uptime externo lo comprobaba por ping, y reportó el servidor principal como caído de inmediato.

La máquina llevaba todo el rato en pie, con todos sus servicios funcionando.

**La tentación.** Apagar el modo sigiloso. El argumento se presenta solo: antes funcionaba, he cambiado esto, ahora está roto. Y además el monitor está mandando alertas, mientras que la medida de seguridad no se nota.

**Qué cambió.** El monitor pasó a una comprobación TCP contra un puerto que de verdad sirve. Un ping demuestra que una pila de red responde a ICMP; una comprobación TCP demuestra que el sistema está vivo, la red funciona, y hay un proceso aceptando conexiones — que es mucho más parecido a lo que "arriba" quería decir.

**Lección:** cuando un control de seguridad rompe uno operativo, **el que suele estar midiendo mal es el operativo**. La medida de seguridad acaba de hacerte visible una debilidad de tu monitorización que llevaba ahí desde el principio.

Pero el operativo es el que tiene el panel en rojo y el que te manda notificaciones al móvil, así que tiende a ganar la discusión. Y la forma en que gana es que debilites la medida que acabas de poner.

---

### Introduje un bug y publiqué su arreglo el mismo día

**Qué escribí.** Instrumentando la salud de los contenedores, registré `healthy` para todo lo que no fuera explícitamente `unhealthy`.

**Por qué está mal.** El campo de salud de Docker está *vacío* cuando el contenedor no define ningún health check, porque definirlo es opcional. Vacío no es sano: es **sin comprobar**. Confundirlos convierte una métrica de salud en una métrica de "no ha explotado todavía".

**Qué provocó.** Un escalón brusco en la serie temporal el día que lo añadí, causado enteramente por mi propio código. Cualquiera mirando esa gráfica dentro de seis meses concluiría que algo cambió en el sistema ese día. No cambió nada en el sistema.

**Cómo lo vi.** Por casualidad, mirando por encima una muestra del agregado horario y encontrando una columna llena de ceros donde esperaba variedad.

**Qué cambió.** El valor se deriva del texto de estado, que sí se guardaba íntegro, y una migración corrigió las filas ya escritas. **491 de 779 estaban mal: el 63%.**

**Lo que hace fiable el arreglo** no es que se ejecutara sin errores, sino que las proporciones coinciden a ambos lados de la frontera: 37,4% en el histórico frente a 37,0% tras la corrección. Dos conjuntos de datos generados con meses de diferencia por escritores distintos, coincidiendo en medio punto porcentual.

**Lección:** una migración que corre limpia y produce basura plausible es el resultado por defecto. Valida contra algo que no acabes de calcular tú.

---

### Valores por defecto que nunca llegaron a una configuración existente

**Qué monté.** Un sistema de latidos que lee un manifiesto declarando qué tareas se esperan y con qué antigüedad máxima. El manifiesto se crea con unos valores por defecto la primera vez que se ejecuta.

**Qué añadí después.** Una opción para que el latido, además de escribir el fichero local, notificara a un monitor externo.

**Qué falló.** El cargador devolvía el fichero existente tal cual. Un manifiesto **ya creado** no tenía la clave nueva, así que la opción no existía para él y el push no se enviaba nunca.

**Por qué no lo detecté.** Porque las dos comprobaciones que hice daban verde, cada una por un motivo distinto. El comando de prueba de push funcionaba, porque va directo al monitor externo sin consultar el manifiesto. Y el comando de escritura del latido decía "registrado", porque el fichero local sí se escribía correctamente.

Dos verificaciones, dos éxitos, y nada llegando a su destino.

**Cómo lo pillé.** A una línea de salida le faltaba un sufijo de tres palabras que debería haber aparecido si el push se hubiera intentado.

**Qué cambió.** El cargador fusiona los valores por defecto en las claves ausentes y reescribe el fichero, respetando lo que se haya modificado a mano. Y los mensajes de estado ahora dicen explícitamente lo que **no** hicieron, en lugar de omitirlo en silencio.

**Lección:** una salida que solo informa de lo que ocurrió no puede contarte lo que no ocurrió. Si el mensaje hubiera dicho `push omitido (no configurado)` en vez de callarse, se habría visto al instante.

Y la de fondo: **los valores por defecto que solo se aplican en la creación no llegan a las instalaciones existentes**, que son precisamente todas las que ya están funcionando.

---

### El backup que llevaba meses sin borrar nada

**Qué creía.** Que el respaldo era un espejo del disco de datos: lo que se borra en el origen desaparece del destino. Es lo que hace `rsync --delete`, y ahí estaba, en la línea de comandos.

**Qué pasaba.** Dos directorios de sistema de macOS —los que guardan el historial de versiones y los temporales— son ilegibles incluso para el propietario de la máquina. rsync no podía abrirlos y terminaba con código 23: transferencia parcial.

Eso ya era invisible, porque el script nunca miraba el código de salida y su resumen decía `✅` pase lo que pase. Pero el daño real estaba una línea más arriba en el log:

```
IO error encountered -- skipping file deletion
```

**rsync desactiva la fase de `--delete` cuando encuentra un error de E/S.** Y su razonamiento es correcto: si no ha podido leer el origen entero, borrar en el destino podría cargarse ficheros que sí existen y que no ha llegado a ver. Es una salvaguarda bien pensada.

La consecuencia es que el respaldo dejó de ser un espejo y pasó a ser un acumulado: todo lo que había existido alguna vez, incluido lo borrado a propósito. Y creciendo.

**Cómo salió a la luz.** Por accidente, arreglando otra cosa. Estaba quitando una credencial escrita a fuego en ese mismo script, ejecuté el backup para comprobar que el cambio no lo rompía, y me fijé en un código de salida que llevaba meses ahí.

**El arreglo, en tres capas.** Excluir los dos directorios de sistema, para que el error desaparezca. Capturar el código de salida e interpretarlo — 0 y 24 son correctos, cualquier otro es un fallo con aviso. Y que el latido que lee el verificador registre el resultado real, en vez de escribir siempre lo mismo.

**Y entonces apareció el segundo problema, que el primero estaba tapando.** Con el borrado funcionando otra vez, rsync intentó eliminar un fichero huérfano y no pudo: pertenecía a `root`. Había 114 así, restos de alguna ejecución antigua del backup con privilegios elevados. Un solo `sudo` de hace meses dejando ficheros que el proceso normal no puede gestionar, con el síntoma apareciendo mucho después y en un sitio que no lo sugiere.

**Lección:** un salvaguarda silencioso es un fallo silencioso. rsync hizo lo correcto —dejar de borrar ante la duda— y lo comunicó por el único canal que nadie estaba leyendo: su código de salida. **Un componente que se degrada para protegerte necesita que alguien compruebe si se ha degradado**, exactamente igual que el espejo del disco de respaldo.

---

### Comprimí la evidencia antes de analizarla

**Qué hice.** Por la mañana implementé y ejecuté la política de retención, que agregó el detalle a cinco minutos de abril en medias horarias.

**Qué necesité por la tarde.** Investigando el contenedor que se había reiniciado 49 veces, la secuencia minuto a minuto: cuánto aguantaba entre caída y caída, si el patrón dentro de cada ráfaga era regular, si había algo periódico.

**Qué tenía.** Medias horarias. Suficientes para llegar a la conclusión —de hecho la evidencia clave, las dos horas enteras a cero, salió de ahí— pero la secuencia fina se había perdido de forma permanente.

**Lección:** investiga antes de comprimir. Las políticas de retención son **irreversibles** de una forma en que casi ninguna otra operación lo es. Puedes revertir un despliegue, restaurar una base de datos, deshacer un commit. No puedes recuperar la resolución de una serie temporal que ya has agregado.

---

## El patrón

Releyendo todo esto, casi todos son el mismo error con distinta ropa.

| | Reportaba | Medía |
| --- | --- | --- |
| Éxito de la recuperación | que el problema se arreglaba | un código de salida |
| Salud de contenedores | sano | no explícitamente roto |
| Cobertura de relays | nueve relays en pie | cinco comprobaciones sin relación pasando |
| Configuración de SSH | solo clave | un fichero, no un intento de login |
| Push del latido | enviado | que se había escrito un fichero local |
| Tareas programadas | todas presentes | todas las tareas *cargadas* presentes |
| Backup diario | el respaldo es un espejo del origen | que el script llegó al final |

Siete indicadores. Ninguno roto. Cada uno medía algo real y lo medía correctamente. **Ninguno medía lo que su etiqueta prometía**, y todos estaban en verde.

### Por qué pasa siempre lo mismo

La forma común es que cada control mide **lo que es fácil de medir** y la etiqueta describe **lo que a mí me importa**.

Leer un código de salida es una línea de código; comprobar que un servicio se recuperó requiere esperar, consultar el estado real y decidir qué significa "recuperado". Leer un fichero de configuración es trivial; intentar un login y verificar el rechazo exige diseñar la prueba. Listar las tareas cargadas es una llamada al sistema; saber si falta alguna requiere mantener una lista de lo esperado.

En cada caso hay una versión barata y una cara, la barata se implementa primero, y la etiqueta se escribe describiendo la intención en lugar de la implementación. Después nadie vuelve.

### Por qué no se detecta

Porque **la distancia entre las dos cosas es invisible precisamente cuando el indicador está en verde**. Un control que falla se investiga: miras, y en cinco minutos descubres que estaba midiendo lo que no era. Un control que pasa no se mira nunca. El verde no es un estado que nadie investigue.

Todos estos llevaban meses a la vista, en un informe que leo todas las mañanas.

### El corolario incómodo

Cada punto de esta página lo encontró **una persona notando algo raro**. El escalón en la salud lo vi de refilón mirando una muestra. El push que no se enviaba lo pillé por tres palabras que faltaban en una línea. Los 49 reinicios aparecieron porque me puse a mirar una tabla por un motivo distinto.

Ni uno solo lo cazó un sistema construido para cazarlo. Y construir ese sistema —comprobaciones de coherencia sobre las propias métricas, proporciones estables entre periodos, detección de series que se paran— es el siguiente trabajo obvio, y no está hecho.
