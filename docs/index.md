# Homelab AI Platform

Una plataforma de IA autoalojada sobre un único Mac Mini M4 Pro. Da servicio a mi casa, pero está construida y operada con las prácticas que aplicaría a un sistema en producción: instrumentada, testeada, documentada y honesta sobre lo que hace mal.

**41 contenedores. Un agente. 1.991 documentos indexados. Cero dependencias cloud para lo que importa.**

---

## Qué hace

**Un agente conversacional sobre Telegram.** Enruta cada petición a un `qwen3.5-9b` local o a un modelo cloud según la complejidad, ejecuta tareas programadas, consulta la base de conocimiento y opera la infraestructura — con aprobación humana obligatoria antes de tocar nada crítico.

**Un RAG de búsqueda híbrida** sobre 1.991 documentos y 10.762 chunks. Búsqueda vectorial (HNSW) fusionada con búsqueda léxica (BM25) mediante reciprocal rank fusion. Los embeddings corren en local sobre `nomic-embed-text`.

**Infraestructura que se auto-repara.** Cada cinco minutos se revisan 41 contenedores y 3 discos. Los no críticos se reinician automáticamente y el reinicio **se verifica**; los críticos se escalan a un humano. Un cortacircuitos detiene el bucle cuando un contenedor empieza a caerse en cadena.

**Backups que se comprueban, no que se suponen.** Snapshot nocturno más nueve volcados de base de datos, seguidos de trece verificaciones de integridad a la mañana siguiente. El informe llega tanto si pasa como si falla.

---

## Por qué esto es un caso de estudio

Casi todo lo que se escribe sobre homelabs es un inventario: una lista de contenedores y la captura de un panel. Esto va de la parte difícil y transferible — **descubrir que tu monitorización te está mintiendo**.

Instrumentando el pipeline de métricas, tres indicadores que llevaban meses en verde resultaron medir algo distinto de lo que prometía su etiqueta:

| Indicador | Lo que parecía decir | Lo que medía en realidad |
| --- | --- | --- |
| Auto-remediación: 100% de éxito | los reinicios arreglan el problema | que `docker restart` devolvió 0 |
| Ratio de salud de contenedores | proporción de contenedores sanos | todo lo que no estaba roto explícitamente |
| Relays 5/5 | los nueve relays funcionan | cinco comprobaciones pasan, tres sin relación |

Ninguno estaba roto. Cada uno medía algo real. Ninguno medía lo que prometía.

Ese es el hilo que recorre toda esta documentación: la diferencia entre un sistema que reporta verde y un sistema que de verdad funciona, y la ingeniería que hace falta para cerrar esa distancia.

---

## Por dónde empezar

<div class="grid cards" markdown>

- **[Arquitectura](architecture.md)**

    Cómo encajan las piezas: el runtime del agente, el motor de RAG, la capa de monitorización, y por qué la red de contenedores necesita relays para alcanzar dispositivos físicos.

- **[El agente](case-studies/agent.md)**

    Enrutado de modelos por coste, tool calling contra infraestructura real, aprobación humana, y las tres formas en que un modelo local de 9B falló en silencio.

- **[Fiabilidad](case-studies/reliability.md)**

    Persistencia de métricas, remediación verificada, cortacircuitos, retención con agregación, y latidos para las tareas programadas.

- **[Auditoría de seguridad](case-studies/security.md)**

    Una auditoría activa de toda la plataforma: 37 hallazgos, once de ellos críticos, y un control que reportaba justo lo contrario de lo que estaba pasando.

</div>

Si solo vas a leer una página, lee **[Decisiones y errores](decisions.md)**. Es la más corta y la única donde las cosas salen mal.

---

## Restricciones que conviene conocer

Esto es un sistema de un solo nodo, sin redundancia, sobre hardware de consumo y en una red doméstica plana. Está diseñado para una casa, no para una empresa. Varias decisiones que parecen raras —relays para alcanzar la LAN, un modelo local para las tareas programadas, SQLite en lugar de una base de datos de series temporales— salen directamente de esas restricciones, y cada una se argumenta donde aparece.

Las direcciones, dominios y nombres de host que aparecen en esta documentación son ilustrativos.
