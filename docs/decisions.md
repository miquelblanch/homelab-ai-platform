# Decisiones y errores

Las demás páginas describen un sistema que funciona. Esta va de cómo llegó hasta ahí, que fue bastante menos limpio.

---

## Decisiones que aguantaron

Cada una va con la condición bajo la cual deja de ser correcta, porque una decisión sin condición de reversión es una preferencia.

**El agente corre nativo, no en un contenedor.**
Gestiona el runtime de contenedores. Meterlo dentro de aquello que gestiona crea un bucle en el que no puede reiniciarse a sí mismo, y el modo de fallo es el peor posible: solo aparece cuando algo ya ha ido mal.
*Revertir cuando* el agente deje de tocar el runtime.

**SQLite en vez de Prometheus.**
Un nodo, intervalo de cinco minutos, unas pocas decenas de series. SQLite significa ningún contenedor extra, ninguna configuración de scraping, ningún subsistema de retención, y un lenguaje de consulta que ya conozco. Prometheus y Grafana necesitarían a su vez su propia monitorización.
*Revertir en torno a* diez nodos, o la primera vez que quiera reglas de alerta expresadas sobre las propias métricas en lugar de sobre las comprobaciones en crudo.

**Modelo local para las tareas programadas, cloud para lo interactivo.**
El trabajo programado corre sin supervisión y a menudo; su coste tiene que ser cero. El interactivo tiene a un humano esperando, así que gana la calidad.
*Revertir por tarea* — que es exactamente lo que pasó con las multi-tool, más abajo.

**Los monitores nunca lanzan excepciones.**
Todas las funciones de las capas de persistencia y de latidos capturan sus propias excepciones y devuelven un valor neutro. En código de aplicación sería mala práctica. En un monitor, una excepción no capturada significa dejar de enterarte de *todo lo demás*, que es estrictamente peor que perder una muestra.
*Revertir* en ningún caso. La alternativa es un monitor que se muere en silencio.

**Agregar antes de borrar.**
La retención comprime a medias horarias antes de purgar el detalle, y la purga solo toca horas que ya existen en el agregado. La red de seguridad vive en el SQL, no en el llamante.
*Revertir* nunca. Es un seguro barato contra la clase de error que solo se comete una vez.

**La auditoría es un script, no un documento.**
El documento era el entregable que pedí. El script es el artefacto que sigue teniendo valor un año después, porque se puede volver a ejecutar y produce un número.

---

## Errores

### El relay que se robaba el puerto a sí mismo

Se montó un relay `socat` para reenviar `0.0.0.0:PUERTO` a `127.0.0.1:PUERTO` y que un panel fuera accesible desde la LAN. El mismo puerto a los dos lados.

El panel ya estaba escuchando en `127.0.0.1:PUERTO`. Y el relay arrancaba antes en el boot.

Así que tras cada reinicio el relay ocupaba el puerto, el panel fallaba al arrancar con "address already in use", y el relay reenviaba diligentemente el tráfico hacia sí mismo. La línea de log se repetía indefinidamente. El panel quedaba caído el tiempo que tardase en darme cuenta, una vez por reinicio, durante semanas.

**Qué cambió:** el relay se eliminó y el panel bindea la interfaz de LAN por su cuenta. El plist está archivado con una nota que dice *no recrear*, porque el fallo se parece exactamente a un conflicto de puertos causado por otra cosa.

**La lección no es "revisa tus puertos".** Es que un componente cuyo trabajo entero es la indirección apuntará encantado hacia sí mismo, y nada en las herramientas va a objetar.

---

### Un escritor de métricas que nadie echó de menos

Se desmanteló un componente. Escribía métricas de contenedores en una tabla. Nada más leía esa tabla.

Setenta días después fui a buscar los datos y encontré 265.365 filas, la más reciente de diez semanas antes, y ningún código en todo el repositorio que escribiera ahí. No se había roto. Lo habían borrado junto con su dueño, y la ausencia no produjo ninguna señal porque nada dependía de ella.

**Qué cambió:** el monitor que ya estaba corriendo ganó persistencia, y la tarea programada que la mantiene ganó un latido, de modo que una segunda desaparición sí se notaría.

**Lección:** los datos que nadie lee dejan de producirse, y te enteras cuando los necesitas.

---

### Un 100% de éxito que medía códigos de salida

El sistema de auto-remediación reportaba que sus 83 intervenciones habían tenido éxito, todas.

Registraba éxito cuando `docker restart` devolvía 0, cosa que ocurre siempre que el daemon acepta el comando — no cuando el contenedor se mantiene en pie. Durante dos horas de abril, un contenedor estuvo a cero de memoria y cero de CPU en doce muestras consecutivas mientras el healer anotaba cuatro reinicios exitosos.

**Qué cambió:** los reinicios se verifican contra `docker inspect` diez segundos después, y un cortacircuitos detiene el bucle tras tres intentos en seis horas.

**Coste:** un contenedor consumió el 59% de todas las intervenciones automáticas del sistema durante siete semanas sin levantar una sola alerta, porque el healer funcionaba.

Desarrollo completo en [Fiabilidad](case-studies/reliability.md).

---

### Un control que reportaba lo contrario de la realidad

SSH estaba configurado para autenticación solo por clave. `sshd -T` reportaba que la autenticación por contraseña estaba activa. La autenticación por contraseña funcionaba de verdad, porque con PAM en el camino puede autenticar al margen de esa directiva.

Tres respuestas distintas, y la que importaba era la que nadie había preguntado: qué pasa cuando alguien lo intenta.

**Qué cambió:** `AuthenticationMethods publickey`, verificado intentando un login con contraseña y siendo rechazado.

**Y la parte que escuece:** mi script de auditoría tenía el mismo bug que mi configuración. Comprobaba `sshd -T`, así que tras el arreglo seguía reportando un fallo. Hubo que corregir el control junto con el sistema.

Desarrollo completo en [Auditoría de seguridad](case-studies/security.md).

---

### "Nunca reiniciar" se convirtió calladamente en "nunca mirar"

Hay un contenedor deliberadamente apagado. Se añadió a una lista `NEVER_RESTART` para que el monitor lo dejara en paz.

El monitor implementa eso saltándose el contenedor por completo, antes de ejecutar comprobación alguna. Lo que significa que un contenedor que debería estar apagado puede llevar días corriendo sin que nada lo reporte. Que es lo que pasó: cuatro días, sano, inadvertido, hasta que leí un `docker ps` en crudo por un motivo que no tenía nada que ver.

**Qué cambió:** de momento solo la documentación, que ahora dice explícitamente que ese contenedor no tiene monitorización y que conviene verificar su estado de vez en cuando. El arreglo bueno —una categoría que signifique *observar pero nunca actuar*— no está construido.

**Lección:** las listas de exclusión confunden dos intenciones distintas. "No actúes sobre esto" y "no mires esto" no son la misma instrucción, y colapsarlas crea un punto ciego que crece en silencio.

---

### El hardening rompió la monitorización, y la monitorización estaba mal

Activar el modo sigiloso del firewall dejó al host sin responder a ICMP. El monitor de uptime externo lo comprobaba por ping, y reportó el servidor principal como caído de inmediato.

El reflejo es apagar el modo sigiloso, porque ahora el monitor miente y el monitor es lo que hace ruido.

**Qué cambió:** el monitor pasó a una comprobación TCP contra un puerto que de verdad sirve. Un ping demuestra que una pila de red responde a ICMP; una comprobación TCP demuestra que hay algo escuchando, que es más parecido a lo que "arriba" quería decir.

**Lección:** cuando un control de seguridad rompe uno operativo, el que suele estar midiendo mal es el operativo. Pero es el que se queja, así que tiende a ganar la discusión.

---

### Introduje un bug y publiqué su arreglo el mismo día

Instrumentando la salud de los contenedores, registré `healthy` para todo lo que no fuera explícitamente `unhealthy`. El campo real de Docker está *vacío* cuando el contenedor no define healthcheck — y vacío no es sano, es sin comprobar.

El resultado fue un escalón en la serie temporal el día que lo añadí, causado enteramente por mi propio código. 491 de 779 filas estaban mal: el 63%.

**Qué cambió:** el valor se deriva del `status` almacenado, y una migración corrigió las filas existentes. Lo que hizo fiable el arreglo no fue que se ejecutara, sino que las proporciones coincidían a ambos lados de la frontera: 37,4% histórico frente a 37,0% tras la corrección.

**Lección:** una migración que corre limpia y produce basura plausible es el resultado por defecto. Valida contra algo que no acabes de calcular tú.

---

### Valores por defecto que nunca llegaron a una configuración existente

El sistema de latidos lee un manifiesto que declara qué tareas se esperan. Cuando después añadí una opción para hacer push también al monitor externo, el cargador devolvía el fichero existente sin tocar — así que la clave nueva simplemente no existía en un manifiesto ya creado.

El push no se enviaba nunca. Los dos comandos de verificación reportaban éxito: uno porque se saltaba el manifiesto, el otro porque la escritura local sí funcionaba. Lo pillé porque faltaba un sufijo de tres palabras en una línea de salida.

**Qué cambió:** el cargador fusiona los valores por defecto en las claves ausentes y reescribe el fichero. Y los mensajes de estado ahora dicen lo que **no** hicieron, en vez de omitirlo en silencio.

**Lección:** una salida que solo informa de lo que ocurrió no puede contarte lo que no ocurrió.

---

### Comprimí la evidencia antes de analizarla

La retención agregó el detalle a cinco minutos de abril en medias horarias por la mañana. Esa tarde, investigando un contenedor que se había reiniciado 49 veces, necesitaba la secuencia minuto a minuto.

Las medias horarias bastaron para llegar a una conclusión. La secuencia completa se perdió de forma permanente.

**Lección:** investiga antes de comprimir. Las políticas de retención son irreversibles de una forma en que la mayoría de operaciones no lo son.

---

## El patrón

Releyendo todo esto, casi todos son el mismo error con distinta ropa.

| | Reportaba | Medía |
| --- | --- | --- |
| Éxito de la remediación | que el problema se arreglaba | un código de salida |
| Salud de contenedores | sano | no explícitamente roto |
| Cobertura de relays | nueve relays en pie | cinco comprobaciones sin relación pasando |
| Configuración de SSH | solo clave | un fichero, no un intento de login |
| Push del latido | enviado | que se había escrito un fichero local |
| Tareas programadas | todas presentes | todas las tareas *cargadas* presentes |

Seis indicadores. Ninguno roto. Cada uno medía algo real. **Ninguno medía lo que su etiqueta prometía**, y todos estaban en verde.

La forma común es que cada control medía lo que era fácil de medir —un código de salida, un fichero de configuración, una lista de unidades cargadas— y la etiqueta describía lo que a mí me importaba. La distancia entre esas dos cosas es invisible precisamente porque el indicador está en verde, y el verde no es un estado que nadie investigue.

El corolario incómodo: cada punto de esta página lo encontró una persona notando algo raro. Ni uno solo lo cazó un sistema construido para cazarlo. Construir ese sistema es el siguiente trabajo obvio, y no está hecho.
