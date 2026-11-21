# The agent

> An agent that operates real infrastructure from Telegram: how the attempt to make it cheap with a local model failed silently, and why the best design in the end was that nothing essential depended on it.

---

## What it is

A conversational agent, built on Hermes Agent, that you message on Telegram. It can answer questions, query the knowledge base, and **run actions on the infrastructure and the house**: list containers, read their logs, restart services, query and control the home automation, check disk space.

It also runs a couple of scheduled tasks and a nightly pass that consolidates its own memory, and other systems **wake it up** when judgment is needed: after a power cut, or when a failure appears that nobody knows how to fix yet.

### Why Telegram

It is not a product decision; it is a consequence.

The natural alternative would be a web interface. That means publishing one more service, protecting it with authentication, maintaining a frontend and, most importantly, **opening an additional access surface to a system that already has plenty**.

Telegram removes all of that. It is already installed on the phone, authentication is the device's own, it works from outside the house without exposing any port, and the notifications the monitors already send arrive in the same place. The agent and the alerts share a channel, which means that when an alert fires you can reply asking about it without switching apps.

The cost is a plain-text interface, with no rich components or visual state. For operating infrastructure, that turns out to be enough.

### Why it doesn't run in a container

The agent manages the container runtime. Putting it inside the thing it manages creates a circular dependency whose failure mode only shows up when something has already gone wrong: if Docker has a problem, the agent that could diagnose it is down precisely because of that problem.

It runs as a native system process, managed by the operating system's scheduler. This is developed in [Architecture](../architecture.md).

---

## What the agent doesn't do

The most important decision about the agent is what it **stopped doing**.

In the first version the agent was the centre: it generated the daily system report, sent the agenda, watched disk space, decided what to restart. Every agent failure (a provider down, an account out of credit, a runtime update that broke something, a model answering badly) took down things that had nothing to do with conversation.

Today the rule is the opposite: **everything essential is deterministic code that runs without it.** Monitoring, repairing, backing up, recovering from a power cut, alerting and writing the morning report don't need a language model, so they don't use one. The agent comes in when judgment is needed, and if it goes down, you lose the conversation and little else.

The following sections tell how it got there, because the path is more instructive than the destination.

---

## The model routing story

### The arithmetic

The first design had cost-based model routing, and it is worth putting rough numbers on why.

An interactive conversation is a few requests a day. Even if each one carries plenty of context, the monthly total is negligible with any provider.

A scheduled task is something else. It runs at fixed times, carries its full system context, chains several tool calls, and **nobody watches it**. Multiplied by several tasks and thirty days, it is no longer negligible. Above all, it is spend that grows by itself, without any action of yours triggering it.

Hence the rule at the time: **automatic work goes local by default; only what proves it needs it moves up to the cloud.** The local model was `qwen3.5-9b`, served from LM Studio on loopback, with a per-task pin in the configuration.

### The crons stopped working all at once

One day, every scheduled task that used the agent started being marked as failed:

```
last_status: error
last_error: "RuntimeError: HTTP 402: Insufficient Balance"
```

**The cloud provider account had run out of credit.** With no warning on my side, because I had never looked at consumption: the spend was small and grew slowly, which is exactly the kind of spend nobody watches.

A 402 is an honest, noisy error, so this part was diagnosed in minutes.

### The fallback saved nothing

The configuration had a local fallback. If the cloud provider fails, tasks are supposed to fall back to the local model and keep working.

That didn't happen. **LM Studio was running but had no model loaded.**

An inference server with no model loaded answers the health check (the process is alive, the port accepts connections) but cannot serve any request. The fallback existed, pointed at something that answered, and was useless.

It is the same pattern that runs through this whole project, applied to another layer: **something that looks available because it responds, but cannot do its job.**

### Where a 9B model stops being useful

When the tasks moved to local, some worked perfectly and others didn't. The pattern took a while to see because **they didn't fail: they delivered something**.

Tasks that make **a single tool call** (check the disk, run a script and summarise the output) worked with the local model without any problem.

**Multi-tool** tasks, which chain several calls and decide the next one based on the result of the previous one, returned this in the runtime log:

```
↻ Empty response after tool calls
```

The model ran the tools correctly. It received the results. And then it produced an empty final answer. Faced with that, the runtime delivered the only thing it had: **the narration of the plan** the model had verbalised before starting.

What reached Telegram was along the lines of *"now I'll check the status of the containers and prepare a summary"*. Instead of the summary.

Listing the failure mode:

- The task **ends with exit code 0**
- There is no exception, no timeout, and no error in any log except that runtime line
- The task status stays `ok`
- The output is **grammatically perfect, plausible** prose
- It arrives through the usual channel, at the usual time

If you skim that message at three in the morning, it looks like it worked. And if the task is a daily summary of system status, a message saying "I'm going to check the system" looks a lot like a message saying the system is fine.

**It can't be fixed with the prompt.** Even when explicitly demanding a final summary sentence, the answer kept going empty after the tool calls. The same prompt, task and tools against the cloud model delivered a clean answer. It is a limitation of the model keeping coherence over a long sequence of tool exchanges: the kind of limit that doesn't show up in any benchmark. A benchmark measures whether the model can use a tool; it doesn't measure whether it holds up on the sixth.

### How it ended

The resulting rule was *local for a single tool, cloud for several*. It didn't last long, because the next question was obvious: **did the single-tool tasks need a model at all?**

No. They were "check this and summarise it", and a summary of a known state is better written in code: always the same, always complete, free, and with no way to narrate instead of doing. One by one they left the agent: the daily report, the agenda, the disk monitoring. When the local model ran out of work, it left the machine and freed the memory it was using.

Today the agent uses a main cloud model and a **cheaper fallback** that kicks in on its own if the main one fails (outage, key, credit) and **announces it on Telegram**. Both lessons from the 402 are applied: a monitor watches account balances before they run out, and the fallback announces itself instead of hiding.

---

## Tool calling against real infrastructure

### What it can do

The agent's tools are not queries against a toy API. They run commands against the system: list container status, read the last log lines of a given one, restart it, check disk space, query and control the home automation, search the knowledge base.

That is what makes the agent useful and what makes it dangerous.

### The line between acting and asking

The first version handled the danger with lists: **critical** containers (databases, password manager, home automation, reverse proxy) where the system alerted but touched nothing; containers that were **never** touched; and the rest, which restarted on their own. Keeping that line in an explicit list rather than in the model's judgment was deliberate: a model can reason very well about whether restarting a database is a good idea and get it wrong the day its context arrives truncated.

The line has moved, but the principle stands: **the model doesn't decide what it may do, only whether something applies.**

- **Automatic remediation** has a closed list of reversible actions (restart a container, restart a service, rotate a log). A model reads the episode's evidence and answers a single question: does any of these actions fix it? If it says no, there is no action; if it says yes, the action runs **and is verified**, with a circuit breaker if it repeats. With that verification, restarting the critical containers too turned out to be less risky than waiting for someone to read the alert.
- Whatever doesn't fit that list goes to the **Aprendiz** (the Apprentice): the first time a failure appears it stores the evidence and notes how it was fixed; with that it proposes a **recipe**. **Recipes are confirmed by a person**, and only a confirmed recipe is applied automatically the next time. That is where the agent acts: the Apprentice wakes it with the recipe and the evidence, the agent runs it, and the Apprentice checks whether the alarm cleared.

### Human approval

Human approval didn't go away; it moved. Before, it sat on **every incident**: what's down, since when, the last log lines, you decide. That scales badly, and in practice it means a failure at three in the morning waits until eight.

Now it sits on **every kind of fix**: a person confirms once how a class of failure is fixed, and from then on it fixes itself, with verification and a retry limit. The blast radius of a hallucination is still bounded by what someone considered reversible, but the person stops being the bottleneck.

---

## Scheduled tasks, and the silent failure that was closed

### The problem

One of the tasks running on the local model watched disk space and **only replied if some disk crossed a threshold**. Under normal conditions it said nothing, and the runtime logged it as run successfully and silent.

That was the problem: **a silent result with status `ok` doesn't prove the tool was ever run.** A model that decides not to call the tool and replies "all good" directly produces exactly the same output as one that runs it and finds nothing to report. And the absence of a message, which is the signal that all is well, is also the signal that nothing was checked.

### How it was closed

Not by making the model always report, but by taking the task away from it. Disk monitoring is a deterministic monitor with its own heartbeat: if it stops running, the heartbeat goes stale and the alarm panel flags it. **Silence now means failure, not normality**, because something is actively waiting to hear from it.

What remains scheduled in the agent is what really needs a model: a news digest, maintenance of its own memory, and the periodic purge of deleted items in the knowledge base.

---

## Memory consolidation

### The problem

The agent accumulates notes every day: decisions made, errors solved, configuration changes. With no process organising them, within a few weeks there are thousands of notes and no way of knowing which ones matter. A full notebook with no index.

And a model's context is finite: you can't carry everything forever.

### How it works

It runs every night, in three phases:

**1. What happened.** It searches the day's conversations for facts, decisions and recurring topics. The heuristic is simple and surprisingly good: if something is mentioned several times, in different contexts, it probably matters.

**2. What's worth keeping.** It scores each candidate. Configurations, technical decisions, important paths and solved errors score high; greetings, trivial conversations and things already noted score low.

**3. Save.** Whatever passes the threshold is written to long-term memory, which is the first thing the agent reads when a conversation starts, and everything it did goes to a separate log.

The first version was a script with its own local model and its own search index; today the agent itself does it, following written instructions. It sends no message: its summary appears in the morning report, and if it doesn't run one night, the report says so.

### Why it was the most troublesome task

It is multi-tool by nature: it reads, evaluates, writes. It was the first to show the problem of the local model narrating instead of executing, and the reason that behaviour was investigated in depth.

The promotion threshold still has no empirical justification. It was chosen by eye, and how many promotions are correct has never been measured.

---

## Integration with the knowledge base

The agent queries the RAG through an MCP server: from Telegram with dedicated commands, and also internally when a question needs context that isn't in the conversation.

What matters from the agent's point of view is that corpus search is **just another tool**: the model decides when to call it. That means answer quality depends on two chained things (that the model decides to search, and that the search returns the right thing), and a failure in either produces the same symptom.

The retrieval engine is described in [The RAG](rag.md).

---

## What isn't evaluated

This is the section that makes the page honest.

**There is no quantitative evaluation of the agent.** There is no set of test cases, no tool-selection accuracy metric, and no measured comparison between models.

The conclusion about the 9B model (that it gets lost from the third or fourth chained call) came from observing failures, not from measuring a threshold. The real limit probably depends on the size of the accumulated context or the length of tool outputs. I don't know, because I didn't measure it before retiring it.

The memory consolidation threshold has no empirical backing at all.

What does exist is measurement **of the system around the agent**: how long each induced failure takes to detect and repair, and who repaired it (Docker, the system, remediation, the agent with a recipe, or a person). That tells when the agent is needed, but not how well it does its job when it is. Systematic evaluation of the agent itself (golden cases, reproducible comparisons) is still pending work, and until it exists, everything on this page is qualitative observation made with care.

Saying so is better than pretending otherwise.
