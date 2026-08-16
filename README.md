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

## Using it

```python
from kernel import run
from adapters.company_os import registry, charter as ch, dispatch

charter = ch.charter_for("Build a docs Q&A bot", project_type="rag_chatbot",
                         acceptance_criteria=("answers cite sources",
                                              "p95 under 2s"))
agents = registry.load_agents(pool=charter.agent_pool)
result = run(charter, agents, dispatch.real_dispatch)
print(result.terminal_reason, result.path, result.spend_usd)
```

## The stop rules

Every run ends with exactly one of seven terminal reasons. There is no path out
of the loop that returns "it just stopped".

| Killer | Defence | Terminal reason |
|---|---|---|
| never-done | `max_hops`, then one forced gate verdict | `hops_exhausted` |
| budget spiral | pressure line at 80%, hard halt at 100% | `budget_exhausted` |
| ping-pong | stall notice, forced gate, then stop | `stalled` |
| endless rejection | 2 rejects per proposing agent | `reject_cap_reached` |
| unroutable output | 2 attempts, then the gate judges | `charter_violation` |
| transport failure | no retry — a timeout is not a parse error | `dispatch_failure` |
| — | the gate ratified | `ratified` |

Budget halts *without* a final gate call, unlike hops: a call made past the
ceiling would spend money the charter forbade.

## Testing

`bash verify.sh` — free, offline, deterministic. Tier 2 (`bench/replay.py
--confirm-spend`) costs real money and is never run by the gate.

Every guard in `kernel.runtime.Guards` can be switched off individually. That is
not a debug convenience: `tests/test_anti_vacuity.py` deletes each one alone and
asserts the behaviour changes, because two overlapping guards cover for each
other and leave a green suite that proves nothing.

Parser fixtures are real recorded agent output pulled from the sandbox corpus
(`tests/fixtures/extract_real_prose.py`), not hand-written JSON — a parser suite
built only from clean canned blocks passes while the parser is useless on what
models actually emit.
