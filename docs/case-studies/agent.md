# El agente

> Un agente que opera infraestructura real desde Telegram — cómo el intento de hacerlo barato con un modelo local falló en silencio, y por qué al final el mejor diseño fue que nada esencial dependiera de él.

---

## Qué es

Un agente conversacional, construido sobre Hermes Agent, al que se le escribe por Telegram. Puede responder preguntas, consultar la base de conocimiento, y **ejecutar acciones sobre la infraestructura y la casa**: listar contenedores, leer sus logs, reiniciar servicios, consultar y mover la domótica, comprobar el espacio en disco.

Corre además un par de tareas programadas, una pasada nocturna que consolida su propia memoria, y lo **despiertan** otros sistemas cuando hace falta criterio: tras un corte de luz, o ante una avería que nadie sabe arreglar todavía.

### Por qué Telegram

No es una decisión de producto, es una consecuencia.

La alternativa natural sería una interfaz web. Eso implica publicar un servicio más, protegerlo con autenticación, mantener un frontend, y —lo importante— **abrir una superficie de acceso adicional a un sistema que ya tiene bastante**.

Telegram elimina todo eso. Ya está instalado en el móvil, la autenticación es la del propio dispositivo, funciona desde fuera de casa sin exponer ningún puerto, y las notificaciones que ya emiten los monitores llegan por el mismo sitio. El agente y las alertas comparten canal, lo cual significa que cuando salta un aviso puedes responder preguntando por él sin cambiar de aplicación.

El coste es una interfaz de texto plano, sin componentes ricos ni estado visual. Para operar infraestructura eso resulta ser suficiente.

### Por qué no corre en un contenedor

El agente gestiona el runtime de contenedores. Meterlo dentro de aquello que gestiona crea una dependencia circular cuyo modo de fallo solo aparece cuando algo ya ha ido mal: si Docker tiene un problema, el agente que podría diagnosticarlo está caído exactamente por ese problema.

Corre como proceso nativo del sistema, gestionado por el planificador del sistema operativo. Está desarrollado en [Arquitectura](../architecture.md).

---

## Lo que el agente no hace

La decisión más importante sobre el agente es lo que **dejó de hacer**.

En la primera versión, el agente era el centro: generaba el informe diario del sistema, enviaba la agenda, vigilaba el espacio en disco, decidía qué reiniciar. Cada fallo del agente —un proveedor caído, una cuenta sin saldo, una actualización del runtime que rompía algo, un modelo que respondía mal— se llevaba por delante cosas que no tenían nada que ver con conversar.

Hoy la regla es la contraria: **todo lo esencial es código determinista que corre sin él.** Vigilar, reparar, copiar, recuperarse de un corte de luz, avisar y escribir el informe de la mañana no necesitan un modelo de lenguaje, así que no lo usan. El agente entra cuando hace falta criterio, y si se cae, se pierde la conversación y poco más.

Las páginas siguientes cuentan cómo se llegó ahí, porque el camino es más instructivo que el destino.

---

## La historia del enrutado de modelos

### La aritmética

El primer diseño tenía enrutado de modelos por coste, y conviene ponerle números aproximados al porqué.

Una conversación interactiva son unas pocas peticiones al día. Aunque cada una arrastre bastante contexto, el total mensual es despreciable con cualquier proveedor.

Una tarea programada es otra cosa. Corre a horas fijas, lleva su contexto de sistema completo, encadena varias llamadas a herramientas, y **nadie la mira**. Multiplicado por varias tareas y treinta días, ya no es despreciable. Y sobre todo: es un gasto que crece solo, sin que ninguna acción tuya lo dispare.

De ahí la regla de entonces: **lo automático va a local por defecto; se sube a la nube solo lo que demuestre que lo necesita.** El modelo local era `qwen3.5-9b`, servido desde LM Studio en el loopback, con un pin por tarea en la configuración.

### Los crons dejaron de funcionar de golpe

Un día, todas las tareas programadas que usaban el agente empezaron a marcarse en error:

```
last_status: error
last_error: "RuntimeError: HTTP 402: Insufficient Balance"
```

**La cuenta del proveedor cloud se había quedado sin saldo.** Sin aviso previo por mi parte, porque nunca había mirado el consumo — el gasto era pequeño y crecía despacio, que es exactamente la forma de gasto que uno no vigila.

Un 402 es un error honesto y ruidoso, así que esta parte se diagnosticó en minutos.

### El fallback no salvó nada

La configuración tenía un fallback a local. Si el proveedor cloud falla, se supone que las tareas caen al modelo local y siguen funcionando.

No pasó. **LM Studio estaba corriendo pero no tenía ningún modelo cargado.**

Un servidor de inferencia sin modelo cargado responde a la comprobación de salud —el proceso está vivo, el puerto acepta conexiones— pero no puede servir ninguna petición. El fallback existía, apuntaba a un sitio que respondía, y no servía para nada.

Es el mismo patrón que recorre este proyecto entero, aplicado a otra capa: **algo que parece disponible porque responde, pero que no puede hacer su trabajo.**

### Dónde deja de servir un modelo de 9B

Al pasar las tareas a local, unas funcionaron perfectamente y otras no. El patrón tardó en verse porque **no fallaban: entregaban algo**.

Las tareas que hacen **una sola llamada a herramienta** —consultar el disco, ejecutar un script y resumir la salida— funcionaban con el modelo local sin ningún problema.

Las tareas **multi-tool**, que encadenan varias llamadas y deciden la siguiente en función del resultado de la anterior, devolvían esto en el log del runtime:

```
↻ Empty response after tool calls
```

El modelo ejecutaba las herramientas correctamente. Recibía los resultados. Y después producía una respuesta final vacía. Ante eso, el runtime entregaba lo único que tenía: **la narración del plan** que el modelo había verbalizado antes de empezar.

El resultado que llega a Telegram es del estilo *"ahora consultaré el estado de los contenedores y prepararé un resumen"*. En vez del resumen.

Enumerando el modo de fallo:

- La tarea **termina con código de salida 0**
- No hay excepción, ni timeout, ni error en ningún log salvo esa línea del runtime
- El estado de la tarea queda en `ok`
- La salida es prosa **gramaticalmente perfecta y plausible**
- Llega por el canal habitual, a la hora habitual

Si lees ese mensaje por encima a las tres de la mañana, parece que funcionó. Y si la tarea es un resumen diario del estado del sistema, un mensaje que dice "voy a revisar el sistema" se parece bastante a un mensaje que dice que el sistema está bien.

**No se arregla por prompt.** Aun exigiendo de forma explícita una frase-resumen final, la respuesta seguía vaciándose tras las llamadas a herramientas. El mismo prompt, la misma tarea y las mismas herramientas contra el modelo cloud entregaban una respuesta limpia. Es una limitación del modelo manteniendo coherencia a lo largo de una secuencia larga de intercambios con herramientas — el tipo de límite que no aparece en ningún benchmark. Un banco de pruebas mide si el modelo sabe usar una herramienta; no mide si aguanta la sexta.

### Cómo terminó

La regla resultante fue *local para una sola herramienta, cloud para varias*. Duró poco, porque la pregunta siguiente era obvia: **¿las tareas de una sola herramienta necesitaban un modelo?**

No. Eran "consulta esto y resúmelo", y un resumen de un estado conocido se escribe mejor con código: siempre igual, siempre completo, sin coste y sin forma de narrar en lugar de hacer. Una a una, salieron del agente — el informe diario, la agenda, la vigilancia del disco. Cuando el modelo local se quedó sin trabajo, salió de la máquina y liberó la memoria que ocupaba.

Hoy el agente usa un modelo principal en la nube y una **reserva más barata** que entra sola si el principal falla —caída, clave, saldo— y **lo avisa por Telegram**. Las dos lecciones del 402 están aplicadas: hay un monitor que vigila el saldo de las cuentas antes de que se agote, y el fallback se anuncia en vez de esconderse.

---

## Tool calling contra infraestructura real

### Qué puede hacer

Las herramientas del agente no son consultas a una API de juguete. Ejecutan comandos contra el sistema: listar el estado de los contenedores, leer las últimas líneas de log de uno concreto, reiniciarlo, consultar el espacio en disco, interrogar y mover la domótica, buscar en la base de conocimiento.

Es lo que hace útil al agente y lo que lo hace peligroso.

### La frontera entre actuar y preguntar

La primera versión resolvía el peligro con listas: contenedores **críticos** —bases de datos, gestor de contraseñas, domótica, proxy inverso— sobre los que el sistema alertaba pero no tocaba nada; contenedores que **nunca** se tocaban; y el resto, que se reiniciaba solo. Que esa frontera viviera en una lista explícita y no en el juicio del modelo era deliberado: un modelo puede razonar muy bien sobre si conviene reiniciar una base de datos y equivocarse el día que el contexto le llega truncado.

La frontera se ha movido, pero el principio sigue: **el modelo no decide qué puede hacer, solo si algo aplica.**

- La **remediación automática** tiene una lista cerrada de acciones reversibles —reiniciar un contenedor, reiniciar un servicio, rotar un log—. Un modelo lee la evidencia del episodio y responde una sola pregunta: ¿alguna de estas acciones lo resuelve? Si dice que no, no hay acción; si dice que sí, la acción se ejecuta **y se verifica**, con un cortacircuitos si se repite. Con esa verificación, reiniciar también los críticos resultó menos arriesgado que esperar a que alguien leyera el aviso.
- Lo que no cabe en esa lista lo atiende el **Aprendiz**: la primera vez que aparece una avería guarda la evidencia y anota cómo se arregló; con eso propone una **receta**. **Las recetas las confirma una persona**, y solo una receta confirmada se aplica sola la vez siguiente. Ahí es donde el agente actúa: el Aprendiz lo despierta con la receta y la evidencia, el agente la ejecuta, y el Aprendiz comprueba si la alarma desapareció.

### Aprobación humana

La aprobación humana no desapareció; se movió de sitio. Antes estaba en **cada incidente**: qué se ha caído, desde cuándo, las últimas líneas de log, decide tú. Eso escala mal, y en la práctica significa que una avería a las tres de la mañana espera a las ocho.

Ahora está en **cada tipo de arreglo**: una persona confirma una vez cómo se arregla una clase de avería, y a partir de ahí se arregla sola, con verificación y con límite de intentos. El radio de impacto de una alucinación sigue acotado a lo que alguien ha considerado reversible, pero la persona deja de ser el cuello de botella.

---

## Las tareas programadas, y el fallo silencioso que se cerró

### El problema

Una de las tareas que corría en el modelo local vigilaba el espacio en disco y **solo respondía si algún disco superaba un umbral**. En condiciones normales no decía nada, y el runtime la registraba como ejecutada correctamente y silenciosa.

Ahí estaba el problema: **un resultado silencioso con estado `ok` no demuestra que la herramienta se llegara a ejecutar.** Un modelo que decide no llamar a la herramienta y responder directamente "todo correcto" produce exactamente la misma salida que uno que la ejecuta y comprueba que no hay nada que reportar. Y la ausencia de mensaje —que es la señal de que todo va bien— es también la señal de que nada se ha comprobado.

### Cómo se cerró

No haciendo que el modelo reportara siempre, sino quitándole la tarea. La vigilancia del disco es un monitor determinista con su propio latido: si deja de pasar, el latido envejece y la central de alarmas lo marca. **El silencio ahora significa fallo, no normalidad**, porque hay algo esperando activamente noticias suyas.

Lo que queda programado en el agente es lo que de verdad necesita un modelo: un digest de noticias, el mantenimiento de su propia memoria, y la purga periódica de lo borrado en la base de conocimiento.

---

## Consolidación de memoria

### El problema

El agente acumula notas cada día: decisiones tomadas, errores resueltos, configuraciones cambiadas. Sin ningún proceso que organice eso, en pocas semanas hay miles de anotaciones y ninguna forma de saber cuáles importan. Una libreta llena sin índice.

Y el contexto de un modelo es finito: no se puede arrastrar todo indefinidamente.

### Cómo funciona

Corre cada noche, en tres fases:

**1. Qué ha pasado.** Busca en las conversaciones del día los hechos, decisiones y temas que se repiten. La heurística es simple y sorprendentemente buena: si algo se menciona varias veces, en contextos distintos, probablemente importa.

**2. Qué vale la pena.** Puntúa cada candidato. Puntúan alto las configuraciones, decisiones técnicas, rutas importantes y errores resueltos; puntúan bajo los saludos, las conversaciones triviales y lo que ya está anotado.

**3. Guardar.** Lo que supera el umbral se escribe en la memoria a largo plazo, que es lo primero que el agente lee al empezar una conversación, y todo lo que ha hecho queda en un registro aparte.

La primera versión era un script con su propio modelo local y su propio índice de búsqueda; hoy lo hace el propio agente siguiendo unas instrucciones escritas. No manda mensaje: su resumen aparece en el informe de la mañana, y si una noche no corre, el informe lo dice.

### Por qué fue la tarea que más problemas dio

Es multi-tool por naturaleza: lee, evalúa, escribe. Fue la primera en manifestar el problema del modelo local narrando en vez de ejecutar, y la razón por la que se investigó ese comportamiento a fondo.

El umbral de promoción sigue sin justificación empírica. Se eligió a ojo y no se ha medido nunca cuántas promociones son acertadas.

---

## Integración con la base de conocimiento

El agente consulta el RAG a través de un servidor MCP: desde Telegram con comandos dedicados, y también internamente cuando una pregunta necesita contexto que no está en la conversación.

Lo relevante desde el punto de vista del agente es que la búsqueda en el corpus es **una herramienta más**: el modelo decide cuándo invocarla. Eso significa que la calidad de la respuesta depende de dos cosas encadenadas — que el modelo decida buscar, y que la búsqueda devuelva lo correcto — y que un fallo en cualquiera de las dos produce el mismo síntoma.

El motor de recuperación está descrito en [El RAG](rag.md).

---

## Lo que no está evaluado

Esta es la sección que hace honesta a la página.

**No hay evaluación cuantitativa del agente.** No hay un conjunto de casos de prueba, ni una métrica de acierto en la selección de herramientas, ni una comparación medida entre modelos.

La conclusión sobre el modelo de 9B —que se pierde a partir de la tercera o cuarta llamada encadenada— salió de observar fallos, no de medir un umbral. Es probable que el límite real dependa del tamaño del contexto acumulado o de la longitud de las salidas de las herramientas. No lo sé, porque no lo medí antes de retirarlo.

El umbral de la consolidación de memoria no tiene ningún respaldo empírico.

Lo que sí hay es medición **del sistema alrededor del agente**: cuánto se tarda en detectar y reparar cada avería provocada, y quién la reparó —Docker, el sistema, la remediación, el agente con una receta, o una persona—. Eso dice cuándo el agente es necesario, pero no cómo de bien hace su trabajo cuando lo es. La evaluación sistemática del agente en sí —casos dorados, comparativas reproducibles— sigue siendo trabajo pendiente, y hasta que exista, todo lo de esta página son observaciones cualitativas hechas con cuidado.

Decirlo es preferible a fingir lo contrario.
