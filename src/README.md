# Código operativo

Una copia saneada del código que se comenta en [Fiabilidad](../docs/case-studies/reliability.md). Es el real, no una demo — los mismos módulos que corren cada cinco minutos en el host — con las rutas absolutas sustituidas por variables de entorno y valores por defecto que resuelven dentro de este repositorio.

**Solo biblioteca estándar.** Sin dependencias, sin virtualenv. Esto corre bajo un planificador (`launchd`), y cada dependencia es una cosa más que puede faltar a las 3 de la mañana.

## Ficheros

| Fichero | Qué hace |
| --- | --- |
| `docker_monitor.py` | El monitor. Revisa contenedores y discos cada cinco minutos, registra métricas, reinicia los no críticos y **verifica el reinicio**, escala los críticos, y abre un cortacircuitos ante fallos repetidos. |
| `metrics_db.py` | Persistencia. Escrituras, agregación, retención con compresión, y la red de seguridad que impide borrar datos sin agregar. |
| `heartbeat.py` | Señal de vida de las tareas programadas. Escribe un registro local y opcionalmente hace push a un monitor de uptime externo. |
| `homelab_secrets.py` | Carga de credenciales desde el entorno o desde ficheros fuera del control de versiones. |
| `test_docker_monitor.py` | Tests del parseo y de la lógica del cortacircuitos. |

## Cómo ejecutarlo

Cada módulo se autocomprueba contra bases de datos temporales y no toca nada real:

```bash
python3 metrics_db.py --selftest          # 22 aserciones
python3 test_docker_monitor.py            # 24 aserciones
python3 heartbeat.py --selftest           # 21 aserciones
```

El monitor tiene un modo de simulación que registra métricas pero ni reinicia nada ni envía notificaciones:

```bash
python3 docker_monitor.py --dry-run
```

## Configuración

Los valores por defecto resuelven relativos a este directorio, así que las autocomprobaciones funcionan en cualquier máquina sin configurar nada.

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `HOMELAB_DB` | `./data/homelab.db` | Base de datos de métricas |
| `HOMELAB_STATE` | `./data/docker_monitor_state.json` | Último estado conocido, para alertar solo en el cambio |
| `HOMELAB_HEARTBEATS` | `./data/heartbeats/` | Latidos y manifiesto |
| `HOMELAB_SECRETS` | `./.secrets/telegram.env` | Fichero de credenciales |
| `HOMELAB_MAINT_FILE` | `./data/.maintenance_until` | Ventana de mantenimiento; silencia notificaciones |
| `DOCKER_BIN` | `docker` | Binario de Docker |
| `DISK_DATA`, `DISK_BACKUP` | `/data`, `/backup` | Puntos de montaje a vigilar |
| `KUMA_BASE_URL` | `http://localhost:3001` | Monitor de uptime externo |

Las credenciales se leen primero del entorno, después de `HOMELAB_SECRETS` y de cualquier otro `*.env` que haya a su lado. Nunca se leen del código fuente.

## Tres cosas por las que merece la pena leerlo

**Nada lanza excepciones.** Todas las funciones de `metrics_db.py` y `heartbeat.py` capturan sus propias excepciones y devuelven un valor neutro. En código de aplicación eso esconde bugs; en un monitor, una excepción no capturada significa dejar de enterarte de todo lo demás. El `_selftest` lo verifica explícitamente apuntando la base de datos a una ruta donde no se puede escribir.

**`success` significa verificado.** `restart_container()` espera y después le pregunta a `docker inspect` si el contenedor está realmente corriendo. Registra `failed` cuando el comando devolvió cero y el contenedor no volvió. La página de [Fiabilidad](../docs/case-studies/reliability.md) explica por qué esa distinción costó siete semanas.

**El borrado está protegido en el SQL, no en el llamante.** `prune()` solo borra una hora de datos en crudo que ya exista en el agregado horario. Si la agregación falla, la purga no hace nada. La red de seguridad está en la consulta para que no se pueda saltar llamando a las cosas en el orden equivocado.

## Una advertencia

Esto es una foto, no un espejo. Las copias que corren en el host son la fuente de verdad y divergirán de estas. Donde eso importa, la [documentación](../docs/) describe el comportamiento en lugar de los números de línea.

> Los comentarios del código están en castellano, igual que el resto del repositorio.
