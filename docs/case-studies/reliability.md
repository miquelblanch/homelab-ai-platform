# Fiabilidad

> La distancia entre un sistema que reporta verde y un sistema que funciona.

Esta página es la versión larga de la tabla de la portada. Tres indicadores llevaban meses en verde. Ninguno estaba roto. Ninguno medía lo que su etiqueta prometía.

Los encontré los tres en una sola tarde, y solo porque me había puesto a hacer otra cosa: empezar a registrar métricas para tener números que contar.

---

## Qué había antes de empezar

Conviene entender qué hacía el sistema, porque todo lo que viene después son matices sobre esta base.

Un script en Python, sin dependencias externas, lanzado por el planificador de macOS (`launchd`) **cada cinco minutos**. En cada ejecución:

1. Lista los 41 contenedores con `docker ps -a` y se queda con nombre, estado y si está corriendo.
2. Para cada uno decide en qué categoría cae:
   - **Crítico** (Home Assistant, las bases de datos, el proxy inverso, el gestor de contraseñas): si se cae, **alerta pero no toca nada**. Reiniciar una base de datos a ciegas puede ser peor que la caída.
   - **Nunca tocar**: un contenedor apagado a propósito, que se ignora.
   - **El resto**: si se ha caído, **lo reinicia solo**.
3. Comprueba el porcentaje de uso de los tres discos contra sus umbrales.
4. Manda a Telegram lo que haya que mandar.

Un detalle de diseño que importa para todo lo que viene: **solo notifica en el cambio de estado**. Guarda en un fichero JSON si cada contenedor estaba bien en la ejecución anterior, y solo habla cuando pasa de bien a mal o de mal a bien. Sin eso, un contenedor caído generaría 288 mensajes al día.

Esto llevaba meses funcionando sin fallar. Y **lo tiraba todo**: la alerta iba a Telegram, el mensaje se leía, y no quedaba registro de nada.

### El primer intento de arreglarlo

Existía una base de datos SQLite con una tabla llamada `container_metrics`. Tenía 265.365 filas. La más reciente era de setenta días antes.

Mi primera hipótesis fue la evidente: algo se rompió y nadie lo notó. Así que fui a buscar el código que escribía en esa tabla.

```
grep -r "container_metrics" .
# (sin resultados)
```

Cero resultados en todo el repositorio. No es que el escritor fallara: **no existía**. Rastreando un poco más, la explicación apareció: la tabla la alimentaba un componente que se desmanteló meses atrás. Cuando se borró el componente, se llevó por delante al escritor. La base de datos se quedó ahí, huérfana, y nadie la echó de menos porque nada la leía.

Así que la tarea real no era reparar nada. Era **darle memoria al monitor que ya funcionaba**, que cada cinco minutos tenía todos esos datos en la mano y los estaba tirando.

Esa es la primera lección del día, y la más barata: **los datos que nadie lee dejan de producirse y nadie se entera.** No hay alerta posible para la desaparición de algo de lo que no depende nada.

---

## Hallazgo 1 — un 100% de éxito que medía códigos de salida

### Cómo llegué a mirar ahí

Una vez el monitor empezó a escribir métricas, me quedaba una pregunta obvia: **¿cuántas veces ha intervenido este sistema, y con qué resultado?** Es el número que resume si la recuperación automática aporta algo o es decoración.

La respuesta debería estar en una tabla llamada `restart_history`. Y ahí empezó el segundo problema.

### El histórico estaba partido en dos

Había **dos** tablas, no una:

| Tabla | Periodo | Filas | Esquema |
| --- | --- | --- | --- |
| `restart_history_v0` | mar – 20 abr 2026 | 79 | `timestamp` en texto ISO, `success` como entero 0/1 |
| `restart_history` | 23 abr – may 2026 | 7 | `timestamp` en epoch, `result` como texto |

Alguien —yo— había cambiado el esquema en abril: pasar de un booleano `success` a un campo `result` de texto que admitiera más estados que "fue bien / fue mal". La migración creó la tabla nueva y dejó la vieja donde estaba. Razonable en el momento, y la razón por la que tres meses después los datos no servían para responder a una pregunta simple.

De las 7 filas de la tabla nueva, además, **tres eran basura de pruebas**: un contenedor llamado literalmente `contenedor-que-no-existe`, y dos de cuando estaba probando el healer.

### Consolidarlas

Escribí una migración en lugar de lanzar un `INSERT ... SELECT` a mano, por tres motivos que en su momento parecen exagerados y luego no:

**Simulación por defecto.** El script no escribe nada salvo que le pases `--apply`. Imprime el plan: cuántas filas va a insertar, de qué rango de fechas, cuántas filas de prueba ha detectado.

**Copia de seguridad con la API de SQLite, no con `cp`.** La base de datos está viva: el monitor escribe en ella cada cinco minutos. Copiar el fichero a pelo mientras hay una transacción abierta y un WAL sin consolidar da una copia inconsistente. `sqlite3.backup()` respeta el bloqueo y produce una copia coherente.

**Idempotencia mediante una tabla `migrations`.** El script se registra al terminar. Si lo lanzas dos veces, la segunda no hace nada y te lo dice. Sin eso, un dedazo duplica 79 eventos históricos y corrompe todos los porcentajes que vengan después.

La conversión en sí fue trivial: `timestamp` ISO a epoch, y `success = 1` a `result = 'success'`. Lo que no era trivial era comprobar que no hubiera solapamiento — si las dos tablas se pisaban en el tiempo, habría eventos duplicados. No se pisaban: la vieja terminaba el 20 de abril, la nueva empezaba el 23.

Resultado: **83 eventos reales** de marzo a mayo, con las tres filas de prueba eliminadas y `restart_history_v0` conservada intacta como respaldo.

### La distribución no era sutil

```sql
SELECT container_name, COUNT(*) FROM restart_history
GROUP BY container_name ORDER BY 2 DESC LIMIT 5;
```

```
beszel              49
frigate              8
stirling-pdf         7
homelab-dashboard    5
audiobookshelf       2
```

**Un solo contenedor acumulaba el 59% de todas las intervenciones automáticas del sistema.**

Y aquí está lo que lo convierte en algo más que una curiosidad: cuarenta y nueve reinicios en siete semanas y **jamás me llegó una alerta**. La lógica de "solo notificar en el cambio de estado" hacía su trabajo: el contenedor caía, el healer lo levantaba, el estado volvía a bien, y el mensaje que llegaba a Telegram era el de recuperación. Un mensaje ocasional diciendo que algo se había arreglado solo no dispara ninguna alarma mental.

El sistema estaba enmascarando un problema crónico con la eficacia suficiente para que nunca escalara.

### Primera hipótesis: flapeo. Descartada.

Mi lectura inicial fue "contenedor inestable que se cae a menudo". Los datos dicen otra cosa. Agrupando los 49 reinicios por día:

```
2026-04-01  ██████ 6
2026-04-02  ███████████████████████████ 27
2026-04-04  ███ 3
2026-04-16  ███ 3
2026-04-18  ███████ 7
2026-05-05  ███ 3
```

No es un goteo constante. Son **seis episodios discretos** separados por semanas de estabilidad total. Entre el 4 y el 16 de abril no pasó absolutamente nada.

Y dentro de cada episodio, la estructura temporal es muy característica. Los intervalos entre reinicios consecutivos: 44 de 48 por debajo de una hora, con una mediana de **12 minutos**. Ampliando el episodio del 18 de abril:

```
05:50:26   05:55:22   06:02:38      ← tres ciclos consecutivos de 5 minutos
06:55:23   07:00:09   07:05:12      ← otros tres, una hora después
07:55:25
```

Ráfagas de tres reinicios en ciclos consecutivos del monitor, separadas por unos cincuenta minutos de calma, y vuelta a empezar. Eso no es un contenedor degradándose poco a poco. Es algo que lo tumba de golpe, un healer que lo levanta, y el ciclo repitiéndose.

### Segunda hipótesis: recursos. También descartada.

La sospecha natural con un contenedor que se cae en bucle es la memoria: una fuga que lo lleva al límite, el kernel lo mata, arranca de nuevo, y vuelta a empezar. Es el patrón más común que existe.

Los agregados horarios lo descartan sin margen de duda:

- Memoria media: **23 MB**. Pico absoluto en cinco semanas: **42 MB**.
- CPU media: **0,05%**. Pico: 0,56%.

En una máquina con 64 GB de memoria unificada. No había fuga, no había presión, no había nada parecido a agotamiento de recursos. Sea lo que sea lo que tumbaba a ese contenedor, no era su propio consumo.

### La evidencia que sí importaba

Al mirar las métricas horarias del 18 de abril alrededor de los reinicios, apareció esto:

```
2026-04-18T05   12 muestras   cpu 0.01   mem  17.7 MB
2026-04-18T06   12 muestras   cpu 0.00   mem   0.0 MB   ← hora entera a cero
2026-04-18T07   12 muestras   cpu 0.00   mem   0.0 MB   ← hora entera a cero
2026-04-18T08   12 muestras   cpu 0.01   mem  23.8 MB
```

Doce muestras por hora es exactamente lo que produce un monitor que corre cada cinco minutos. Así que durante las horas 06 y 07, en **las veinticuatro muestras**, el contenedor consumía cero memoria y cero CPU.

Un contenedor corriendo, por muy ocioso que esté, consume algo. Cero significa que no estaba ejecutándose. Aparecía en el listado —por eso hay muestras— pero estaba parado.

Y en esas mismas dos horas, el healer registró **cuatro reinicios. Los cuatro como `success`.**

### Por qué el registro mentía

La explicación cabe en una línea de código. El healer antiguo hacía esto:

```python
subprocess.check_call(["docker", "restart", name])
registrar(name, exito=True)   # ← si llegamos aquí, el comando no lanzó excepción
```

`docker restart` devuelve código 0 en cuanto el daemon **acepta** la orden. No espera a que el contenedor arranque, no comprueba que siga vivo, no sabe nada de lo que pasa después. Si el contenedor arranca y muere dos segundos más tarde, el comando ya devolvió 0 hace rato.

Así que la métrica no medía "el problema quedó resuelto". Medía **"Docker aceptó mi orden"**. Que es cierto prácticamente siempre.

Un 100% de éxito sobre 83 eventos es exactamente el tipo de número que acaba en una diapositiva y que nadie cuestiona porque es redondo. No estaba midiendo nada.

### Arreglo 1: verificar antes de registrar

```python
def restart_container(name: str, reason: str = "") -> bool:
    """Reinicia y **verifica**. Devuelve True solo si el contenedor
    está realmente corriendo después."""
    try:
        subprocess.check_call(["docker", "restart", name], timeout=30)
    except Exception as e:
        metrics_db.record_restart(name, "failed", f"{reason} | comando: {e}")
        return False

    sleep(VERIFY_DELAY_S)                      # 10 segundos
    running = container_is_running(name)       # docker inspect, no el código de salida

    if running:
        metrics_db.record_restart(name, "success", f"{reason} | verificado")
    else:
        metrics_db.record_restart(
            name, "failed",
            f"{reason} | devolvió 0 pero no está running tras {VERIFY_DELAY_S}s")
    return running
```

La comprobación real es `docker inspect -f {{.State.Running}}`, que pregunta al daemon por el estado actual del contenedor en lugar de por el resultado de una orden pasada.

**Por qué diez segundos.** Es un compromiso. Demasiado corto y pillas al contenedor en pleno arranque, marcándolo como fallido cuando iba a levantar bien. Demasiado largo y alargas cada ciclo del monitor sin ganar nada. Diez segundos cubre el arranque de todo lo que corre en esta máquina, y solo se pagan cuando hay un reinicio de por medio — en un ciclo normal, sin caídas, el coste es cero.

**Lo que sigue sin garantizar.** Un contenedor puede estar en pie a los diez segundos y morir al minuto. Esta verificación no lo detecta; lo detectará el siguiente ciclo, cinco minutos después. La diferencia es que ahora es una afirmación sobre el estado del sistema en lugar de una afirmación sobre un subproceso, y eso ya se puede contar.

El mensaje de Telegram también cambió, porque el anterior heredaba la misma mentira: donde decía "✅ Reiniciado automáticamente" ahora dice **"✅ Reiniciado y verificado"** o **"❌ Reinicio sin efecto — sigue caído"**.

### Arreglo 2: un cortacircuitos

La verificación te dice que un reinicio no funcionó. No te impide intentarlo cuarenta y nueve veces.

Un contenedor en bucle de caída no se arregla reiniciándolo; el reinicio es justo lo que impide que el problema se vuelva visible. Hace falta que en algún momento el sistema **deje de intentarlo y llame a un humano**.

```python
def breaker_decision(attempts: int, max_attempts: int = CB_MAX_ATTEMPTS):
    """Función pura, para poder testearla sin Docker."""
    if attempts >= max_attempts:
        return False, (f"cortacircuitos abierto: {attempts} intentos en las últimas "
                       f"{CB_WINDOW_HOURS} h (máximo {max_attempts})")
    return True, f"intento {attempts + 1}/{max_attempts} en {CB_WINDOW_HOURS} h"
```

Que la decisión sea una función pura —recibe un número, devuelve un booleano y un motivo— no es cosmético: permite testear el comportamiento del cortacircuitos sin Docker, sin base de datos y sin esperar seis horas.

Cuando se abre, el monitor deja de reiniciar, registra el evento como `circuit_breaker`, y manda una alerta **distinta de la habitual**, que incluye las últimas líneas de log del contenedor. Ese detalle importa: cuando por fin te avisan de que algo está en bucle, lo primero que vas a querer es precisamente el log, y si no viene en el mensaje tienes que ir al ordenador a buscarlo.

**Los umbrales: 3 intentos en 6 horas.** No son arbitrarios, salen del episodio real. Con esos valores, el 18 de abril habría quedado así:

```
05:50:26  intento 1/3   →  reinicia
05:55:22  intento 2/3   →  reinicia
06:02:38  intento 3/3   →  reinicia
06:55:23  BLOQUEADO     →  🔴 alerta con los logs del contenedor
```

Tres intentos legítimos —porque a veces un reinicio sí resuelve— y al cuarto se planta. Me habría enterado a las 06:55 del 18 de abril, en vez de tres meses después leyendo una base de datos.

**Dos decisiones internas que parecen detalles y no lo son:**

*El recuento excluye los propios eventos `circuit_breaker` y `skipped`.* Solo cuenta intentos reales de reinicio. Si contase también sus propias decisiones de no actuar, cada ciclo añadiría una fila al recuento, el número crecería solo, y el cortacircuitos no se cerraría jamás. Se alimentaría a sí mismo.

*Si falla la lectura de la base de datos, el recuento devuelve cero.* Es decir: ante un problema de almacenamiento, el sistema **sí intenta** el reinicio. La alternativa —fallar hacia el bloqueo— significaría que un disco lleno desactiva silenciosamente toda la recuperación automática, que es un modo de fallo mucho peor que reiniciar de más.

**Cómo se cierra.** Solo con el paso del tiempo: cuando los intentos salen de la ventana de seis horas, vuelve a permitir. No hay reseteo manual, lo cual es una carencia reconocida — forzarlo hoy implica borrar filas del histórico, que es feo. Un comando de reseteo por Telegram sería lo suyo.

!!! warning "La causa raíz sigue sin conocerse"
    `beszel` lleva estable desde el 5 de mayo. Todo esto detecta y contiene la **clase** de fallo; no diagnostica ese contenedor concreto. Lo que sabemos: no era memoria, no era CPU, y venía en episodios separados por semanas. Lo que no sabemos: qué lo tumbaba. Si vuelve a pasar, los logs llegan al móvil en el momento — que es exactamente lo que faltaba para poder investigarlo.

---

## Hallazgo 2 — `healthy` no significaba sano

### Cómo apareció

Este no lo encontré auditando nada. Lo encontré mirando por encima una muestra del agregado horario, un rato después de haberlo generado, y viendo una columna llena de ceros donde esperaba variedad.

Docker tiene un concepto de *health check*: una orden que el contenedor ejecuta periódicamente sobre sí mismo para decir si está sano. Es **opcional**. Un contenedor puede definirla o no, y cuando no la define, el campo `health` que devuelve Docker viene **vacío**.

Al instrumentar las métricas escribí esto:

```python
"health": "healthy" if c["healthy"] else "unhealthy"
```

Donde `c["healthy"]` era simplemente "el texto de estado no contiene la palabra unhealthy". Es decir: todo lo que no estuviera roto explícitamente lo marcaba como sano, **incluidos los contenedores que no tienen forma de saberlo**.

Vacío y sano no son lo mismo. Un contenedor sin health check no está sano: está **sin comprobar**. Confundirlos convierte una métrica de salud en una métrica de "no ha explotado todavía".

### Por qué importaba más de lo que parece

El escritor antiguo, el del componente desmantelado, guardaba el campo real de Docker. El mío guardaba mi interpretación. Las dos mitades de la serie temporal medían cosas distintas con la misma etiqueta.

En la práctica eso significa que cualquier gráfica de salud a lo largo del tiempo mostraría un **escalón brusco el 26 de julio**, el día que introduje mi versión. Alguien mirando esa gráfica dentro de seis meses concluiría que algo cambió en el homelab ese día. No cambió nada en el homelab: cambió mi código.

### El arreglo, y por qué se pudo hacer retroactivo

Por suerte el campo `status` —el texto completo que devuelve Docker, tipo `Up 2 weeks (healthy)`— sí se guardaba íntegro. El valor correcto se podía **derivar** de él para las filas ya escritas:

| Estado de Docker | `health` derivado |
| --- | --- |
| `Up 2 weeks (healthy)` | `healthy` |
| `Up 3 hours (unhealthy)` | `unhealthy` |
| `Up 5 seconds (health: starting)` | `starting` |
| `Up 10 hours` | `` — sin health check definido |
| `Exited (1) 3 minutes ago` | `` — sin health check definido |

Un detalle que casi genera otro bug: `unhealthy` **contiene** `healthy` como subcadena. Si compruebas primero si aparece `healthy`, todos los contenedores enfermos se clasifican como sanos. El orden de las comprobaciones es lo único que impide ese error, y por eso hay un test explícito para ello.

**491 de 779 filas estaban mal: el 63%.**

La migración importa la función de parseo directamente del monitor en lugar de reimplementar la regla, para que no puedan divergir. Si mañana cambio cómo se deriva `health`, el código de corrección cambia con él.

### La comprobación que hace fiable el arreglo

Que una migración se ejecute sin errores no significa nada. Lo que hay que preguntarse es si el resultado es *plausible*, y para eso hace falta contrastarlo con algo que no acabes de calcular tú.

En este caso, con el histórico:

```
histórico (abr-may):     8.446 / 22.556 horas con health check  =  37,4%
hoy, tras el arreglo:      288 /    779 filas                   =  37,0%
```

Dos conjuntos de datos generados con meses de diferencia, por escritores distintos, coincidiendo en medio punto porcentual. Eso es lo que confirma que la derivación es correcta: la proporción de contenedores con health check definido no ha cambiado, y ahora ambas mitades la miden igual.

Sin esa comprobación tendría una migración limpia que produce basura plausible, que es el resultado por defecto de cualquier migración de datos.

---

## Hallazgo 3 — "Relays 5/5"

### El contexto que hace falta

Home Assistant corre en un contenedor y, por cómo funciona la red de contenedores en macOS, **no puede alcanzar la red local**. Todos los dispositivos físicos de la casa están al otro lado de esa frontera.

La solución son **nueve relays** `socat` corriendo de forma nativa en el host: cada uno escucha en un puerto y reenvía a un dispositivo concreto. El contenedor habla con el host y el host habla con el aparato. Si un relay muere, Home Assistant pierde esa integración y muestra el dispositivo como no disponible, sin ninguna pista de por qué.

Nueve puntos de fallo independientes, invisibles desde dentro del contenedor. Merece la pena vigilarlos.

### Lo que decía el informe

El informe diario de las 09:00 incluía una línea: `📡 Relays: ✅ 5/5`.

Cinco de cinco. Verde. Pero hay nueve.

### Lo que descubrí al mirar

Los cinco que contaba **no eran un subconjunto de los nueve**. Tres de ellos eran relays del proxy inverso —cosas de red totalmente ajenas a la domótica— que estaban en el mismo fichero de estado por razones históricas. Solo dos de los nueve relays de dispositivo estaban ahí.

Cobertura real, una vez auditada a fondo:

| Relay | Cobertura | Cómo |
| --- | --- | --- |
| Zigbee, y dos puentes de microcontrolador | **Indirecta** | Otro monitor comprueba cada 15 minutos, vía la API de Home Assistant, que esas integraciones responden |
| Dos relays de dispositivo | **Directa** | Comprobación del propio relay |
| Los cuatro restantes | **Ninguna** | — |

Hay un matiz que va a favor del sistema: la cobertura indirecta es **mejor** que comprobar el relay. Verificar que el relay existe demuestra que hay un proceso escuchando; verificar a través de la API de Home Assistant demuestra que la integración de verdad funciona de extremo a extremo. Es la comprobación correcta.

Pero cuatro relays no tenían cobertura de ningún tipo. Si alguno lleva semanas caído, no hay forma de saberlo.

### El problema de fondo

El denominador era **"las cosas que resulta que miramos"**, presentado como **"las cosas que importan"**. Es lo mismo que pasaba con la tasa de éxito de la recuperación, expresado en otro formato: un cociente cuya parte de abajo se fue definiendo sola, por acumulación, y cuya etiqueta describe otra cosa.

Y es un patrón especialmente traicionero porque un ratio `5/5` se lee como completitud. Nadie mira un `5/5` y se pregunta "cinco de qué".

---

## El pipeline que hay debajo

Hasta aquí los hallazgos. Esta parte es el sistema que se construyó para que la próxima vez los datos estén ahí.

### A prueba de fallos por contrato

Todas las funciones de la capa de persistencia capturan sus propias excepciones y devuelven un valor neutro:

```python
def record_containers(rows: list[dict], timestamp: str | None = None) -> int:
    ...
    except Exception as e:
        _log(f"record_containers falló: {e}")
        return 0
```

Es una inversión deliberada de la buena práctica, y merece defenderse porque parece un error.

En código de aplicación, tragarse las excepciones esconde bugs y es de las peores cosas que puedes hacer. En un monitor la ecuación se invierte: una excepción no capturada mata el proceso, `launchd` registra el fallo, y **dejas de enterarte de todo lo demás** hasta el siguiente ciclo o hasta que alguien lo mire. Perder una muestra de métricas es un daño trivial. Perder la monitorización entera porque falló una escritura a disco es un daño grande.

El contrato está verificado en el propio selftest: hay una aserción que apunta la base de datos a una ruta donde no se puede escribir y comprueba que la función devuelve 0 en lugar de lanzar.

### El problema de volumen

41 contenedores × 12 muestras/hora × 24 horas ≈ **11.800 filas al día**. Unos 4,3 millones al año. A los ~230 bytes por fila que ocupa esta tabla, eso son **casi 900 MB anuales** solo de métricas de contenedores.

Es perfectamente asumible en disco. No lo es en usabilidad: las consultas para cualquier gráfica se vuelven lentas, y los backups nocturnos cargan con datos de granularidad absurda para su edad. A nadie le importa el consumo de CPU minuto a minuto de hace ocho meses.

### Por qué la solución obvia era peligrosa

Lo natural es una purga: borrar todo lo anterior a N días. Con una retención de 90 días, ejecutándola el 26 de julio, el corte cae el 27 de abril.

Y el histórico rescatado esa misma mañana iba **del 17 de abril al 17 de mayo**. La purga se habría llevado los primeros diez días de golpe, y en septiembre el bloque entero. Habría destruido, ese mismo día, los datos que acababan de permitir todos los hallazgos de esta página.

### Retención en dos niveles

Así que la política **agrega antes de borrar**:

1. El detalle a cinco minutos se conserva **30 días**.
2. Lo más antiguo se comprime a **medias horarias por contenedor** —número de muestras, CPU media y máxima, memoria media y máxima, ratio de salud— y a medias diarias por disco.
3. Los agregados se guardan **para siempre**.
4. Se compacta el fichero con `VACUUM`.

Se pierde la resolución fina del pasado y se conserva la forma de la serie, que es lo que se usa a partir de cierta antigüedad.

**Primera propiedad de seguridad: el corte se trunca a la hora.**

```python
def _cutoff_hour(keep_days: int) -> str:
    """Truncar es lo que hace segura la política: rollup y prune usan
    exactamente el mismo corte, así que una hora entra entera o no entra."""
    return datetime.fromtimestamp(time() - keep_days * 86400).strftime("%Y-%m-%dT%H")
```

Sin truncar, el corte caería a mitad de una hora: la agregación resumiría media hora, la purga borraría el detalle, y esa media hora quedaría mal contada para siempre. Con el corte truncado y compartido por ambas operaciones, una hora entra completa en el agregado o no entra en absoluto.

**Segunda propiedad: el borrado solo toca lo ya agregado.**

```sql
DELETE FROM container_metrics
WHERE substr(timestamp, 1, 13) < ?
  AND substr(timestamp, 1, 13) IN (SELECT hour FROM container_metrics_hourly)
```

La segunda condición es la red de seguridad. Si la agregación falla por lo que sea, esas horas no existen en la tabla de agregados y la purga no borra nada. **El sistema falla hacia conservar datos.**

Y está en el SQL a propósito, no en el código que llama. Una comprobación en el llamante se puede saltar invocando las funciones en el orden equivocado; una condición dentro de la consulta, no.

**Resultado de la primera pasada real:**

```
265.365 filas de detalle  →  22.556 filas horarias
74,6 MB                   →  2,9 MB
serie abril-mayo          →  intacta, a resolución horaria
```

Un 96% de reducción conservando la forma completa de la serie. La compresión es tan grande porque doce muestras por hora y contenedor colapsan en una sola fila.

---

## Latidos, y por qué la monitorización por descubrimiento es ciega

### El agujero

La retención corre una vez por semana, los domingos a las 06:00. Y aquí aparece un problema nuevo: **¿cómo sé que se ha ejecutado?**

El sistema ya vigilaba las tareas programadas, pero lo hacía **por descubrimiento**: pregunta al planificador qué tareas tiene cargadas e informa sobre ellas. Responde bien a "¿está esta tarea cargada, y su última ejecución fue bien?".

No puede responder a "**¿falta alguna tarea?**". Si un `plist` no llega a cargarse, o alguien lo descarga, o falla el registro tras un reinicio, la tarea desaparece del recuento. El informe pasa de `21/21` a `20/20` y **sigue en verde**, porque el porcentaje es el mismo.

Para una tarea semanal es el peor escenario posible: podría no ejecutarse durante un mes sin que nada chille.

### La inversión

Un latido hace la pregunta al revés. En lugar de "¿está la tarea cargada?", pregunta "**¿se hizo el trabajo, y hace cuánto?**".

Cada tarea escribe un fichero JSON al terminar, con su estado y un detalle. Un manifiesto declara qué tareas se esperan y qué antigüedad máxima se tolera en cada una. El informe diario compara y detecta tres cosas distintas:

- **Ausente** — la tarea nunca ha latido
- **Rancio** — el último latido supera el máximo tolerado
- **Error** — la tarea terminó con código distinto de cero

Cubre además el caso que el descubrimiento tampoco ve: una tarea cargada que falla en silencio.

### No elegir entre local y externo

Esto duplica en parte lo que ya hace un monitor de tipo *push* en Uptime Kuma, que es la herramienta canónica para vigilar tareas programadas. En lugar de elegir una, el latido hace las dos cosas en la misma llamada, porque **fallan de forma distinta**:

| | Push a Uptime Kuma | Latido local |
| --- | --- | --- |
| Alerta inmediata | sí, con canales de notificación | no — hasta el informe de las 09:00 |
| Historial y gráficas | sí | no |
| Observador externo | **sí, otra máquina** | no |
| Funciona sin red ni DNS | no | sí |
| Detalle del fallo | truncado a 200 caracteres | JSON completo |
| Dar de alta una tarea | crear un monitor en la interfaz | una línea en un fichero |

El reparto tiene un motivo concreto. Kuma corre en otra máquina, y eso es lo correcto: un observador externo puede detectar que el servidor principal está muerto, cosa que ningún monitor local puede hacer jamás. Pero plantea la pregunta inversa — **¿quién vigila a Kuma?** Si esa máquina cae, dejas de recibir alertas y el silencio se parece a la normalidad. El latido local cubre parte de ese hueco: el informe diario del servidor principal seguiría llegando aunque Kuma estuviera muerto.

### Tres decisiones, todas por haberlo hecho mal antes

**Kuma se direcciona por IP, no por nombre.** El latido no puede depender del DNS interno, porque el DNS interno es a su vez uno de los servicios monitorizados. Si cae, el aviso de que ha caído tiene que poder salir igualmente.

**Un push fallido no invalida el latido.** La función devuelve el estado de la escritura local únicamente. Si Kuma está caído se registra en `stderr` y punto: el informe diario se apoya en el fichero, no en la red. Hay un test que verifica exactamente esto, apuntando a un puerto cerrado y comprobando que el fichero local se escribe igual.

**El manifiesto se autocompleta.** Y esta es la buena.

La primera versión del cargador devolvía el fichero de configuración tal cual estaba. Cuando después añadí la opción de hacer push a Kuma, un manifiesto **ya creado** no tenía esa clave — y por tanto la ignoraba en silencio. El push no se enviaba nunca.

Lo que lo hace ilustrativo es que **las dos comprobaciones que hice daban verde**: el comando de prueba de push funcionaba, porque salta el manifiesto y va directo; y el comando de escritura del latido decía "registrado", porque el fichero local sí se escribía. Todo correcto, y nada llegaba a Kuma.

Lo pillé porque a una línea de salida le faltaba un sufijo de tres palabras.

Ahora el cargador fusiona los valores por defecto en las claves ausentes y reescribe el fichero. Y los mensajes de estado dicen explícitamente lo que **no** hicieron, en lugar de omitirlo. Es el hallazgo número cuatro del día, encontrado igual que los otros tres.

---

## Tests

86 aserciones en tres módulos. Ninguna necesita Docker, red, ni la base de datos real: todas corren contra bases de datos temporales que se destruyen al terminar.

| Suite | Cubre |
| --- | --- |
| `metrics_db` | escrituras, agregación, retención, la red de seguridad de la purga, no lanzar nunca |
| `docker_monitor` | parseo de estado, parseo de stats, derivación de health, decisión del cortacircuitos |
| `heartbeat` | fusión del manifiesto, latidos rancios, fallo de push, configuración corrupta |

**Por qué las funciones de parseo están extraídas.** El monitor originalmente parseaba la salida de `docker ps` dentro de la misma función que la ejecutaba, lo que hace imposible testearlo sin Docker. Separar `get_containers()` (efecto) de `parse_ps()` (función pura) permite probar el parseo contra entradas que en la práctica no consigues reproducir: `--` y `n/a` donde debería haber números, columnas ausentes, líneas truncadas, CPU por encima del 100% —que es normal en multinúcleo—, memoria en GiB frente a MiB, y `unhealthy` conteniendo `healthy`.

**Los tests que más valen son los que se escribieron después de cada error.** Hay uno que afirma que un manifiesto de una versión anterior hereda las claves nuevas. Otro que afirma que un push fallido sigue escribiendo el latido local. Otro que afirma que la purga no borra nada cuando falta el agregado. Ninguno de los tres se me habría ocurrido antes de meter la pata.

---

## Lo que costó de verdad, y lo que sigue abierto

Todo esto fue una tarde. Y la mayor parte no fue escribir código: fue leer datos que llevaban meses ahí sin que nadie los mirase.

Lo que quedó abierto aquella tarde, y cómo está hoy:

| Abierto entonces | Hoy |
| --- | --- |
| La causa raíz del contenedor que consumió el 59% de las recuperaciones | Sigue sin conocerse. No ha vuelto a pasar, y si pasa, la evidencia se congela en el momento para poder investigarla después |
| Cuatro relays sin cobertura de ningún tipo | Los 25 relays actuales se comprueban todos, cada pocos minutos, y cualquier fallo llega a la central de alarmas |
| Los latidos cubrían una tarea de nueve | 36 tareas con latido, todas declaradas en el manifiesto; un latido rancio es una alarma |
| Ninguna comprobación de consistencia sobre las propias métricas | Hay un comprobador de coherencia y un inventario de cobertura (abajo) |

Aquella tarde terminó con una frase incómoda: **todos los problemas de esta página los encontró una persona notando algo raro.** El escalón en la salud lo vi de refilón mirando una muestra. El push que no se enviaba lo pillé por tres palabras que faltaban en una línea. Ninguno lo cazó un sistema diseñado para cazarlo. La sección siguiente es lo que se construyó para cambiar eso.

---

## Tres cosas que le diría a mi yo de marzo

**Investiga antes de comprimir.** El detalle a cinco minutos de abril se agregó por la mañana; el análisis de los 49 reinicios se hizo esa misma tarde. Las medias horarias bastaron para llegar a una conclusión, pero la secuencia minuto a minuto —que habría dicho exactamente cuánto aguantaba el contenedor entre caídas— se perdió de forma permanente. Las políticas de retención son irreversibles de una forma en que casi ninguna otra operación lo es.

**Un dato en verde no es un dato correcto.** Los tres indicadores llevaban meses en el informe diario, a la vista, todas las mañanas. Ninguno estaba roto. Cada uno medía algo real. Ninguno medía lo que su etiqueta prometía. Y precisamente porque estaban en verde, nadie los miró dos veces: el verde no es un estado que nadie investigue.

**La monitorización por descubrimiento no ve las ausencias.** Es cómoda porque no hay lista que mantener y se adapta sola cuando añades componentes. Esa misma comodidad es lo que convierte la desaparición de un componente en silencio, y el silencio en un informe que dice `20/20`.

---

## Después de aquella tarde

Los tres hallazgos tenían la misma forma: un indicador que nadie cuestionaba porque estaba en verde. Lo que vino después fueron cuatro piezas, cada una pensada para encontrar esa forma sin que haga falta tener suerte.

### Una central de alarmas, en vez de un chat

Los avisos de Telegram son un historial: dicen lo que **pasó**. Lo que hacía falta era un sitio que dijera lo que **está mal ahora**. Todos los monitores escriben su estado, y una central de alarmas en el panel de vigilancia los reúne: contenedores, latidos, relays, domótica, discos, servicios del sistema, tareas del agente, coherencia, seguridad. Una alarma desaparece cuando el problema desaparece, no cuando alguien lee el mensaje.

Es la diferencia entre el espejo degradado que avisó una vez y siguió once días así (está en [Decisiones y errores](../decisions.md)) y un sistema donde ese estado se queda en rojo hasta que se arregla.

### Remediación con evidencia, y un Aprendiz

El healer de reinicios verificados creció en dos direcciones.

**Hacia más tipos de avería.** La remediación tiene ahora una lista cerrada de acciones reversibles —reiniciar un contenedor, reiniciar un servicio del sistema, rotar un log que crece sin control— y para cada episodio congela la evidencia (estado, logs, eventos de Docker, historial de reinicios) y pregunta a un modelo una sola cosa: **¿alguna de estas acciones lo resuelve?** El modelo no decide qué puede hacer; solo si algo de la lista aplica, y por qué. Si dice que no, el motivo queda escrito: «el contenedor no arrancó porque Docker no pudo acceder al disco del montaje; reiniciarlo no lo resolvería» es un diagnóstico, no un fallo de la remediación.

**Hacia lo que no está en la lista.** El Aprendiz vigila la central de alarmas. La primera vez que aparece una avería guarda las pruebas al aparecer y al resolverse, y anota cómo se arregló; con eso propone una **receta**, que confirma una persona. La vez siguiente, con la receta confirmada, despierta al agente para que la aplique, comprueba si la alarma desaparece, y respeta un cortacircuitos de tres intentos al día.

El cortacircuitos tuvo su propia lección: durante las pruebas de antifragilidad se abría con reparaciones *correctas*, porque contaba igual un arreglo que funcionó que uno que no. Ahora lo que se repara durante un experimento no cuenta, y el mensaje dice «se ha caído N veces» en vez de «ha fallado».

### Coherencia e inventario

**Coherencia** cruza cada hora las listas que deberían cuadrar entre sí: las rutas del proxy inverso, los portales de la página de inicio, lo que vigila el portal de seguridad, los latidos esperados, los servicios del sistema cargados, los contenedores. Un portal nuevo sin ruta, un latido declarado de una tarea que ya no existe, un destino del proxy donde no escucha nadie: cada uno es un aviso. Las excepciones a propósito se escriben en un fichero, con su porqué.

**El inventario de cobertura** hace a cada componente —contenedores, servicios, entidades de la domótica, tareas, relays— tres preguntas: ¿tiene un **estado esperado declarado**?, ¿lo **vigila** alguien?, ¿un fallo **llegaría al panel**? Lo que no responde a las tres es un hueco aunque esté en verde. Es la versión sistemática de la pregunta que destapó los relays: *cinco de qué*.

### Antifragilidad: romperlo a propósito

Todo lo anterior responde a «¿se vería este fallo?». No responde a «¿qué pasa de verdad cuando ocurre?». Para eso hay un programa de **averías provocadas**: un catálogo de algo más de cien experimentos en cinco niveles, de matar un contenedor a cortar Internet una hora o apagar el Mac de golpe, que se provocan **a mano, uno a uno** —nunca un orquestador automático—, con la familia avisada cuando afectan a sus servicios.

Cada ejecución mide cuánto se tardó en **detectar**, cuánto en **reparar**, y **quién** reparó: Docker, el sistema operativo, la remediación, el Aprendiz con una receta, el guion de arranque, nadie porque el sistema aguantó, o una persona. Una reparación humana puntúa poco aunque sea rápida: el objetivo es que no haga falta.

En los primeros seis días hubo **154 ejecuciones sobre 101 experimentos**, y quedaron **56 aprendizajes** escritos. Unos cuantos que dicen más que el índice:

| Experimento | Lo que se esperaba | Lo que pasó |
| --- | --- | --- |
| Matar un contenedor con `docker kill` | Docker lo relanza | **Docker no relanza tras un kill a mano**: lo trata como parada voluntaria. Hicieron falta cuatro intentos —y ajustar márgenes, frecuencia y el cortacircuitos— para que se reparara solo en dos minutos |
| Congelar un proceso con `kill -STOP` | Algo lo detecta | **Nadie lo vio.** Vivo para el sistema, sin latido, invisible. Hubo que añadir latidos a dos procesos que no los tenían |
| Parar el panel de vigilancia | Se avisa y se repara | Hallazgo grave: la remediación llevaba **dos meses excluyendo** a tres contenedores, el panel incluido. Una vez, el modelo devolvió una respuesta mal formada; ese intento quedó «sin evaluar», y la regla que evita evaluar dos veces lo mismo no miraba la fecha: nunca caducaba. Y el Aprendiz, que lee la central, se quedaba ciego sin avisar |
| Cortar Internet una hora | Lo local sigue funcionando | Lo local, perfecto. Pero las integraciones en la nube de la domótica cayeron y, al volver la conexión, un monitor mandó trece avisos de golpe de cosas que se arreglaron solas |
| Apagar el Mac de golpe | Arranca y se recupera solo | **Cuatro intentos.** El primero tardó 19 minutos por una alarma vieja; el segundo encontró que el apagón dejaba bloqueada una aplicación; el tercero, que el disco de datos montaba tarde y una docena de servicios fallaban al arrancar sin él; el cuarto, todo en verde en algo más de tres minutos sin que nadie hiciera nada. Por el camino apareció que el observador externo no podía avisar durante el apagón |

La última fila es el resumen de todo el programa: **cada experimento que sale mal es un arreglo que no habría existido**, porque ninguno de esos fallos se ve leyendo configuraciones. Solo aparecen rompiendo cosas.

### La lección de fondo, otra vez

La tarde de los tres hallazgos terminó diciendo que un dato en verde no es un dato correcto. Las averías provocadas añadieron la segunda mitad: **un sistema que se recupera en las pruebas que imaginaste no es un sistema que se recupera**. La única forma de saber si se recupera es hacerlo caer, de la forma más parecida posible a como caerá de verdad, y mirar qué llega, a quién y cuándo.

