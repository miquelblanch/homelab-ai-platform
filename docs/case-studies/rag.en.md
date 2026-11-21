# The RAG

> Hybrid retrieval over about 2,000 pages from two sources that look nothing alike. The hard part wasn't search: it was ingestion.

---

## What it indexes

Two sources, deliberately different:

| Source | What it holds | Approximate volume |
| --- | --- | --- |
| Obsidian vault | Personal markdown notes, densely linked | ~550 documents |
| Nextcloud | Office documents, PDFs, spreadsheets | ~600 documents |

Current total: **about 2,080 pages and 11,200 chunks**, all embedded: about 640 pages from the vault and about 1,440 from the file server.

The gap between the number of documents and the number of chunks already says something: a document produces more than five fragments on average, and that average is very unbalanced between the two sources.

---

## Hybrid retrieval

### The two engines

Two searches run in parallel over the same corpus.

**Dense.** Each fragment is represented by a 1,024-dimension vector, indexed with HNSW on pgvector. The query is turned into a vector and the nearest neighbours are found by cosine similarity. It captures meaning: it finds documents about the same thing even if they don't share a single word.

**Lexical.** PostgreSQL full-text with `tsvector`, ranked with BM25. It looks for the literal words, weighting by frequency in the document and rarity in the corpus.

### Why both

The short answer is that they fail in different places. The long answer shows up in a real example from this system.

If I ask about the `nextcloud_redis` container, dense search returns documents about Redis, documents about Nextcloud, and documents about caching in general. All topically correct. None is the one that mentions that specific container, because **for an embedding model an identifier is an odd string with little semantic meaning**, and its vector ends up near other odd identifiers rather than near the concept.

Lexical search finds that document immediately, because the string is literally there.

And the other way round: if I ask "how do I stop the disk filling up", lexical search looks for *disk* and *filling* and returns anything that contains them. Dense search finds the note about retention policies without using either word.

**In an operational corpus this is not a nuance.** Real questions constantly mix the conceptual and the literal: *"why does `immich_postgres` fail to start?"* needs lexical search to hit the identifier and dense search to find the note that explains database start-up without naming it.

### How they're combined: rank fusion

The problem with combining two search engines is that their scores aren't comparable. A cosine similarity of 0.82 and a BM25 score of 14.3 aren't on the same scale or distribution, and normalising them requires assumptions about those distributions that rarely hold.

**Reciprocal rank fusion** solves this by ignoring scores entirely and using only each document's **position** in each list. A document's combined score is the sum of `1/(k + position)` over the lists it appears in.

The practical consequence is what matters: **a document that comes third in both lists beats one that comes first in one and doesn't appear in the other.** It rewards consensus between two different ways of searching, which is exactly the signal you want when combining heterogeneous methods.

It is a simple method, with no parameter to tune beyond `k`, and surprisingly hard to beat precisely because it throws information away.

---

## Local embeddings

Embeddings are generated with Ollama on `bge-m3`, 1,024 dimensions, on the same machine.

**The reason isn't technical.** The corpus contains private data, so third-party embedding APIs were ruled out before evaluating any. Neither quality nor price was compared: they weren't candidates.

The costs of that decision are real, and it's worth stating them instead of hiding them:

**It limits model choice** to what fits in memory alongside the 51 containers and PostgreSQL. In practice, that means models considerably smaller than commercial ones.

**It makes a full reindex an operation of hours**, not minutes. That shapes the whole ingestion design: if reprocessing everything were cheap, the incremental pipeline that takes up half of this page would be unnecessary.

**It fixes the dimensionality.** Changing embedding models means re-embedding the whole corpus and rebuilding the index. It isn't a decision you can reverse for free, and the cost was paid: the first model was `nomic-embed-text`, with 768 dimensions, and moving to `bge-m3` meant reindexing everything.

---

## Ingestion, which is where the work is

Hybrid search is a few dozen lines and has been solved for years. **Ingestion is 90% of the code and 100% of the problems.**

The two sources have completely different pipelines because they look nothing alike.

### Path A: the notes vault

**Step 1: copy before touching.** The pipeline copies the whole vault to a temporary directory and works on the copy. The original files **are never modified**.

It may sound excessive until you consider what's at stake: the vault is a personal source of truth, synced across devices. A bug in the next step that wrote over the originals would spread to every device before anyone noticed. Copying takes seconds.

**Step 2: inject metadata as visible text.** This was the step with the biggest payoff, and the least obvious.

Notes carry frontmatter: tags, relations, area, date. It is extremely valuable for retrieval (it says what the note is about and what it relates to) and **it is invisible to search**, because the indexer treats it as metadata, not content.

The pre-step reads that frontmatter and writes it as plain text into the document body, in the temporary copy. From then on, a note tagged `runbook` and `docker` can be found by searching those words, even if they don't appear in its prose.

It is a concrete example of something general: **in a RAG pipeline, the question isn't what information the document has, but what information reaches the index.**

**Step 3: import.** Documents are identified by their relative path inside the vault, which becomes their identifier (their *slug*) in the system.

### Path B: the file server

There is no markdown here: there are PDFs, office documents and spreadsheets, downloaded over WebDAV and converted to text.

**Deliberate exclusions.** There is a list of folders that aren't indexed, for two different reasons: some are large and add nothing to a search (book libraries, appliance manuals, audio recordings); others contain sensitive documents that I decided not to put into an index a language model can query.

That second category is a design decision worth stating: **not everything you can index, you should.** A home RAG multiplies the exposure surface of any document that goes into it.

**Conversion.** Each file type has its own path to text. And each path has its own failure modes, which brings us to the interesting part.

---

## Defensive engineering in the exporter

A pipeline that processes arbitrary files from a home server faces inputs you never anticipated. Not because they're malicious, but because they've been piling up for years.

### Three hard limits

**Maximum file size: 50 MB.** Anything above is skipped. A video or a disk image forgotten in a folder must not consume the whole pipeline.

**Maximum rows per sheet in spreadsheets: 2,000.** There are sheets with tens of thousands of rows (exports, logs, registers) whose extracted text would be huge and useless for retrieval.

**Per-file timeout: 120 seconds.**

### Process isolation

The timeout isn't implemented with a timer inside the loop, but by **processing each file in a child process that is killed if it runs over time**.

It's more expensive, and it's the only way that works. A converter stuck in an infinite loop, or exhausting memory inside a C library, doesn't respond to any Python timer. It can only be killed from outside.

Without that isolation, a single malformed file blocks the whole nightly reindex, indefinitely, and the system stops updating without anything failing visibly.

### Quarantine for poisonous files

Files that hit the timeout **are flagged and not retried** in later runs.

Without this, every night the pipeline would spend two minutes on each problem file, only to fail again. With twenty such files, that's forty minutes a day burned retrying something already known not to work.

The cost of the decision is that a quarantined file stays out of the index permanently and silently. It's a conscious trade-off, and the kind of thing that would deserve a periodic report that doesn't exist today.

---

## Two incidents that show how this works inside

### A document's identity is its path

Documents are identified by their relative path. And the `/notas` command filters by that identifier to return only results from the notes vault, excluding the file server's.

At some point the pipeline was configured pointing at the vault's **parent** directory instead of the vault itself. The effect: every identifier gained an extra leading segment.

The content was indexed perfectly. General search worked. And the filtered command stopped returning anything, because the filter looked for identifiers starting with certain prefixes, and now they all started with another one.

**What makes it instructive** is that there was no error. No exception, no warning, nothing in the logs. A system working perfectly while returning zero results for one specific command, because each document's identifier (which you tend to see as an implementation detail) is actually **part of its semantics**.

The fix required deleting and reindexing the affected notes, because the old and new identifiers coexisted as different documents.

### The incremental state that lied

Reindexing is incremental: a state file stores the last-modified date of each processed document, and only what changed is reprocessed. That is what lets the nightly pass take minutes instead of hours.

One day that state file got corrupted. The result:

```
0 exportados, 1.495 sin cambios
```

("0 exported, 1,495 unchanged.") A perfectly reassuring message. It means "everything was already up to date". But the output directory was empty: nothing had been exported, and nothing had been in previous runs either.

**The state said 1,495 files were processed and none were.** The system compared against a record that no longer matched reality, concluded there was no work, and reported it as success.

It is exactly the same pattern that runs through this project: **a green indicator that measures something other than what it promises.** "Unchanged" measured the contents of a state file, not the contents of the index.

Recovery is deleting the state file and forcing a full reprocess. Detection, today, still depends on someone noticing.

---

## What this system indexes badly

### Spreadsheets

The indexer chunks spreadsheets **by cell**, losing row and column context. The result is meaningless fragments: loose values without the header that says what they are or the row that says whose they are.

A fragment that says `1,240` is neither retrievable nor useful. The same value as `Maintenance budget 2026: €1,240` is.

It's a real, known limitation, and it affects a part of the corpus with information that is worth querying.

**The identified solution** is to convert the relevant sheets into markdown tables, with headers repeated so each fragment stands on its own, and feed them through the vault path. **It isn't implemented.**

What's interesting about the case is what it shows: **chunking isn't a parameter, it's a decision that depends on the document type.** Fixed-size chunking that works well for prose destroys tabular data. A serious RAG pipeline needs different strategies for different source types, and this one has only one.

---

## Soft delete

Documents aren't deleted from the index: they're marked with a deletion date and excluded from searches.

There is a grace window before physical deletion. That allows recovering from a misconfigured reindex that marks correct documents as deleted, which happened in the identifier incident.

The cost is that a real deletion requires a deliberate step, and that the database size doesn't reflect active content. For a corpus this size, it's a clearly favourable trade.

---

## Operation

**Automatic nightly reindex**: it checks that the service is up, processes the vault, exports what changed on the file server, imports both, and leaves a **heartbeat** with what it processed, whether it succeeds or fails.

The first version only notified on errors, and that made silence ambiguous between "all good" and "the reindex didn't run either", for the same reason explained in [Reliability](reliability.md). With the heartbeat, silence stops being a signal: if it doesn't run one night, the heartbeat goes stale and the alarm panel flags it. And the reindex **fails loudly** if the vault arrives with zero notes or the file server doesn't answer, instead of accepting an empty pass.

That last point isn't theoretical. The vault copy that gets indexed lived in a temporary folder that only the manual sync prepared, and that the manual sync deleted when finished. The nightly reindex didn't prepare it. **For two and a half months it imported zero vault notes every night and ended saying "OK"**: the index kept the notes from the last manual sync, searches kept returning results, and nothing looked broken. New material simply didn't get in. It is the same mistake as [the metrics table that stopped receiving writes](../decisions.md): something that stands still looks a lot like something that's working.

**Manual sync** is available as a Telegram command, which launches the same process in the background and confirms immediately. It exists because when you've just written a note and want to query it, waiting until 04:45 isn't an option.

---

## What isn't evaluated

And this is where this page has to be honest, because it is the most important gap in the whole project.

**There is no measurement of retrieval quality at all.** No set of questions with their correct answers, no precision, no recall, no comparison between strategies.

Specifically, I can't answer any of these questions:

- How much does hybrid fusion add over dense search alone? I know it adds *something*, because I've seen specific cases where one finds what the other doesn't. I don't know how much.
- Is the fusion `k` value right? It's the default.
- Is the chunk size right for this corpus? It has never been compared with others.
- Would a reranker on the results improve precision enough to pay for its latency?
- Which of the two sources produces more useful answers? The impression is the vault, by far, despite contributing less volume. It's an impression.

**All those questions are answered with a hand-built evaluation set of a couple of hundred questions and a few hours of boring work.** That is the next project, and it isn't done.

Meanwhile, what exists is a production system used daily whose design decisions are reasoned. That isn't the same as an evaluated system, and it shouldn't be presented as one.

It's worth saying out loud because it is the dominant pattern in this category of projects: **almost nobody evaluates their RAG**, and almost everyone claims it works well. The difference between the two is precisely the boring work of building the test set.
