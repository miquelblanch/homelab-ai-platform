# Fiabilidad

> La distancia entre un sistema que reporta verde y un sistema que funciona.

Esta página es la versión larga de la tabla de la portada. Tres indicadores llevaban meses en verde. Ninguno estaba roto. Ninguno medía lo que su etiqueta prometía.

Los encontré los tres en una sola tarde, y solo porque me había puesto a hacer otra cosa: empezar a registrar métricas para tener números que contar.

---

## El punto de partida

La monitorización funcionaba. Cada cinco minutos un script revisaba 41 contenedores y 3 discos, reiniciaba los no críticos que se habían caído, escalaba los críticos a Telegram y seguía. Llevaba meses haciéndolo sin fallar.

También **lo tiraba todo**. La alerta iba a Telegram y no se guardaba nada. Hubo una base de datos de métricas en su momento, escrita por un componente que después se desmanteló; la tabla seguía ahí, con 265.365 filas y sin una sola escritura en 70 días.

Así que la primera tarea parecía simple: darle memoria al monitor que ya funcionaba.

```
grep -r "container_metrics" .
# (sin resultados)
```

El escritor no existía. Nunca se había roto — lo borraron junto con el componente que lo mantenía, y nadie se enteró porque nada dependía de esos datos.

Esa es la primera lección del día, y la más barata: **los datos que nadie lee dejan de producirse y nadie se entera.**

---

## Hallazgo 1 — un 100% de éxito que medía códigos de salida

Con las métricas registrándose otra vez, la tabla histórica de remediación pasó a merecer una mirada. Dos tablas, en realidad: el esquema había cambiado meses antes y el histórico estaba partido. Tras consolidarlas: **83 eventos reales de remediación** entre marzo y mayo.

La distribución no era sutil.

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

**Un solo contenedor acumulaba el 59% de todas las intervenciones automáticas del sistema.** Cuarenta y nueve reinicios en siete semanas, y jamás me llegó una alerta — porque el healer funcionaba. Cada reinicio se registraba como `success`, así que nunca escalaba nada.

### No era flapeo. Eran seis episodios.

```
2026-04-01  ██████ 6
2026-04-02  ███████████████████████████ 27
2026-04-04  ███ 3
2026-04-16  ███ 3
2026-04-18  ███████ 7
2026-05-05  ███ 3
```

Dentro de cada episodio las marcas de tiempo se agrupan muy juntas — 44 de 48 intervalos por debajo de una hora, mediana de 12 minutos:

```
05:50:26   05:55:22   06:02:38      ← tres ciclos consecutivos de 5 minutos
06:55:23   07:00:09   07:05:12      ← otros tres, una hora después
07:55:25
```

Eso no es un contenedor degradándose. Es algo que lo tumba, y un healer reiniciándolo en cada ciclo.

El agotamiento de recursos era la hipótesis obvia, y los datos la mataron: memoria media 23 MB, pico 42 MB, CPU media 0,05%. Nada pasaba hambre.

### La evidencia que importaba

Los agregados horarios del 18 de abril:

```
2026-04-18T05   12 muestras   cpu 0.01   mem  17.7 MB
2026-04-18T06   12 muestras   cpu 0.00   mem   0.0 MB   ← hora entera a cero
2026-04-18T07   12 muestras   cpu 0.00   mem   0.0 MB   ← hora entera a cero
2026-04-18T08   12 muestras   cpu 0.01   mem  23.8 MB
```

Durante dos horas completas, las doce muestras de cada hora dan cero memoria y cero CPU. El contenedor estaba listado pero no ejecutándose.

En esas mismas dos horas el healer registró **cuatro reinicios, los cuatro como `success`**.

La explicación cabe en una línea de código. El healer antiguo registraba éxito cuando `docker restart` devolvía 0 — cosa que ocurre siempre que el daemon acepta el comando, independientemente de que el contenedor se mantenga luego en pie. **La métrica medía el código de salida, no el resultado.**

Un 100% de éxito sobre 83 eventos es exactamente el número que acaba en una diapositiva. No estaba midiendo nada.

### El arreglo: verificar y luego registrar

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

`success` significa ahora *observado corriendo diez segundos después*. Sigue sin ser una garantía —un contenedor puede levantarse y morir un minuto más tarde— pero es una afirmación sobre el sistema en vez de una afirmación sobre un subproceso.

### El arreglo: un cortacircuitos

La verificación te dice que un reinicio falló. No te impide hacerlo cuarenta y nueve veces.

```python
def breaker_decision(attempts: int, max_attempts: int = CB_MAX_ATTEMPTS):
    """Función pura, para poder testearla sin Docker."""
    if attempts >= max_attempts:
        return False, (f"cortacircuitos abierto: {attempts} intentos en las últimas "
                       f"{CB_WINDOW_HOURS} h (máximo {max_attempts})")
    return True, f"intento {attempts + 1}/{max_attempts} en {CB_WINDOW_HOURS} h"
```

Tres intentos en seis horas y el monitor deja de reiniciar, registra el evento como `circuit_breaker` y manda una alerta *distinta* — una que incluye las últimas líneas de log del contenedor.

Dos decisiones internas merecen enunciarse:

**El recuento de intentos excluye los eventos `circuit_breaker` y `skipped`.** Contarlos dejaría que el cortacircuitos se alimentase a sí mismo y no se abriera nunca.

**Si falla la lectura de la base de datos, el recuento es cero.** Ante la duda, intentar el reinicio. Un problema de almacenamiento no puede desactivar la remediación en silencio.

Calibrado contra el episodio real, los umbrales lo cortan aquí:

```
05:50:26  intento 1/3   →  reinicia
05:55:22  intento 2/3   →  reinicia
06:02:38  intento 3/3   →  reinicia
06:55:23  BLOQUEADO     →  alerta con los logs del contenedor
```

Me habría enterado a las 06:55 del 18 de abril, en vez de tres meses después leyendo una base de datos.

!!! warning "La causa raíz sigue sin conocerse"
    `beszel` lleva estable desde el 5 de mayo. Este trabajo detecta y contiene la clase de fallo; no diagnostica ese contenedor en concreto. Si vuelve a pasar, los logs llegan al móvil — que es justo lo que faltaba.

---

## Hallazgo 2 — `healthy` no significaba sano

El escritor nuevo registraba `health='healthy'` para cualquier contenedor que no reportase `unhealthy` explícitamente. El escritor antiguo guardaba el campo real de Docker, que está **vacío** cuando el contenedor no define ningún `HEALTHCHECK`.

Vacío y sano no son lo mismo. Un contenedor sin healthcheck no está sano: está sin comprobar.

La consecuencia fue un escalón en la serie temporal el día que lo introduje — un artefacto de mi propio código, no del sistema. Como el `status` se guardaba íntegro, el valor correcto se podía derivar retroactivamente:

| Estado de Docker | `health` |
| --- | --- |
| `Up 2 weeks (healthy)` | `healthy` |
| `Up 3 hours (unhealthy)` | `unhealthy` |
| `Up 5 seconds (health: starting)` | `starting` |
| `Up 10 hours` | `` — sin healthcheck definido |

**491 de 779 filas estaban mal: el 63%.**

La migración importa la función de parseo del monitor en lugar de reimplementarla, para que la regla no pueda divergir entre las dos.

Lo que hace fiable el arreglo no es que se ejecutara, sino que las proporciones cuadran a ambos lados de la frontera:

```
histórico (abr-may):     8.446 / 22.556 horas con healthcheck  =  37,4%
hoy, tras el arreglo:      288 /    779 filas                  =  37,0%
```

Dos conjuntos de datos producidos de forma independiente coincidiendo en medio punto porcentual. Sin esa comprobación tendría una migración que corrió limpia y produjo basura plausible.

---

## Hallazgo 3 — "Relays 5/5"

El informe diario contenía una línea que decía `Relays: 5/5`. Hay nueve relays. Y los cinco que contaba no eran un subconjunto de los nueve: tres de ellos eran relays del proxy inverso, sin relación con las integraciones de hardware.

Cobertura real, una vez auditada:

| Relay | Cobertura |
| --- | --- |
| Zigbee y dos puentes de microcontrolador | Indirecta — otro monitor verifica el *efecto* a través de la API de Home Assistant |
| Dos relays de dispositivo | Directa |
| **Los cuatro restantes** | **Ninguna** |

La cobertura indirecta es discutiblemente mejor que comprobar el relay: verifica que la integración funciona, no que exista un proceso. Pero cuatro relays no tenían cobertura de ningún tipo, y la línea del informe se leía como una garantía.

El denominador era "las cosas que resulta que miramos", presentado como "las cosas que importan".

---

## El pipeline que hay debajo

### A prueba de fallos por contrato

Todas las funciones de la capa de persistencia capturan sus propias excepciones y devuelven un valor neutro. Un monitor que corre cada cinco minutos no puede morirse porque falle una escritura a disco.

```python
def record_containers(rows: list[dict], timestamp: str | None = None) -> int:
    ...
    except Exception as e:
        _log(f"record_containers falló: {e}")
        return 0
```

Es una inversión deliberada de la buena práctica. En código de aplicación, tragarse las excepciones esconde bugs. En un monitor, una excepción no capturada significa **dejar de enterarte de todo lo demás**, que es estrictamente peor que perder una muestra.

### Retención que no destruye la serie

A 41 contenedores cada cinco minutos, esto produce unas 11.000 filas al día — cerca de 900 MB al año.

El arreglo ingenuo, borrar lo viejo, habría destruido el histórico de abril y mayo la misma mañana en que se recuperó. Así que la política agrega antes de borrar:

- Resolución completa durante 30 días
- Lo anterior se colapsa en medias horarias por contenedor y diarias por disco
- Los agregados se guardan indefinidamente

Dos propiedades lo hacen seguro.

**El corte se trunca a la hora.** Agregación y borrado usan exactamente la misma frontera, así que una hora entra completa en el agregado o no entra. Nunca se borra detalle a medio resumir.

```python
def _cutoff_hour(keep_days: int) -> str:
    """Truncar es lo que hace segura la política: rollup y prune usan
    exactamente el mismo corte, así que una hora entra entera o no entra."""
    return datetime.fromtimestamp(time() - keep_days * 86400).strftime("%Y-%m-%dT%H")
```

**El borrado solo toca lo que ya está agregado.**

```sql
DELETE FROM container_metrics
WHERE substr(timestamp, 1, 13) < ?
  AND substr(timestamp, 1, 13) IN (SELECT hour FROM container_metrics_hourly)
```

Si la agregación falla, la purga no borra nada. La red de seguridad está en la consulta, no en el llamante.

Primera pasada:

```
265.365 filas de detalle  →  22.556 filas horarias
74,6 MB                   →  2,9 MB
serie abril-mayo          →  intacta, a resolución horaria
```

Un 96% de reducción conservando la forma de la serie.

---

## Latidos, y por qué la monitorización por descubrimiento es ciega

El monitor de tareas programadas funciona **por descubrimiento**: lista las tareas cargadas e informa sobre ellas. Responde a "¿está esta tarea cargada y su última ejecución fue bien?".

No puede responder a "¿falta alguna tarea?". Si una nunca se carga, o se descarga, o falla al registrarse tras un reinicio, desaparece del recuento y el informe pasa de `21/21` a `20/20`. Sigue en verde. Para una tarea semanal, eso puede pasar desapercibido un mes.

Los latidos invierten la pregunta. Cada tarea escribe un registro al terminar; un manifiesto declara qué tareas se esperan y cuánta antigüedad se tolera en cada una. El informe diario compara.

Esto duplica lo que ya hace un monitor de tipo push en Uptime Kuma y, en vez de elegir uno, el latido escribe en local **y** hace push a Kuma. Fallan de forma distinta:

| | Push a Uptime Kuma | Latido local |
| --- | --- | --- |
| Alerta inmediata | sí | no — hasta el informe diario |
| Historial y gráficas | sí | no |
| Observador externo | **sí, otra máquina** | no |
| Funciona sin red ni DNS | no | sí |
| Detalle del fallo | truncado | JSON completo |

Kuma corre en otra máquina, que es la única forma de detectar que esta máquina está muerta. El fichero local es lo único que sobrevive a que Kuma esté muerto. Ninguno sustituye al otro.

Tres notas de diseño, cada una fruto de haberlo hecho mal primero:

**Kuma se direcciona por IP, no por nombre.** El latido no puede depender del DNS interno, que es a su vez uno de los servicios monitorizados.

**Un push fallido no invalida el latido.** La escritura devuelve el estado del fichero local únicamente; el informe diario depende del fichero, no de la red.

**El manifiesto se autocompleta.** La primera versión devolvía el fichero de configuración tal cual, así que un manifiesto ya existente ignoraba en silencio las opciones añadidas después — que es exactamente lo que le pasó a la opción de push el día que se introdujo. El push parecía configurado, los dos comandos de verificación daban éxito, y no se enviaba nada. Ahora fusiona los valores por defecto en las claves ausentes y reescribe el fichero.

Ese es el hallazgo número cuatro, encontrado igual que los otros: por una palabra que faltaba en una línea de salida.

---

## Tests

86 aserciones en tres módulos, ninguna de las cuales necesita Docker, red, ni la base de datos real.

| Suite | Cubre |
| --- | --- |
| `metrics_db` | escrituras, agregación, retención, la red de seguridad, no lanzar nunca |
| `docker_monitor` | parseo de estado, parseo de stats, derivación de health, decisión del cortacircuitos |
| `heartbeat` | fusión del manifiesto, latidos rancios, fallo de push, configuración corrupta |

Las funciones de parseo se extrajeron del monitor precisamente para poder probarlas contra entradas degeneradas: `--`, `n/a`, columnas ausentes, `unhealthy` conteniendo `healthy` como subcadena, CPU por encima del 100%.

Los tests de regresión que más valen son los que se escribieron después de cada error de arriba. Hay uno que afirma que un manifiesto de una versión anterior hereda las claves nuevas, y otro que afirma que un push fallido sigue escribiendo el latido local.

---

## Lo que costó de verdad, y lo que sigue abierto

Todo esto fue una tarde, y la mayor parte no fue escribir código. Fue leer datos que llevaban meses ahí.

Sigue abierto:

- La causa raíz del contenedor que consumió el 59% de las remediaciones
- Cuatro relays sin cobertura de monitorización
- Los latidos cubren una tarea; el resto del trabajo programado sigue dependiendo del descubrimiento
- No hay ninguna comprobación de consistencia sobre la propia serie de métricas. Los cuatro hallazgos se detectaron a ojo

Ese último es el incómodo. **Todos los problemas de esta página los encontró una persona notando algo raro, no un sistema diseñado para notarlo.** Construir esa comprobación es el siguiente paso obvio, y no está hecho.

---

## Tres cosas que le diría a mi yo de marzo

**Investiga antes de comprimir.** El detalle a cinco minutos de abril se agregó por la mañana; el análisis de los 49 reinicios se hizo esa tarde. Las medias horarias bastaron para concluir, pero la secuencia minuto a minuto se perdió para siempre.

**Un dato en verde no es un dato correcto.** Los tres indicadores llevaban meses en el informe diario. Ninguno estaba roto. Cada uno medía algo real. Ninguno medía lo que su etiqueta prometía.

**La monitorización por descubrimiento no ve las ausencias.** Es cómoda porque no hay lista que mantener, y esa comodidad es justo lo que convierte la desaparición de un componente en silencio.
