# Auditoría de seguridad

> Qué pasa cuando auditas un sistema que has construido tú, y resulta que varios controles informaban sobre la configuración en lugar de sobre el comportamiento.

!!! info "Qué contiene y qué no contiene esta página"
    Describe el **método y los hallazgos por categoría**, más dos incidencias ya cerradas que merecen leerse por *cómo* se encontraron. No contiene detalle reproducible, ni configuración actual, ni nada sobre qué está expuesto. Todo lo concreto que se nombra aquí estaba arreglado antes de escribirlo.

---

## Por qué la hice

La plataforma había crecido por acumulación. Los contenedores se añadían cuando hacían falta, los puertos se abrían cuando algo no funcionaba, y la configuración del proxy inverso tenía capas que nadie había vuelto a mirar. No había fallado nada, que es exactamente la condición bajo la cual hay que mirar.

El alcance fue todo: el host, todos los contenedores, los servicios que corren dentro y el router. Auditoría activa, no una checklist leída de un documento — comandos ejecutados contra el sistema vivo, salida recogida, hallazgos triados.

---

## Método

La auditoría fue un único script de bash cubriendo doce áreas, ejecutado como root contra el sistema en vivo, con un solo fichero de salida.

| Área | Ejemplos de qué se comprobó |
| --- | --- |
| Base del host | Cifrado de disco, estado del firewall, protección de integridad del sistema, verificación de firma de código |
| Cuentas | Usuarios locales, privilegios, configuración de acceso remoto |
| SSH | Métodos de autenticación, inventario de claves, protección contra fuerza bruta |
| Red | Sockets a la escucha, ámbito de binding, autenticación de servicios |
| Contenedores | Modo privilegiado, exposición del socket, bind mounts, ámbito de publicación de puertos |
| Servicios | API de inferencia, plataforma de automatización, bases de datos |
| TLS | Validez y emisión de certificados |
| Logs | Registro de eventos de seguridad y retención |
| Tareas programadas | Privilegios y propiedad de los jobs a nivel de sistema |
| Herramientas | Cobertura de escaneo de vulnerabilidades |

Escribir la auditoría como **script y no como sesión** fue la decisión más útil de todas. Una revisión manual produce un documento; un script produce un número que puedes regenerar. Todo lo que vino después —los sprints, el marcador, la capacidad de confirmar que un arreglo aterrizó de verdad— depende de que la auditoría sea repetible.

---

## Qué salió

La primera pasada produjo **37 hallazgos**:

| Severidad | Cantidad |
| --- | --- |
| Críticos | 11 |
| Altos | 10 |
| Medios | 8 |
| Ya correctos | 8 |

Once hallazgos críticos en un sistema que había construido yo y consideraba razonable. La mayoría se agrupaban en tres clases:

**Servicios escuchando en más interfaces de las necesarias.** Cosas publicadas en todas las interfaces cuando solo necesitaban loopback o una única red. Individualmente defendible, en conjunto una superficie bastante mayor de la pretendida.

**Privilegios de contenedor.** Contenedores corriendo con más capacidades de las que su función requería, heredadas en su mayoría de ficheros `docker-compose` de ejemplo copiados al montarlos y nunca revisados.

**Huecos de autenticación en servicios internos.** Componentes desplegados asumiendo que "esto solo está en la LAN", que es un modelo de seguridad válido hasta que uno de los dispositivos de esa LAN queda comprometido.

Nada exótico. Eran la consecuencia ordinaria de un sistema que creció un `docker run` cada vez.

El plan de remediación se organizó en cuatro fases por criticidad, con 26 ítems numerados. La primera fase se ejecutó de inmediato; el resto fue por sprints.

---

## El hallazgo que merece leerse: un control que mentía

SSH estaba configurado para autenticación solo por clave. El fichero de configuración lo decía, en dos sitios. El script de auditoría lo comprobaba con `sshd -T`, el volcado de configuración efectiva del propio demonio.

`sshd -T` reportaba `PasswordAuthentication yes`.

La primera hipótesis obvia era un bloque `Match` sobreescribiéndolo al final del fichero. No había ninguno. Segunda hipótesis: un problema de orden en los includes. Tampoco.

Y llegado ese punto, lo correcto es dejar de leer configuración y **probar el comportamiento**:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no user@host "echo test"
```

```
user@host's password:
test
```

La autenticación por contraseña funcionaba. La configuración decía que no, el volcado de configuración efectiva decía que sí, y la realidad no coincidía con ninguno de los dos de una forma que importaba: **con PAM en el camino, PAM puede autenticar por contraseña independientemente de la directiva `PasswordAuthentication`.** El ajuste no miente, simplemente no cubre todo el flujo de autenticación.

El arreglo consiste en declarar el requisito en lugar de deshabilitar una opción:

```
AuthenticationMethods publickey
```

Y verificar igual que se encontró:

```
user@host: Permission denied (publickey).
```

### Dos lecciones, y la segunda es la incómoda

**Una comprobación de configuración no es una comprobación de seguridad.** La auditoría preguntaba "qué dice el demonio que hace" en vez de "qué pasa cuando alguien lo intenta". Esas dos preguntas tuvieron respuestas distintas durante meses.

**Mi script de auditoría tenía el mismo bug que mi configuración.** Leía `sshd -T`, así que tras el arreglo seguía reportando un fallo — el control seguía equivocado, ahora en la otra dirección. Hubo que corregirlo junto con el sistema.

Es el mismo modo de fallo que todo lo de la página de [Fiabilidad](reliability.md), llegando desde un dominio completamente distinto: **un indicador que mide lo que es fácil de medir en lugar de lo que te importa.** No vi el patrón hasta que me lo encontré en los dos sitios.

---

## Cuando el hardening rompió la monitorización

Parte del endurecimiento del host activó el modo sigiloso del firewall, que deja de responder a ICMP.

El monitor de uptime externo comprobaba el host por ping.

El monitor reportó el servidor principal como caído de inmediato. Llevaba todo el rato en pie.

El arreglo tentador es volver a desactivar el modo sigiloso, porque ahora el monitor se equivoca y el monitor molesta. El arreglo correcto es el otro: **cambiar el monitor a una comprobación TCP contra un puerto que de verdad esté sirviendo.** Un ping demuestra que una pila de red responde a ICMP. Una comprobación TCP demuestra que hay algo escuchando, que es más parecido a lo que "arriba" quería decir desde el principio.

Merece la pena escribirlo porque el reflejo va en la dirección equivocada. Cuando un control de seguridad rompe un control operativo, el que suele estar midiendo mal es el segundo — pero es el que hace ruido, así que gana la discusión.

---

## El marcador

Como la auditoría es un script, el progreso es un número y no una afirmación:

| Ejecución | Controles | Pasan | Fallan |
| --- | --- | --- | --- |
| Tras la fase uno | 67 | 15 | 2 |
| Tras los sprints de hardening | 81 | 27 | **0** |

El número de controles creció porque arreglar cosas revela cosas que merece la pena comprobar. Ese crecimiento es el más sano de los dos números: un conjunto de controles estático deja de encontrar nada en cuanto lo has satisfecho.

Junto a los arreglos se añadieron tres capacidades que antes no existían: protección contra fuerza bruta con lista blanca de la LAN, escaneo de vulnerabilidades sobre las imágenes de contenedor, y un observador externo corriendo en hardware separado, de modo que el fallo total del host principal sea siquiera detectable.

---

## Qué se aplazó, y por qué

Las fases posteriores del plan siguen en marcha. Son los ítems estructurales —los que exigen cambiar cómo está dispuesta la red y no cambiar un ajuste— y se secuenciaron los últimos a propósito, porque cada uno arriesga romper servicios que funcionan y ninguno resuelve un hallazgo crítico.

Esa secuenciación es la parte que defendería en una revisión: **arregla primero lo crítico y barato, y acepta los ítems estructurales como conocidos y planificados en vez de fingir que el plan está completo.** Un plan de remediación con todo marcado como hecho suele ser un plan acotado a lo que era fácil.

---

## El punto ciego que tuvo la auditoría

La auditoría cubrió configuración del sistema, postura de los contenedores, exposición de red y autenticación de servicios. No cubrió **secretos a nivel de aplicación dentro de los scripts operativos**.

Meses después, instrumentando la monitorización, encontré credenciales de API escritas a fuego en seis scripts de Python. Nunca se publicaron en ningún sitio y se movieron a un fichero cargado en tiempo de ejecución fuera del control de versiones, pero la auditoría había pasado limpia sobre un sistema que las contenía, porque esa categoría no estaba en el alcance.

No es un fallo de ejecución. Es lo que una auditoría es: una foto acotada por un alcance, tomada en un momento. De ahí se siguen dos cosas.

**El alcance lo es todo.** Lo que queda fuera es invisible, y el informe no te lo va a advertir.

**Los sistemas derivan después de auditarlos.** La razón por la que el script importa más que el informe es que un script se puede volver a ejecutar. El informe describía abril; el script puede describir hoy.

La ejecución semanal automatizada es el ítem que más lamento no haber terminado.

---

## Qué haría distinto

**Probar el comportamiento antes de leer la configuración.** Lo de SSH habría salido en los primeros cinco minutos con un intento de login, en vez de tras una cadena de arqueología de configuración cada vez más ingeniosa.

**Escribir el comprobador antes que la checklist.** El documento era el entregable que pedí. El script es el artefacto que sigue teniendo valor un año después.

**Contar con que la auditoría rompa algo.** Lo rompió, de inmediato, y la recuperación enseñó más que el arreglo. Presupuéstalo en vez de sorprenderte.
