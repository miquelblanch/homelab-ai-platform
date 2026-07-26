# Auditoría de seguridad

> Qué pasa cuando auditas un sistema que has construido tú, y resulta que varios controles informaban sobre la configuración en lugar de sobre el comportamiento.

!!! info "Qué contiene y qué no contiene esta página"
    Describe el **método y los hallazgos por categoría**, más dos incidencias ya cerradas que merecen leerse por *cómo* se encontraron. No contiene detalle reproducible, ni configuración actual, ni nada sobre qué está expuesto a internet. Todo lo concreto que se nombra aquí estaba arreglado antes de escribirlo.

---

## Por qué la hice

La plataforma había crecido por acumulación. Es el patrón habitual en cualquier sistema que uno monta para sí mismo: un contenedor nuevo cuando hace falta un servicio, un puerto abierto cuando algo no funciona a la primera, una regla añadida al proxy inverso que resuelve el problema de esa tarde. Cada decisión aislada es razonable. La suma de cincuenta decisiones aisladas, tomadas a lo largo de un año, no la ha diseñado nadie.

**No había fallado nada.** Ese es justamente el momento de mirar. Después de un incidente miras a la fuerza, y miras con el sesgo de buscar lo que acaba de romperse.

El alcance fue todo: el host, los cuarenta y tantos contenedores, los servicios que corren dentro y el router. **Auditoría activa**, no una checklist leída de un documento: comandos ejecutados contra el sistema vivo, salida recogida en un fichero, hallazgos triados después.

---

## Método

### La decisión que más rentabilidad dio

La auditoría se escribió como **un único script de bash de unas 500 líneas**, ejecutado como root contra el sistema en vivo, produciendo un solo fichero de salida.

La alternativa habría sido una sesión de revisión: ir mirando cosas, apuntando hallazgos, y redactando un informe al final. Es más rápido de empezar y produce el mismo documento.

Y es la decisión equivocada, porque **una revisión manual produce un documento y un script produce un número que puedes regenerar**. Todo lo que vino después depende de esa diferencia: los sprints de corrección, el marcador de progreso, y sobre todo la capacidad de comprobar que un arreglo aterrizó de verdad en lugar de que lo pareciera.

Un informe describe el día que lo escribiste. Un script describe hoy.

### Las doce áreas

| Área | Ejemplos de qué se comprobó |
| --- | --- |
| Base del host | Cifrado de disco, estado y modo del firewall, protección de integridad del sistema, verificación de firma de código |
| Cuentas | Usuarios locales, quién tiene privilegios administrativos, configuración de acceso remoto |
| SSH | Métodos de autenticación permitidos, inventario de claves autorizadas, protección contra fuerza bruta |
| Red | Sockets a la escucha, en qué interfaces publican, qué servicios exigen autenticación |
| Contenedores | Modo privilegiado, acceso al socket de Docker, bind mounts sensibles, ámbito de publicación de puertos |
| Servicios | API de inferencia, plataforma de automatización, bases de datos |
| TLS | Validez, emisión y caducidad de certificados |
| Logs | Qué eventos de seguridad se registran y durante cuánto |
| Tareas programadas | Privilegios y propiedad de los jobs a nivel de sistema |
| Herramientas | Qué cobertura de escaneo de vulnerabilidades existe, si existe |

Cada bloque volcaba su salida en bruto al fichero. El análisis y la clasificación por severidad se hicieron después, sobre ese volcado, y no durante la ejecución — separar la recogida del juicio evita ir corrigiendo sobre la marcha y perder la foto completa.

---

## Qué salió

### El recuento

La primera pasada produjo **37 hallazgos**:

| Severidad | Cantidad |
| --- | --- |
| Críticos | 11 |
| Altos | 10 |
| Medios | 8 |
| Ya correctos | 8 |

Once hallazgos críticos en un sistema que había construido yo, que usaba a diario y que consideraba razonablemente montado. Merece la pena detenerse ahí un momento: no es que hubiera hecho las cosas sin cuidado. Es que **el cuidado puntual no acumula**, y nada me había obligado nunca a mirar el conjunto.

### Las tres clases

La mayoría de los críticos caía en uno de tres patrones.

**Servicios escuchando en más interfaces de las necesarias.** Cosas publicadas en todas las interfaces cuando solo necesitaban el loopback o una única red.

El origen de esto casi siempre es el mismo: montas un servicio, no funciona a la primera, abres el binding para descartar que sea un problema de red, funciona, y ahí se queda. La configuración provisional que resuelve el problema de esa tarde se convierte en la definitiva porque nunca vuelve a molestar. Individualmente cada caso es defendible; en conjunto, la superficie de escucha era bastante mayor de la que yo creía tener.

**Privilegios de contenedor por encima de lo necesario.** Contenedores corriendo con más capacidades de las que su función requiere.

El origen aquí también es identificable: los ficheros `docker-compose` de ejemplo que publican los proyectos suelen ser permisivos a propósito, para que funcionen en el máximo número de entornos sin que nadie abra una incidencia. Los copias, funciona, y nunca vuelves a leer esas líneas. Cada `privileged: true` heredado de un ejemplo es un contenedor que, si lo comprometen, tiene mucha más superficie desde la que moverse.

**Huecos de autenticación en servicios internos.** Componentes desplegados sobre la premisa de que "esto solo está en la LAN".

Es un modelo de seguridad válido hasta el momento en que uno de los dispositivos de esa LAN queda comprometido — un televisor con firmware sin actualizar, un aparato de domótica barato, un portátil que se conecta también a otras redes. A partir de ahí, "solo en la LAN" significa "sin autenticación" para quien ya está dentro.

Nada de esto es exótico. Es la consecuencia ordinaria de un sistema que creció un `docker run` cada vez.

### El plan

Las correcciones se organizaron en **cuatro fases por criticidad, con 26 ítems numerados**. La primera fase se ejecutó de inmediato; el resto fue por sprints a lo largo de las semanas siguientes.

Numerarlos importó más de lo que parece: permite decir "R-19 hecho" en lugar de "creo que ya arreglé lo de las claves", y permite que el marcador del script se corresponda con ítems concretos del plan.

---

## El hallazgo que merece leerse: un control que mentía

### El punto de partida

SSH estaba configurado para autenticación **solo por clave**. El fichero de configuración lo decía. Lo decía en dos sitios distintos: en un fichero incluido y en el principal.

El script de auditoría lo comprobaba con `sshd -T`, que no lee el fichero sino que le pregunta al propio demonio cuál es su configuración efectiva. Es, en teoría, la fuente de verdad: resuelve includes, aplica precedencias y te dice qué está haciendo de verdad.

`sshd -T` reportaba `PasswordAuthentication yes`.

### La cadena de hipótesis

**Primera: un bloque `Match` al final del fichero** sobreescribiendo la directiva. Es la causa más común de este síntoma — los bloques `Match` se aplican condicionalmente y anulan lo anterior. Miré el final del fichero. No había ninguno.

**Segunda: un problema de orden en los includes.** El fichero incluido podía estar cargándose antes de la línea del principal, o viceversa, con una precedencia distinta a la esperada. Comprobé el orden. Ambos decían lo mismo, así que la precedencia era irrelevante.

En este punto llevaba un rato haciendo arqueología de ficheros de configuración cada vez más ingeniosa, sin resultado.

### El cambio de enfoque

Lo correcto llegado ese punto es dejar de leer configuración y **probar el comportamiento**. Intentar exactamente aquello que no debería funcionar:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no user@host "echo test"
```

Las dos opciones fuerzan al cliente a intentar solo contraseña, desactivando explícitamente la autenticación por clave. Si la configuración es correcta, esto tiene que ser rechazado.

```
user@host's password:
test
```

Pidió la contraseña, la aceptó, y ejecutó el comando.

### La causa

**Con PAM habilitado, PAM puede autenticar por contraseña al margen de la directiva `PasswordAuthentication`.**

El ajuste no miente exactamente: controla el método de autenticación por contraseña *propio de SSH*. Pero cuando `UsePAM` está activo —lo está por defecto en prácticamente todas las distribuciones— existe un segundo camino de autenticación que pasa por los módulos de PAM, y ese camino no lo cierra esa directiva.

Es decir: había tres respuestas distintas a la misma pregunta. El fichero decía que no. El volcado de configuración efectiva decía que sí. Y el comportamiento real —el único que importa— también decía que sí, pero por un motivo que ninguna de las dos fuentes explicaba.

### El arreglo

La solución no es deshabilitar una opción, es **declarar el requisito**:

```
AuthenticationMethods publickey
```

Esto no dice "no permitas contraseñas". Dice "el único método aceptable es clave pública", lo que cierra todos los caminos alternativos incluido el de PAM.

Y se verifica exactamente igual que se encontró:

```
user@host: Permission denied (publickey).
```

### Dos lecciones, y la segunda es la incómoda

**Una comprobación de configuración no es una comprobación de seguridad.** La auditoría estaba preguntando *"¿qué dice el demonio que hace?"* cuando la pregunta que importa es *"¿qué pasa cuando alguien lo intenta?"*. Esas dos preguntas tuvieron respuestas distintas durante meses, y la primera daba la respuesta tranquilizadora.

**Mi script de auditoría tenía el mismo bug que mi configuración.** Leía `sshd -T`. Así que después de aplicar el arreglo, el script **seguía reportando un fallo**, porque `sshd -T` sigue diciendo `yes` — PAM sigue ahí, simplemente ya no puede autenticar porque `AuthenticationMethods` lo impide.

El control estaba equivocado antes y siguió equivocado después, ahora en la dirección contraria. Hubo que corregir el comprobador junto con el sistema, y sustituir la lectura de configuración por una prueba de comportamiento real.

Es el mismo modo de fallo que todo lo que hay en la página de [Fiabilidad](reliability.md), llegando desde un dominio completamente distinto: **un indicador que mide lo que es fácil de medir en lugar de lo que te importa.** Leer un fichero de configuración es trivial; intentar un login y comprobar que te rechazan requiere pensar en el diseño de la comprobación. No vi que era el mismo patrón hasta que me lo encontré en los dos sitios el mismo mes.

---

## Cuando el hardening rompió la monitorización

### Lo que pasó

Entre las medidas de endurecimiento del host se activó el **modo sigiloso** del firewall, que hace que la máquina no responda a peticiones no solicitadas — incluidos los `ping`. El objetivo es no confirmar la propia existencia ante un escaneo de red.

El monitor de uptime externo comprobaba el host **por ping**.

El resultado fue inmediato: el monitor pasó a rojo y reportó el servidor principal como caído. Llevaba todo el rato en pie, con todos sus servicios funcionando, respondiendo perfectamente por SSH y por HTTP.

### La discusión que uno tiene consigo mismo

El reflejo es desactivar el modo sigiloso. El argumento se presenta solo: *"antes funcionaba, he cambiado esto, y ahora está roto; luego el cambio está mal"*. Además el monitor está mandando alertas, y las alertas molestan, mientras que el modo sigiloso no se nota.

Y es la conclusión equivocada. **El control operativo era el que estaba mal.**

Un `ping` demuestra que la pila de red de una máquina responde a ICMP. Eso no es lo que queríamos saber. Lo que queríamos saber es si la máquina está prestando servicio, y para eso la comprobación correcta es una **conexión TCP a un puerto que de verdad esté sirviendo**: demuestra que el sistema operativo está vivo, que la red funciona, y que hay un proceso escuchando y aceptando conexiones.

El monitor se cambió a comprobación TCP. Volvió a verde, y ahora mide algo más parecido a lo que su etiqueta prometía.

### Por qué merece escribirse

Porque el reflejo va en la dirección equivocada de forma sistemática. **Cuando un control de seguridad rompe un control operativo, el que suele estar midiendo mal es el operativo** — el de seguridad acaba de hacerte visible una debilidad de tu monitorización que llevaba ahí desde el principio.

Pero el operativo es el que hace ruido, y el que tiene un panel en rojo, y el que te está mandando notificaciones al móvil. Así que tiende a ganar la discusión, y la forma de que gane es debilitar la medida de seguridad recién puesta.

---

## El marcador

### Los números

Como la auditoría es un script, el progreso es un número y no una afirmación:

| Ejecución | Controles | Pasan | Fallan |
| --- | --- | --- | --- |
| Tras la fase uno | 67 | 15 | 2 |
| Tras los sprints de hardening | 81 | 27 | **0** |

Los "controles" son el total de comprobaciones que ejecuta el script; muchas son informativas y solo vuelcan datos. Las columnas de pasan y fallan son las que emiten un veredicto.

### El número más sano de los dos

El recuento de controles **creció de 67 a 81**, y eso es más significativo que llegar a cero fallos.

Creció porque arreglar cosas revela cosas que merece la pena comprobar. Al corregir la autenticación de SSH aparece la pregunta de qué claves están autorizadas y desde dónde. Al instalar protección contra fuerza bruta aparece la de si su lista blanca es correcta. Cada arreglo abre preguntas nuevas.

**Un conjunto de controles estático deja de encontrar nada en el momento en que lo has satisfecho.** Si tu auditoría lleva un año con las mismas 67 comprobaciones y todas en verde, lo que tienes no es un sistema seguro: tienes una auditoría que ya no está aprendiendo.

### Capacidades nuevas

Junto a las correcciones se añadieron tres cosas que sencillamente no existían:

**Protección contra fuerza bruta** con lista blanca de la red local, para que un error de contraseña desde casa no acabe bloqueándome a mí mismo.

**Escaneo de vulnerabilidades sobre las imágenes de contenedor**, que antes no se hacía en absoluto. Las imágenes se actualizaban por costumbre, no porque nada avisara de que una tenía una vulnerabilidad conocida.

**Un observador externo en hardware separado**, de modo que el fallo total del host principal sea siquiera detectable. Antes, si la máquina se apagaba, no había nada que pudiera decirlo.

---

## Qué se aplazó, y por qué

Las fases posteriores del plan siguen en marcha. Son los **ítems estructurales**: los que exigen cambiar cómo está dispuesta la red y no cambiar un ajuste en un fichero.

Se secuenciaron los últimos a propósito, por dos motivos. Cada uno **arriesga romper servicios que funcionan**, con un radio de impacto que va bastante más allá de un contenedor. Y ninguno resuelve un hallazgo crítico: son mejoras de defensa en profundidad, valiosas, pero que reducen el impacto de un compromiso en lugar de impedirlo.

Esa secuenciación es la parte que defendería en una revisión: **arregla primero lo crítico y barato, y acepta los ítems estructurales como conocidos y planificados en vez de fingir que el plan está completo.**

Un plan de remediación con todos los ítems marcados como hechos suele ser un plan que se acotó a lo que era fácil. Un riesgo aceptado de forma explícita, con su motivo escrito y su lugar en la cola, es una decisión de ingeniería. Un riesgo que nadie ha enunciado es un descuido.

---

## El punto ciego que tuvo la auditoría

### Lo que no cubrió

La auditoría cubrió configuración del sistema, postura de los contenedores, exposición de red y autenticación de servicios.

No cubrió **secretos a nivel de aplicación dentro de los scripts operativos**.

### Cómo apareció

Meses después, instrumentando la monitorización por motivos que no tenían nada que ver con seguridad, encontré credenciales de API escritas a fuego en **seis scripts de Python**. El mismo token repetido en todos, en texto plano, en la cabecera de cada fichero.

Nunca se publicaron en ningún sitio y se movieron a un fichero cargado en tiempo de ejecución fuera del control de versiones, con permisos restrictivos. Pero la auditoría había pasado limpia sobre un sistema que las contenía.

Y lo hizo correctamente: esa categoría no estaba en su alcance. El script comprobaba permisos de ficheros y privilegios de procesos, no el contenido del código fuente.

### Las dos conclusiones

No es un fallo de ejecución. Es **lo que una auditoría es**: una foto acotada por un alcance, tomada en un momento concreto. De ahí se siguen dos cosas que conviene tener presentes cada vez que alguien enseña un informe en verde.

**El alcance lo es todo.** Todo lo que queda fuera es invisible, y el informe no te va a advertir de lo que no miró. Un resultado limpio significa "no encontramos problemas en lo que buscamos", que se parece mucho menos a "no hay problemas" de lo que suena.

**Los sistemas derivan después de auditarlos.** Ese es el argumento definitivo a favor del script frente al informe: un script se puede volver a ejecutar. El informe describía abril. El script puede describir hoy.

La ejecución semanal automatizada del script es el ítem que más lamento no haber terminado, precisamente por esto.

---

## Qué haría distinto

**Probar el comportamiento antes de leer la configuración.** Lo de SSH habría salido en los primeros cinco minutos con un solo intento de login, en lugar de tras una cadena de hipótesis cada vez más rebuscadas sobre includes y precedencias. La configuración es una declaración de intenciones; el comportamiento es el hecho.

**Escribir el comprobador antes que la checklist.** El documento era el entregable que yo mismo había pedido. El script es el artefacto que sigue teniendo valor un año después. Si tuviera que elegir uno de los dos, no dudaría.

**Contar con que la auditoría rompa algo.** Lo rompió, de inmediato, y la recuperación enseñó más que el propio arreglo. Endurecer un sistema es cambiar su comportamiento, y hay componentes que dependían del comportamiento anterior sin que nadie lo hubiera escrito en ningún sitio. Presupuéstalo en lugar de sorprenderte, y sobre todo no uses el susto como argumento para revertir la medida.
