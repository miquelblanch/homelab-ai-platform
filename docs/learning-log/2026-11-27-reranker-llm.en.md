---
title: An LLM reranker
---

# An LLM reranker

**Project:** P1 · **Date:** 27 Nov 2026

## What surprised me

The reranker's improvement surprised me: MRR goes from 0.818 (RRF) to 0.925. My hypothesis was ≥ 0.88, and it beat it by a wide margin. The weak questions improve most (12 of 13), at €0.0006 per question.

What surprised me most is that, even so, one question got much worse (q083, from 1st to 8th place). Looking into it, we saw that the reranker gets only one chunk per document, and for the right document that chunk was the introduction, without the answer. To improve it, I would pass 2–3 chunks per document.
