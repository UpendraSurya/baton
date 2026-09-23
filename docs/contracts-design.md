# Deterministic contracts for agent output: design notes

> **Status:** a design proposal. Nothing in this file is implemented yet.
> Written 2026-09-23 on `mobile-branch` to capture a conversation for later.
> **Goal:** let the AI do the fuzzy work, and add a small deterministic layer
> that decides what is allowed to come out, so agent systems fail *loudly*
> instead of *silently*.

---

## 1. The problem, with a real example

An ETL pipeline built on the OpenAI Agents SDK uses an AI prompt to clean data.
One rule is: **the word `unlimited` must become `999`.** The AI step can
produce four things:

| AI output | right? | would anyone notice today? |
|---|---|---|
| `999` | ✅ | — |
| `X` | ❌ | maybe, later |
| `unlimited` (left unchanged) | ❌ | maybe |
| `null` | ❌ | often not, until a report breaks |

The last three are **silent failures**. It is the same failure baton's gate
already catches for fake completions ("I'm done" with nothing delivered), one
level down: at the level of individual data values.

---

## 2. The design: three layers, cheapest first

### Layer 1: don't ask the AI to do what code can do

`unlimited → 999` is a lookup, not a judgement.

- Handle every known case in plain code first: exact matches, case and
  whitespace variants (`Unlimited`, ` UNLIMITED `).
- Send the AI only the leftovers the rules could not handle.

The deterministic share is 100% correct and costs $0. The AI's error rate
applies only to the leftover cases. **This is where "twice as good as it looks"
comes from.**

### Layer 2: contracts the output must satisfy, whoever produced it

Checks written as code, applied to every output, whichever model or provider
produced it. For the example pipeline:

| contract | what it enforces | catches |
|---|---|---|
| `forbid(column, "unlimited", any_case=True)` | no forbidden value survives in any spelling | output left unchanged |
| `in_range(column, 0, 999, int)` / `one_of(column, {...})` | type and allowed range or set | `X` |
| `no_new_nulls(column)` | a non-null source never becomes null | `null` |
| `unchanged_except(column)` | every other column is byte-identical and row counts match | collateral edits |
| `maps(source="unlimited", target=999)` | every row whose source was `unlimited` now says exactly `999` | anything else in place of `999` |

### Layer 3: refuse loudly, never pass silently

- A failing row is **quarantined or retried**, with a named reason. For
  example: `row 4812: null introduced where source was 'unlimited'
  (no_new_nulls)`.
- This is baton's existing rule applied to data: every run ends **ratified**
  or **refused for a stated reason**, never "it just stopped."

---

## 3. The "range setter": a guaranteed envelope

| end | meaning | set by |
|---|---|---|
| **Floor** (worst-case degradation) | with contracts enforced, the worst outcome is *refused*, never *silently wrong* | what the contracts cover; not how good the AI is |
| **Ceiling** (best-case efficiency) | the share handled by deterministic rules (free, exact) plus the AI's first-try pass rate | rule coverage and model quality |

Reported per run, all measurable:

- the share of items handled by rules, and the share handled by the AI;
- the **contract refusal rate**, broken down by contract;
- the AI retry rate;
- **escapes**: errors that pass every contract. These can only be estimated,
  by sampling outputs and checking them by hand. This is the number that shows
  where to write the next contract.

**Limit to state honestly:** contracts only catch what is written down. A wrong
value that passes every rule (say `998` where the range allows it) still
escapes. That is why `maps(source, target)`, "this exact input must become this
exact output", matters most for mappings like this one.

---

## 4. How it would fit into baton

- **`baton.contracts`:** declarative checks (`forbid`, `in_range`, `one_of`,
  `no_new_nulls`, `unchanged_except`, `maps`). Each returns pass or fail with a
  reason naming the exact row and value. Zero dependencies, like the rest of
  baton.
- **A `normalize` step:** apply the rules before any AI call, and pass only the
  leftovers to the AI.
- **The gate runs the contracts before it may ratify**, alongside the existing
  `require_substance` and `require_coverage` checks, and refuses with a named
  reason.
- **A standalone `verify(output, source, contracts)`** callable from *any*
  framework (OpenAI Agents SDK, LangGraph, CrewAI) without adopting the rest of
  baton. This is the adoption wedge: people add it to what they already use,
  instead of switching.
- **A run report:** the floor and ceiling numbers above.

**First test to write:**

- the `unlimited → 999` case;
- a simulated AI that sometimes outputs `X`, `null`, or leaves the value
  unchanged;
- assert that every bad output is refused with the right named reason, and
  that the floor and ceiling are reported.

---

## 5. Provider variability, and what makes a pattern "OpenClaw-good"

### How big OpenClaw is

- **Stars:** reported at ~386,000 GitHub stars as of August 2026, and over
  210,000 in its first 10 days. That makes it one of the fastest-growing
  open-source projects ever.
- **What it is:** a self-hosted personal AI agent. It talks to you through the
  messaging apps you already use (WhatsApp, Telegram, Discord, email) and
  actually does things: shell commands, files, browser automation, scheduled
  tasks.
- **Architecture:** hub and spoke. A long-lived **Gateway** process (a WebSocket
  control plane) manages sessions, routes messages and dispatches tools.
  Clients (CLI, web UI, Mac app) and device "nodes" connect to it.
- **Providers:** model-agnostic, routing through Claude, GPT, Gemini, DeepSeek
  and others by cost, preference or access.

For scale, baton today is about 3,200 lines of core and provider code, with 45
test files and zero dependencies. That is a kernel, not a product.

### Why provider variability matters

Every provider behaves differently. They differ in:

- how reliably they follow a format;
- how they call tools;
- when they refuse;
- latency, price and context size.

baton has already measured this (`docs/measuring-dynamic-routing.md`):

| model | behaviour |
|---|---|
| `mistral-large` | one-shots every brief, so routing is never exercised |
| `mistral-small` | cannot satisfy the gate's contract; 98 of 120 runs died there |
| Claude | obeyed the contract cleanly |

**Same code, different provider, completely different outcome.**

### What makes a pattern robust across providers

1. **One narrow seam to every provider.** OpenClaw has its Gateway; baton has
   the `Dispatch` callable (`dispatch(agent, baton, prompt) -> DispatchResult`)
   plus `baton/providers/` (gemini, mistral, openai_compat, claude_cli). Adding
   a provider never touches the core.
2. **Never trust the provider's output shape.** Parse strictly, repair once,
   then refuse. baton's parse → `repair_nudge` → escalate ladder already does
   this for routing decisions.
3. **Put the contract at the boundary, not in the prompt.** Prompts vary in
   effect by provider; a deterministic check does not. With `baton.contracts`,
   **the floor is the same whichever provider runs.** A weaker or cheaper model
   only lowers the ceiling (more refusals, more retries). It never lowers the
   floor (silent errors). That turns provider choice into a pure cost and speed
   decision, which is exactly what a multi-provider system wants.
4. **Measure per provider.** Report the refusal rate, retry rate and escapes
   for each provider, so choosing a model is a decision made from data.

### What made OpenClaw big (it isn't the pattern alone)

- **An end-user product that is useful in minutes**, in apps people already
  use.
- **A visible "wow"**: it *does things*, and people could share that.
- **Model freedom**, so users weren't locked in.
- **Self-hosted**: your data and hardware.
- **Distribution**: the author's existing audience, and a community that formed
  fast.

baton's equivalent path is not to become a framework people switch to. It is:

1. `baton.contracts` + `verify()`, usable inside any framework, including
   OpenClaw-style agents. "Your agent can't silently corrupt data."
2. A public repo and a PyPI release.
3. One live, real-model result.
4. Possibly a small end-user product built *on* baton, if an OpenClaw-style
   audience is the goal.

---

## Sources (OpenClaw)

- [openclaw/openclaw on GitHub](https://github.com/openclaw/openclaw)
- [210,000 GitHub stars in 10 days: what OpenClaw's architecture teaches](https://medium.com/@Micheal-Lanham/210-000-github-stars-in-10-days-what-openclaws-architecture-teaches-us-about-building-personal-ai-dae040fab58f)
- [How OpenClaw works](https://bibek-poudel.medium.com/how-openclaw-works-understanding-ai-agents-through-a-real-architecture-5d59cc7a4764)
- [OpenClaw: the open-source AI agent taking over GitHub](https://toolpod.dev/blog/openclaw-open-source-ai-agent-guide)
