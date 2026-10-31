# Homelab AI Platform

Una plataforma de IA autoalojada sobre un único Mac Mini M4 Pro. Da servicio a mi casa, pero está construida y operada con las prácticas que aplicaría a un sistema en producción: instrumentada, testeada, documentada y honesta sobre lo que hace mal.

**49 contenedores. Un agente del que no depende nada esencial. Unas 2.000 páginas indexadas. Cero dependencias cloud para lo que importa, y casi cien averías provocadas a propósito para comprobar que se recupera.**

---

## Qué hace

**Un agente conversacional sobre Telegram.** Consulta la base de conocimiento, opera la infraestructura y la casa, y entra en juego cuando hace falta criterio: tras un corte de luz o ante una avería sin receta conocida. Usa un modelo en la nube con una reserva más barata que entra sola si el principal falla. Todo lo esencial —vigilar, reparar, copiar, informar— funciona sin él.

**Un RAG de búsqueda híbrida** sobre unas 2.080 páginas y 11.200 chunks. Búsqueda vectorial (HNSW) fusionada con búsqueda léxica (BM25) mediante reciprocal rank fusion. Los embeddings corren en local sobre `bge-m3`.

**Infraestructura que se recupera sola.** Unos sesenta servicios propios vigilan contenedores, discos, relays, domótica, DNS y seguridad, y lo reúnen en una central de alarmas. Cada avería se repara sola cuando hay una acción segura para ella —el reinicio **se verifica**, no basta con que el comando devuelva 0— y un cortacircuitos detiene el bucle cuando algo empieza a caerse en cadena. Lo que se arregla a mano dos veces se convierte en receta.

**Backups que se comprueban, no que se suponen.** Copia nocturna con un volcado coherente de cada base de datos, una segunda copia con historial y diecisiete verificaciones a la mañana siguiente. El informe llega tanto si pasa como si falla. Y se restauran de verdad, en ensayos guionizados.

**Averías provocadas a propósito.** Un programa de antifragilidad con casi cien experimentos —de matar un contenedor a cortar Internet una hora o apagar el Mac de golpe— que mide cuánto se tarda en detectar y en reparar cada fallo, y convierte cada sorpresa en un arreglo.

---

## Por qué esto es un caso de estudio

Casi todo lo que se escribe sobre homelabs es un inventario: una lista de contenedores y la captura de un panel. Esto va de la parte difícil y transferible — **descubrir que tu monitorización te está mintiendo**.

Instrumentando el pipeline de métricas, tres indicadores que llevaban meses en verde resultaron medir algo distinto de lo que prometía su etiqueta:

| Indicador | Lo que parecía decir | Lo que medía en realidad |
| --- | --- | --- |
| Recuperación automática: 100% de éxito | los reinicios arreglan el problema | que `docker restart` devolvió 0 |
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

- **[El RAG](case-studies/rag.md)**

    Recuperación híbrida y fusión por rango, embeddings locales, y por qué la parte difícil de un RAG no es la búsqueda sino la ingesta.

- **[Fiabilidad](case-studies/reliability.md)**

    Persistencia de métricas, recuperación verificada, cortacircuitos, retención con agregación, y latidos para las tareas programadas.

- **[Auditoría de seguridad](case-studies/security.md)**

    Una auditoría activa de toda la plataforma: 37 hallazgos, once de ellos críticos, y un control que reportaba justo lo contrario de lo que estaba pasando.

</div>

Si solo vas a leer una página, lee **[Decisiones y errores](decisions.md)**. Es la más corta y la única donde las cosas salen mal.

---

## Restricciones que conviene conocer

Esto es un sistema de un solo nodo, sin redundancia, sobre hardware de consumo y en una red doméstica plana. Está diseñado para una casa, no para una empresa. Varias decisiones que parecen raras —relays para alcanzar la LAN, un proxy TCP delante del proxy inverso, SQLite en lugar de una base de datos de series temporales— salen directamente de esas restricciones, y cada una se argumenta donde aparece.

Las direcciones, dominios y nombres de host que aparecen en esta documentación son ilustrativos.
