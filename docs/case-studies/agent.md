# El agente

> Un agente que opera infraestructura real desde Telegram, enrutando cada petición al modelo más barato que pueda resolverla — y las tres formas en que eso falló en silencio.

---

## Qué es

Un agente conversacional al que se le escribe por Telegram. Puede responder preguntas, consultar la base de conocimiento, y **ejecutar acciones sobre la infraestructura**: listar contenedores, leer sus logs, reiniciar servicios, consultar el estado de la domótica, comprobar el espacio en disco.

Corre además un puñado de tareas programadas sin supervisión, y una pasada nocturna que consolida su propia memoria.

### Por qué Telegram

No es una decisión de producto, es una consecuencia.

La alternativa natural sería una interfaz web. Eso implica publicar un servicio más, protegerlo con autenticación, mantener un frontend, y —lo importante— **abrir una superficie de acceso adicional a un sistema que ya tiene bastante**.

Telegram elimina todo eso. Ya está instalado en el móvil, la autenticación es la del propio dispositivo, funciona desde fuera de casa sin exponer ningún puerto, y las notificaciones que ya emiten los monitores llegan por el mismo sitio. El agente y las alertas comparten canal, lo cual significa que cuando salta un aviso puedes responder preguntando por él sin cambiar de aplicación.

El coste es una interfaz de texto plano, sin componentes ricos ni estado visual. Para operar infraestructura eso resulta ser suficiente.

### Por qué no corre en un contenedor

El agente gestiona el runtime de contenedores. Meterlo dentro de aquello que gestiona crea una dependencia circular cuyo modo de fallo solo aparece cuando algo ya ha ido mal: si Docker tiene un problema, el agente que podría diagnosticarlo está caído exactamente por ese problema.

Corre como proceso nativo del sistema, gestionado por el planificador del sistema operativo. Está desarrollado en [Arquitectura](../architecture.md).

---

## Enrutado de modelos

### La aritmética

La razón de que exista enrutado es de coste, y conviene ponerle números aproximados.

Una conversación interactiva son unas pocas peticiones al día. Aunque cada una arrastre bastante contexto, el total mensual es despreciable con cualquier proveedor.

Una tarea programada es otra cosa. Corre a horas fijas, lleva su contexto de sistema completo, encadena varias llamadas a herramientas, y **nadie la mira**. Multiplicado por varias tareas y treinta días, ya no es despreciable. Y sobre todo: es un gasto que crece solo, sin que ninguna acción tuya lo dispare.

De ahí la regla: **lo automático va a local por defecto; se sube a la nube solo lo que demuestre que lo necesita.**

### El reparto actual

| Carga de trabajo | Backend | Motivo |
| --- | --- | --- |
| Chat interactivo | Modelo cloud | Hay un humano esperando; manda la calidad |
| Tareas de **una** llamada a herramienta | `qwen3.5-9b` local | Coste cero, y es capaz |
| Tareas **multi-tool** | Modelo cloud | El local no puede — ver abajo |

El modelo local se sirve desde LM Studio, que expone una API compatible con OpenAI en el loopback. El enrutado no está en el modelo: está en la configuración, con un **pin por tarea**. El modelo por defecto es el cloud, y las tareas concretas que van a local lo declaran explícitamente.

Esa asimetría es intencionada. Si añado una tarea nueva y me olvido de configurarla, hereda el cloud: funciona y cuesta dinero. Al revés heredaría el local: funcionaría a veces y fallaría en silencio, que es mucho peor.

---

## El incidente que forzó todo esto

### Los crons dejaron de funcionar de golpe

Un día, todas las tareas programadas que usaban el agente empezaron a marcarse en error:

```
last_status: error
last_error: "RuntimeError: HTTP 402: Insufficient Balance"
```

**La cuenta del proveedor cloud se había quedado sin saldo.** Sin aviso previo por mi parte, porque nunca había mirado el consumo — el gasto era pequeño y crecía despacio, que es exactamente la forma de gasto que uno no vigila.

Un 402 es un error honesto y ruidoso, así que esta parte se diagnosticó en minutos.

### La parte interesante: el fallback no salvó nada

La configuración tenía un fallback a local. Si el proveedor cloud falla, se supone que las tareas caen al modelo local y siguen funcionando.

No pasó. **LM Studio estaba corriendo pero no tenía ningún modelo cargado.**

Un servidor de inferencia sin modelo cargado responde a la comprobación de salud —el proceso está vivo, el puerto acepta conexiones— pero no puede servir ninguna petición. El fallback existía, apuntaba a un sitio que respondía, y no servía para nada.

Es el mismo patrón que recorre este proyecto entero, aplicado a otra capa: **algo que parece disponible porque responde, pero que no puede hacer su trabajo.**

### Lo que se arregló

**El modelo se carga al arrancar la máquina**, mediante un servicio del sistema que levanta el servidor de inferencia y carga explícitamente el modelo. Es idempotente, tiene reintentos, y avisa por Telegram si no lo consigue.

**El contexto tuvo que subirse a 64K.** El runtime del agente exige un mínimo de 64.000 tokens de contexto, y el valor por defecto de LM Studio era de unos 24.800. Con ese ajuste el modelo cargaba correctamente y después fallaba al usarse, con un error que no apuntaba al contexto.

**Las tareas simples se fijaron a local** de forma explícita, en vez de depender de un fallback. Un fallback es un mecanismo de emergencia; si quieres que algo corra en local, dilo.

---

## Dónde deja de servir un modelo de 9B

### El síntoma

Al pasar las tareas a local, unas funcionaron perfectamente y otras no. El patrón tardó en verse porque **no fallaban: entregaban algo**.

Las tareas que hacen **una sola llamada a herramienta** —consultar el disco, ejecutar un script y resumir la salida— funcionan con el modelo local sin ningún problema.

Las tareas **multi-tool**, que encadenan varias llamadas y deciden la siguiente en función del resultado de la anterior, devolvían esto en el log del runtime:

```
↻ Empty response after tool calls
```

El modelo ejecutaba las herramientas correctamente. Recibía los resultados. Y después producía una respuesta final vacía. Ante eso, el runtime entregaba lo único que tenía: **la narración del plan** que el modelo había verbalizado antes de empezar.

El resultado que llega a Telegram es del estilo *"ahora consultaré el estado de los contenedores y prepararé un resumen"*. En vez del resumen.

### Por qué es peligroso

Enumerando el modo de fallo:

- La tarea **termina con código de salida 0**
- No hay excepción, ni timeout, ni error en ningún log salvo esa línea del runtime
- El estado de la tarea queda en `ok`
- La salida es prosa **gramaticalmente perfecta y plausible**
- Llega por el canal habitual, a la hora habitual

Si lees ese mensaje por encima a las tres de la mañana, parece que funcionó. Y si la tarea es un resumen diario del estado del sistema, un mensaje que dice "voy a revisar el sistema" se parece bastante a un mensaje que dice que el sistema está bien.

### Lo que se intentó antes de rendirse

Lo primero que uno prueba es arreglarlo con el prompt: pedir explícitamente una frase de resumen al final, insistir en que devuelva el resultado y no el plan, estructurar la instrucción de otra manera.

**No se arregla por prompt.** Aun exigiendo de forma explícita una frase-resumen final, la respuesta sigue vaciándose tras las llamadas a herramientas. El mismo prompt, la misma tarea y las mismas herramientas contra el modelo cloud entregan una respuesta limpia.

No es un problema de instrucciones. Es una limitación del modelo manteniendo coherencia a lo largo de una secuencia larga de intercambios con herramientas, y ningún ajuste de prompt lo compensa.

### La regla resultante

**Modelo local: tareas de una sola llamada a herramienta. Modelo cloud: todo lo que encadene varias.**

Es el tipo de límite que no aparece en ningún benchmark y que solo se encuentra dejando algo corriendo sin supervisión durante semanas. Un banco de pruebas mide si el modelo sabe usar una herramienta; no mide si aguanta la sexta.

---

## Dos limitaciones que no se han resuelto

Merece la pena enunciarlas porque son reales y siguen ahí.

### El esfuerzo de razonamiento es global, no por proveedor

El runtime envía el mismo parámetro de `reasoning_effort` a todos los modelos. El modelo local no soporta el valor configurado y degrada silenciosamente a otro, emitiendo un aviso.

Intentar acotarlo por proveedor —definirlo solo para el backend local— **no se respeta**: la configuración global gana. Y bajar el valor global degradaría también al modelo cloud, que es el que atiende las conversaciones interactivas.

Se quedó como está. Es una limitación del runtime, no del diseño, y el coste real es pequeño; pero es exactamente la clase de detalle que uno descubre solo al mezclar dos proveedores con capacidades distintas.

### Las tareas fijadas a local no tienen red de seguridad

Al fijar explícitamente las tareas simples al modelo local, se renunció al fallback. Si el servidor de inferencia se cae, esas tareas fallan hasta que vuelva.

El servicio del sistema lo recarga en cada arranque, pero **no es un watchdog**: no vigila que siga vivo durante el día. Es una decisión consciente —fallar de forma visible en una tarea auxiliar es aceptable— pero está sin resolver y conviene decirlo.

---

## Tool calling contra infraestructura real

### Qué puede hacer

Las herramientas del agente no son consultas a una API de juguete. Ejecutan comandos contra el sistema: listar el estado de los contenedores, leer las últimas líneas de log de uno concreto, reiniciarlo, consultar el espacio en disco, interrogar la API de domótica, buscar en la base de conocimiento.

Es lo que hace útil al agente y lo que lo hace peligroso.

### La frontera entre actuar y preguntar

Los contenedores están clasificados en tres categorías, y la clasificación **está en el código, no en el criterio del modelo**:

**Críticos** — bases de datos, gestor de contraseñas, domótica, proxy inverso. Sobre estos el sistema **alerta pero no toca nada**. El razonamiento es que un reinicio a ciegas de una base de datos con escrituras en vuelo puede convertir una incidencia menor en corrupción de datos.

**Nunca tocar** — contenedores apagados a propósito.

**El resto** — reiniciar es casi siempre inocuo y casi siempre resuelve, así que se automatiza.

Que esa frontera viva en una lista explícita y no en el juicio del modelo es deliberado. Un modelo de lenguaje puede razonar muy bien sobre si conviene reiniciar una base de datos y equivocarse el día que el contexto le llega truncado. Una lista no se equivoca.

### Aprobación humana

Para las acciones que sí requieren intervención, el patrón es escalar a Telegram con la información necesaria para decidir: qué se ha caído, desde cuándo, y las últimas líneas de log. La decisión la toma una persona.

La diferencia entre esto y un agente con permisos totales no es filosófica: es que el radio de impacto de una alucinación queda acotado a lo que el sistema ya considera reversible.

---

## Tareas programadas, y el fallo silencioso que aún queda

### Qué corren

Un resumen diario del estado del sistema, un digest de noticias, mantenimiento periódico de la base de conocimiento, y la consolidación de memoria nocturna. Cada una con su horario y su modelo fijado según la regla de arriba.

### El problema que sigue abierto

Una de las tareas locales vigila el espacio en disco y **solo responde si algún disco supera un umbral**. En condiciones normales no dice nada, y el runtime la registra como ejecutada correctamente y silenciosa.

Ahí está el problema: **un resultado silencioso con estado `ok` no demuestra que la herramienta se llegara a ejecutar.**

Un modelo que decide no llamar a la herramienta y responder directamente "todo correcto" produce exactamente la misma salida que uno que la ejecuta y comprueba que no hay nada que reportar. El estado es `ok` en ambos casos. Y la ausencia de mensaje —que es la señal de que todo va bien— es también la señal de que nada se ha comprobado.

Es, otra vez, el patrón de este repositorio entero: **un indicador en verde que mide una cosa distinta de la que promete.** En este caso el riesgo real es bajo, porque el monitor de contenedores también vigila los discos cada cinco minutos por su cuenta y no depende de ningún modelo. Pero esa mitigación es una casualidad afortunada, no un diseño.

El arreglo correcto sería que la tarea reportase siempre, aunque fuera con una línea, de modo que el silencio significase fallo en lugar de normalidad. No está hecho.

---

## Consolidación de memoria

### El problema

El agente acumula notas y diarios cada día: decisiones tomadas, errores resueltos, configuraciones cambiadas. Sin ningún proceso que organice eso, en pocas semanas hay miles de anotaciones y ninguna forma de saber cuáles importan. Una libreta llena sin índice.

Y el contexto de un modelo es finito: no se puede arrastrar todo indefinidamente.

### Cómo funciona

Corre cada noche, en cinco fases:

**1. Detección de repetición.** Lee los diarios de los últimos siete días y busca ideas, frases o hechos que aparezcan más de una vez en contextos distintos. La heurística es simple y sorprendentemente buena: si algo se menciona varias veces, probablemente importa.

**2. Evaluación.** Los candidatos se envían a un modelo que puntúa cada uno de 0 a 1 según si merece recordarse indefinidamente. Puntúan alto las configuraciones, decisiones técnicas, rutas importantes y errores resueltos; puntúan bajo los saludos, las conversaciones triviales y lo que ya está anotado. **Solo pasan los que superan 0,6.**

**3. Promoción.** Los aprobados se escriben en el fichero de memoria a largo plazo, que es lo primero que el agente lee al arrancar.

**4. Archivado.** Los diarios de más de catorce días se mueven a una carpeta de archivo. Siguen existiendo, pero dejan de ocupar espacio en la memoria activa.

**5. Reindexado.** Se actualiza el índice de búsqueda semántica para que lo nuevo sea recuperable en lenguaje natural.

### Por qué es la tarea que más problemas dio

Es multi-tool por naturaleza: lee, evalúa, escribe, archiva, reindexa. Fue la primera en manifestar el problema del modelo local narrando en vez de ejecutar, y la razón por la que se investigó ese comportamiento a fondo.

El umbral de 0,6 es un parámetro sin justificar empíricamente. Se eligió a ojo y no se ha medido nunca cuántas promociones son acertadas. Es una de las cosas que un sistema de evaluación en condiciones debería responder.

---

## Integración con la base de conocimiento

El agente expone comandos dedicados que consultan el RAG desde Telegram, y también lo usa internamente cuando una pregunta necesita contexto que no está en la conversación.

Lo relevante desde el punto de vista del agente es que la recuperación es **una herramienta más**: el modelo decide cuándo invocarla. Eso significa que la calidad de la respuesta depende de dos cosas encadenadas — que el modelo decida buscar, y que la búsqueda devuelva lo correcto — y que un fallo en cualquiera de las dos produce el mismo síntoma.

El motor de recuperación está descrito en [El RAG](rag.md).

---

## Lo que no está evaluado

Esta es la sección que hace honesta a la página.

**No hay evaluación cuantitativa de nada de lo anterior.** No hay un conjunto de casos de prueba, ni una métrica de acierto en la selección de herramientas, ni una comparación medida entre el modelo local y el cloud más allá de "uno entrega el resultado y el otro no".

La regla de reparto entre modelos —una tool call a local, varias a cloud— salió de observar fallos, no de medir un umbral. Es probable que el límite real no esté exactamente en dos llamadas, sino que dependa del tamaño del contexto acumulado, de la longitud de las salidas de las herramientas, o de ambas cosas. No lo sé, porque no lo he medido.

El umbral de 0,6 de la consolidación de memoria no tiene ningún respaldo empírico.

Lo que sí hay es **un sistema en producción, con fallos reales documentados y sus causas identificadas**, que es una forma distinta de conocimiento y no un sustituto. La evaluación sistemática —casos dorados, métricas de recuperación, comparativas reproducibles— es el siguiente trabajo, y hasta que exista, todo lo de esta página son observaciones cualitativas hechas con cuidado.

Decirlo es preferible a fingir lo contrario. Un agente sobre el que se afirma que funciona bien, sin números que lo respalden, es exactamente lo que hay en la mayoría de los proyectos de esta categoría.
