# El RAG

> Recuperación híbrida sobre unas 2.000 páginas de dos fuentes que no se parecen en nada. La parte difícil no fue la búsqueda: fue la ingesta.

---

## Qué indexa

Dos fuentes, deliberadamente distintas:

| Fuente | Qué contiene | Volumen aproximado |
| --- | --- | --- |
| Vault de Obsidian | Notas personales en markdown, densamente enlazadas | ~550 documentos |
| Nextcloud | Documentos ofimáticos, PDFs, hojas de cálculo | ~600 documentos |

Total actual: **unas 2.080 páginas y 11.200 chunks**, todos con embedding: unas 640 páginas del vault y unas 1.440 del servidor de ficheros.

La diferencia entre el número de documentos y el de chunks ya dice algo: un documento produce en media más de cinco fragmentos, y esa media está muy desequilibrada entre las dos fuentes.

---

## Recuperación híbrida

### Los dos motores

Sobre el mismo corpus corren dos búsquedas en paralelo.

**Densa.** Cada fragmento está representado por un vector de 1.024 dimensiones, indexado con HNSW sobre pgvector. La consulta se convierte en vector y se buscan los vecinos más próximos por similitud de coseno. Captura significado: encuentra documentos que hablan de lo mismo aunque no compartan ni una palabra.

**Léxica.** Full-text de PostgreSQL con `tsvector`, rankeada con BM25. Busca las palabras literales, ponderando por frecuencia en el documento y rareza en el corpus.

### Por qué las dos

La respuesta corta es que fallan en sitios distintos. La larga se ve con un ejemplo real de este sistema.

Si pregunto por el contenedor `nextcloud_redis`, la búsqueda densa devuelve documentos sobre Redis, documentos sobre Nextcloud, y documentos sobre caché en general. Todos temáticamente correctos. Ninguno es el que menciona ese contenedor concreto, porque **para un modelo de embeddings un identificador es una cadena rara con poco significado semántico**, y su vector queda cerca de otros identificadores raros en lugar de cerca del concepto.

La léxica encuentra ese documento de inmediato, porque la cadena está literalmente ahí.

Y al revés: si pregunto "cómo evito que se llene el disco", la léxica busca *disco* y *llene* y devuelve cualquier cosa que las contenga. La densa encuentra la nota que habla de políticas de retención sin usar ninguna de esas dos palabras.

**En un corpus operativo esto no es un matiz.** Las preguntas reales mezclan constantemente lo conceptual con lo literal: *"¿por qué falla `immich_postgres` al arrancar?"* necesita la búsqueda léxica para dar con el identificador y la densa para encontrar la nota que explica el arranque de las bases de datos sin nombrarlo.

### Cómo se combinan: fusión por rango

El problema de combinar dos buscadores es que sus puntuaciones no son comparables. Una similitud de coseno de 0,82 y una puntuación BM25 de 14,3 no están en la misma escala ni en la misma distribución, y normalizarlas exige asumir cosas sobre esas distribuciones que rara vez se cumplen.

**Reciprocal rank fusion** resuelve esto ignorando las puntuaciones por completo y usando solo la **posición** de cada documento en cada lista. La puntuación combinada de un documento es la suma de `1/(k + posición)` sobre las listas en que aparece.

La consecuencia práctica es la que importa: **un documento que sale tercero en las dos listas gana a uno que sale primero en una y no aparece en la otra.** Premia el consenso entre dos formas distintas de buscar, que es exactamente la señal que uno quiere cuando combina métodos heterogéneos.

Es un método sencillo, sin parámetros que ajustar más allá de `k`, y sorprendentemente difícil de batir precisamente porque tira información.

---

## Embeddings locales

Los embeddings se generan con Ollama sobre `bge-m3`, 1.024 dimensiones, en la misma máquina.

**La razón no es técnica.** El corpus contiene datos privados, así que las APIs de embeddings de terceros quedaron descartadas antes de evaluar ninguna. No se comparó calidad ni precio: no eran candidatas.

Los costes de esa decisión son reales y conviene enunciarlos en vez de esconderlos:

**Limita la elección de modelo** a lo que quepa en memoria junto con los 49 contenedores y PostgreSQL. En la práctica eso significa modelos considerablemente más pequeños que los comerciales.

**Hace que un reindexado completo sea una operación de horas**, no de minutos. Lo cual condiciona todo el diseño de la ingesta: si reprocesar todo fuera barato, el pipeline incremental que ocupa la mitad de esta página sería innecesario.

**Fija la dimensionalidad.** Cambiar de modelo de embeddings implica reembeber el corpus entero y reconstruir el índice. No es una decisión reversible sin coste — y el coste se pagó: el primer modelo fue `nomic-embed-text`, de 768 dimensiones, y pasar a `bge-m3` supuso reindexarlo todo.

---

## La ingesta, que es donde está el trabajo

La búsqueda híbrida son unas decenas de líneas y está resuelta hace años. **La ingesta es el 90% del código y el 100% de los problemas.**

Las dos fuentes tienen pipelines completamente distintos porque no se parecen en nada.

### Ruta A — el vault de notas

**Paso 1: copiar antes de tocar.** El pipeline copia el vault entero a un directorio temporal y trabaja sobre la copia. Los ficheros originales **nunca se modifican**.

Puede sonar excesivo hasta que uno considera qué está en juego: el vault es una fuente de verdad personal, sincronizada entre dispositivos. Un bug en el paso siguiente que escribiera sobre los originales se propagaría a todos los dispositivos antes de que nadie lo notase. Copiar cuesta segundos.

**Paso 2: inyectar los metadatos como texto visible.** Este es el paso que más rendimiento dio y el menos evidente.

Las notas llevan frontmatter: etiquetas, relaciones, área, fecha. Es información valiosísima para recuperar —dice de qué va la nota y con qué se relaciona— y **es invisible para la búsqueda**, porque el indexador la trata como metadatos y no como contenido.

El paso previo lee ese frontmatter y lo escribe como texto plano dentro del cuerpo del documento, en la copia temporal. A partir de ahí, una nota etiquetada como `runbook` y `docker` es recuperable buscando esas palabras, aunque no aparezcan en su prosa.

Es un ejemplo concreto de algo general: **en un pipeline de RAG, la pregunta no es qué información tiene el documento, sino qué información llega al índice.**

**Paso 3: importar.** Los documentos se identifican por su ruta relativa dentro del vault, que se convierte en su identificador —su *slug*— dentro del sistema.

### Ruta B — el servidor de ficheros

Aquí no hay markdown: hay PDFs, documentos ofimáticos y hojas de cálculo, descargados por WebDAV y convertidos a texto.

**Exclusiones deliberadas.** Hay una lista de carpetas que no se indexan. Dos motivos distintos: unas son grandes y no aportan nada a una búsqueda (bibliotecas de libros, manuales de aparatos, grabaciones de audio); otras contienen documentación sensible que decidí no meter en un índice consultable por un modelo de lenguaje.

Esa segunda categoría es una decisión de diseño que merece enunciarse: **no todo lo que puedes indexar deberías indexarlo.** Un RAG doméstico multiplica la superficie de exposición de cualquier documento que entre en él.

**Conversión.** Cada tipo de fichero tiene su camino a texto. Y cada camino tiene sus modos de fallo, lo que nos lleva a la parte interesante.

---

## Ingeniería defensiva en el exportador

Un pipeline que procesa ficheros arbitrarios de un servidor doméstico se enfrenta a entradas que nunca previste. No porque sean maliciosas, sino porque llevan años acumulándose.

### Tres límites duros

**Tamaño máximo de fichero: 50 MB.** Por encima se salta. Un vídeo o una imagen de disco olvidada en una carpeta no debe consumir el pipeline entero.

**Máximo de filas por hoja en las hojas de cálculo: 2.000.** Existen hojas con decenas de miles de filas —exportaciones, logs, registros— cuyo texto extraído sería enorme e inútil para recuperación.

**Timeout por fichero: 120 segundos.**

### El aislamiento por proceso

El timeout no se implementa con un temporizador dentro del bucle, sino **procesando cada fichero en un proceso hijo que se mata si supera el tiempo**.

Es más costoso y es la única forma que funciona. Un conversor que entra en un bucle infinito, o que agota la memoria dentro de una biblioteca de C, no responde a ningún temporizador en Python. Solo se puede matar desde fuera.

Sin ese aislamiento, un solo fichero malformado bloquea el reindexado nocturno entero, indefinidamente, y el sistema deja de actualizarse sin que nada falle de forma visible.

### Cuarentena de ficheros venenosos

Los ficheros que agotan el timeout **se marcan y no se reintentan** en ejecuciones posteriores.

Sin esto, cada noche el pipeline gastaría dos minutos en cada fichero problemático, para volver a fallar. Con veinte ficheros así, son cuarenta minutos diarios quemados en reintentar algo que ya se sabe que no funciona.

El coste de la decisión es que un fichero en cuarentena queda fuera del índice de forma permanente y silenciosa. Es un compromiso consciente, y el tipo de cosa que merecería un informe periódico que hoy no existe.

---

## Dos incidentes que enseñan cómo funciona esto por dentro

### La identidad de un documento es su ruta

Los documentos se identifican por su ruta relativa. Y el comando `/notas` filtra por ese identificador para devolver solo resultados del vault de notas, excluyendo los del servidor de ficheros.

En algún momento el pipeline se configuró apuntando al directorio **padre** del vault en lugar de al vault mismo. El efecto: todos los identificadores ganaron un segmento extra al principio.

El contenido se indexó perfectamente. La búsqueda general funcionaba. Y el comando filtrado dejó de devolver nada, porque el filtro buscaba identificadores que empezaran por ciertos prefijos y ahora todos empezaban por otro.

**Lo que lo hace instructivo** es que no hubo ningún error. Ni excepción, ni aviso, ni nada en los logs. Un sistema que funcionaba perfectamente devolviendo cero resultados a un comando concreto, porque el identificador de cada documento —que uno tiende a ver como un detalle de implementación— es en realidad **parte de su semántica**.

La corrección obligó a borrar y reindexar las notas afectadas, porque los identificadores antiguos y los nuevos coexistían como documentos distintos.

### El estado incremental que mentía

El reindexado es incremental: un fichero de estado guarda la fecha de última modificación de cada documento procesado, y solo se reprocesa lo que ha cambiado. Es lo que permite que la pasada nocturna dure minutos en vez de horas.

Un día ese fichero de estado se corrompió. El resultado:

```
0 exportados, 1.495 sin cambios
```

Un mensaje perfectamente tranquilizador. Significa "todo estaba ya al día". Pero el directorio de salida estaba vacío: no había nada exportado, y no había habido nada en las ejecuciones anteriores tampoco.

**El estado decía que 1.495 ficheros estaban procesados y ninguno lo estaba.** El sistema comparaba contra un registro que ya no correspondía a la realidad, concluía que no había trabajo, y lo reportaba como éxito.

Es exactamente el mismo patrón que recorre este proyecto: **un indicador en verde que mide algo distinto de lo que promete.** "Sin cambios" medía el contenido de un fichero de estado, no el contenido del índice.

La recuperación es borrar el fichero de estado y forzar un reprocesado completo. La detección, hoy, sigue siendo que alguien se dé cuenta.

---

## Lo que este sistema indexa mal

### Hojas de cálculo

El indexador trocea las hojas de cálculo **por celdas**, perdiendo el contexto de fila y columna. El resultado son fragmentos sin significado: valores sueltos sin la cabecera que dice qué son ni la fila que dice de quién.

Un fragmento que dice `1.240` no es recuperable ni útil. El mismo dato como `Presupuesto de mantenimiento 2026: 1.240 €` sí lo es.

Es una limitación real y conocida, y afecta a una parte del corpus donde hay información que sí interesa consultar.

**La solución identificada** es convertir las hojas relevantes a tablas en markdown, con las cabeceras repetidas de forma que cada fragmento se sostenga solo, y meterlas por la ruta del vault. **No está implementada.**

Lo interesante del caso es lo que ilustra: **el chunking no es un parámetro, es una decisión que depende del tipo de documento.** Un troceado por tamaño fijo que funciona bien con prosa destruye datos tabulares. Un pipeline de RAG serio necesita estrategias distintas según el tipo de fuente, y este solo tiene una.

---

## Borrado suave

Los documentos no se borran del índice: se marcan con una fecha de eliminación y quedan excluidos de las búsquedas.

Existe una ventana de gracia antes del borrado físico. Eso permite recuperarse de un reindexado mal configurado que marque documentos correctos como eliminados — cosa que pasó con el incidente de los identificadores.

El coste es que un borrado real requiere un paso deliberado, y que el tamaño de la base de datos no refleja el contenido activo. Para un corpus de este tamaño, es un intercambio claramente favorable.

---

## Operación

**Reindexado nocturno automático**: verifica que el servicio está en pie, procesa el vault, exporta lo modificado del servidor de ficheros, importa ambos, y deja un **latido** con lo que ha procesado — tanto si sale bien como si sale mal.

La primera versión solo notificaba si había errores, y eso hacía el silencio ambiguo entre "todo bien" y "el reindexado tampoco corrió", por lo mismo que se explica en [Fiabilidad](reliability.md). Con el latido, el silencio deja de ser una señal: si una noche no corre, el latido envejece y la central de alarmas lo marca. Y el reindexado **falla en voz alta** si el vault llega con cero notas o el servidor de ficheros no responde, en vez de dar por buena una pasada vacía.

Eso último no es teórico. La copia del vault que se indexa vivía en una carpeta temporal que solo preparaba la sincronización manual, y que esta borraba al acabar. El reindexado nocturno no la preparaba. **Durante dos meses y medio importó cero notas del vault cada noche y terminó diciendo «OK»**: el índice conservaba las notas de la última sincronización manual, las búsquedas seguían devolviendo resultados, y nada parecía roto. Lo nuevo simplemente no entraba. Es el mismo error que [la tabla de métricas que dejó de recibir escrituras](../decisions.md): algo que se queda quieto se parece mucho a algo que va bien.

**Sincronización manual** disponible como comando desde Telegram, que lanza el mismo proceso en segundo plano y confirma de inmediato. Existe porque cuando acabas de escribir una nota y quieres consultarla, esperar a las 04:45 no es una opción.

---

## Lo que no está evaluado

Y aquí es donde esta página tiene que ser honesta, porque es la carencia más importante de todo el proyecto.

**No hay ninguna medición de la calidad de recuperación.** Ni un conjunto de preguntas con sus respuestas correctas, ni precisión, ni recall, ni una comparación entre estrategias.

Concretamente, no puedo responder a ninguna de estas preguntas:

- ¿Cuánto aporta la fusión híbrida frente a usar solo la búsqueda densa? Sé que aporta *algo*, porque he visto casos concretos donde una encuentra lo que la otra no. No sé cuánto.
- ¿Es correcto el valor de `k` de la fusión? Es el que venía por defecto.
- ¿El tamaño de fragmento es el adecuado para este corpus? Nunca se ha comparado con otros.
- ¿Un reordenador sobre los resultados mejoraría la precisión lo suficiente para pagar su latencia?
- ¿Cuál de las dos fuentes produce respuestas más útiles? La impresión es que el vault, con mucha diferencia, pese a aportar menos volumen. Es una impresión.

**Todas esas preguntas se responden con un conjunto de evaluación de un par de cientos de preguntas construido a mano y unas cuantas horas de trabajo aburrido.** Ese es el siguiente proyecto, y no está hecho.

Mientras tanto, lo que hay es un sistema en producción que se usa a diario y cuyas decisiones de diseño están razonadas. Eso no es lo mismo que un sistema evaluado, y no conviene presentarlo como si lo fuera.

Merece decirse en voz alta porque es el patrón dominante en esta categoría de proyectos: **casi nadie evalúa su RAG**, y casi todo el mundo afirma que funciona bien. La diferencia entre las dos cosas es precisamente el trabajo aburrido de construir el conjunto de pruebas.
