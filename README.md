# baton

A small execution kernel for multi-agent systems where **the agents choose who runs next**.

Instead of a pre-baked DAG, each agent ends its output with one fenced `handoff`
block naming the next agent. The kernel makes that safe: legal-move enforcement,
a budget ceiling, a hop cap, a ping-pong detector, and a gate agent that is the
only role permitted to end a run.

- `kernel/` — zero pip dependencies, zero knowledge of any consumer.
- `adapters/company_os/` — the first consumer, wired to `claude -p` on a subscription.
- `bash verify.sh` — the gate. Deterministic, offline, $0.

Design and rejected alternatives:
`~/dev-notes/projects/baton/2026-08-15_baton-dynamic-handoff-kernel-design.md`
