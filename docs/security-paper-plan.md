# Security paper (Paper 2) — audit + plan

Separate research paper, separate co-author, same codebase. Claim: injection
attacks propagate across agent handoffs in existing frameworks, and gated
routing with provenance tracking / privilege separation / verification gates
reduces propagation at acceptable cost and task performance. This document
is the audit against the paper's ten requirements plus the prioritized plan
to close the gaps. Nothing here is implemented — plan only.

## Gap report

1. **Threat model — missing.** The closest artifact is the OWASP ASI01–ASI10
   table in `README.md:331-344`, a posture summary, not a threat model: no
   explicit attacker-capability list, entry points, or attack-success
   criteria. It also documents that this paper's three vectors sit outside
   baton's current scope: ASI02 Tool Misuse is marked N/A ("baton brokers no
   tools itself"), and ASI07 Insecure Inter-Agent Comms is "N/A today"
   (the one shipped adapter is an in-process call, not a network protocol).
2. **Attack suite — missing.** `tests/test_adversarial.py` (4,000 randomized
   runs) fuzzes malformed *routing* behavior (garbage output, illegal
   targets, hallucinated names, ping-pong, missing evidence) — a robustness
   fuzzer, not a semantic-injection fuzzer. It never tests whether
   attacker-controlled *content* can manipulate a downstream agent. No
   canary-payload harness exists.
3. **Propagation measurement — missing, substrate exists.** `Trace` /
   `MemoryTrace` (`src/baton/trace.py`) and every `dispatch`/`decision`
   event in `runtime.py` already record agent, hop, and full decision
   content. Nothing today tags content as attacker-controlled or checks for
   an "unauthorized action" — `ArtifactRef`/`Baton` in `src/baton/packet.py`
   carry no provenance or trust-level field.
4. **Metrics — missing.** No attack success rate, propagation depth,
   unauthorized-action rate, utility-under-attack, or defense-overhead
   metric exists.
5. **Baselines against other frameworks — missing.** Same gap as Paper 1: no
   LangGraph/CrewAI/AutoGen integration exists in this repo.
6. **Defense ablations — partially transferable, not built.** `Guards`
   (`runtime.py:68-111`) already gives per-defense toggling, and two
   existing guards already behave like defenses even though they weren't
   designed as such: `validate_target`/`render_legal_moves` (whitelist
   enforcement — privilege separation against a compromised hop naming an
   illegal target) and `require_coverage`/`require_artifacts`
   (evidence-backed completion — a verification gate against a forged
   claim). Provenance tracking as a togglable defense does not exist at
   all — net-new work.
7. **Multiple models — partially done.** Same situation as Paper 1:
   providers are swappable by construction, no declarative multi-model
   experiment config.
8. **Repeatability — missing.** No `seed` parameter in any provider, no
   repeated-run/variance methodology.
9. **Reproducibility — missing.** No experiment runner for this paper
   exists yet.
10. **Adaptive attacks — missing.** Nothing to adapt against yet, since the
    attack suite itself doesn't exist.

### Overlap risk with the `paper-ready` work
Real, manageable if disclosed. Both papers would reuse the framework-adapter
layer, the experiment-config schema, the seeded-run harness, and the
reproduce-everything script — sharing that infrastructure is fine and
common in systems research. Both also rest on the same underlying baton
mechanism (mechanical gate + whitelist enforcement), read as two different
claims: Paper 1 frames it as false-completion-claim prevention, Paper 2 as
privilege separation / propagation limiting. Keep the two legitimate by
using different task/attack corpora, different dependent variables, and by
having each paper cite the other and name what infrastructure is shared.
Silent reuse of the same corpus or run logs across both would read as
double-counting a result.

### Responsible disclosure
Nothing to disclose yet — no attack suite has been built or run. Standing
requirement for when it is: canary-only, sandbox-only payloads (already
specified), and any incidental finding of a real, currently-exploitable
injection path in a third-party framework goes through that framework's
disclosure process before anything about it is published.

## Prioritized plan

**P0 — foundations, shared with Paper 1 where possible**
1. Write the explicit threat model doc (attacker capabilities, the three
   entry points, attack-success criteria) — pure writing, do it first.
2. Add a provenance / trust-level field to `ArtifactRef`/`Baton`
   (e.g. `origin: "agent" | "tool" | "connector"`) so injected content can
   be tracked as it travels. The one core-library change this paper needs —
   touches `src/baton/packet.py` and needs explicit sign-off before it's
   written.
3. Build the canary-only attack-scenario harness (tool-output,
   agent-message, connector-response injection), sandboxed, reusing the
   shared experiment-config/runner infrastructure from Paper 1's plan where
   it overlaps (framework adapters, seeded repeated runs, one-command
   reproduce).

**P0 — measurement**
4. Propagation-depth and unauthorized-action tracking built on top of
   `Trace`, using the new provenance field.
5. Metrics module: attack success rate, propagation depth,
   unauthorized-action rate, utility-under-attack, defense overhead.

**P1**
6. Baselines: the same framework adapters as Paper 1 (shared code), same
   attack suite run through them.
7. Defense ablations: extend `Guards` (or a parallel security-guards
   dataclass) so provenance-checking, privilege separation, and
   verification-gating can each be toggled independently.
8. Adaptive attacks designed with knowledge of the shipped defenses, run
   only after the baseline defenses are locked.

**P1 — reproducibility**
9. Seeded, repeated-run harness with mean/variance (shared with Paper 1).
10. One-command pipeline regenerating all Paper 2 tables/figures from raw
    logs, kept in a directory separate from Paper 1's experiment code and
    results.

Waiting for approval before touching git further or writing any code.
