"""
baton.crossover — should you route at all?

"Dynamic routing beats a static DAG" is false at zero path variance: when
every task needs the same agents in the same order, a fixed path is optimal
and every routing call is waste. The provable form of the claim is a
CROSSOVER, and it has a closed form with no fitted parameters.

Work in units of one routing call. Let rho be the cost of one specialist hop
in those units, `a` the probability a routed run succeeds (perception
accuracy), `E` the specialist hops a routed run makes, and `H` the routing
calls it makes. For a fixed path P, let p_P be the fraction of tasks whose
needs are a subset of P. Then, per SUCCESSFUL task:

    routed:        (H + E * rho) / a
    fixed path P:  |P| * rho / p_P

Routing beats P iff  rho > H * p_P / (|P| * a - E * p_P),  and never when
|P| * a <= E * p_P. The threshold rho* is the maximum over candidate fixed
paths: routing must beat the BEST fixed path, not a strawman.

Checked, not assumed: with H = 1 and candidates {modal path, whole pool} this
rule predicted the empirical cost-per-repaired winner in 30 of 30 cells of a
frozen 620-item sweep (five variance levels x six price ratios,
~/unlimited/pathvar/crossover.py). Every input is measurable in advance from
a task log plus one calibration pass, which is what makes this a pre-flight
rather than a benchmark.

    from baton import crossover
    c = crossover.estimate(paths=[{"date", "currency"}, {"email"}, ...],
                           perception_accuracy=0.88, price_ratio=3.6)
    print(c.render())

`perception_accuracy` has no default. The runtime never invents a number to
decide on — the same rule as `run(usd_per_call=...)`.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Optional, Sequence

# Above this pool size the exact search over every subset (2^K) is replaced by
# the greedy candidate set. 2^12 = 4096 candidates against a handful of
# distinct paths is trivial; 2^20 is not.
EXACT_SEARCH_MAX_POOL: int = 12

ROUTE = "route"
FIXED_PATH = "fixed_path"
UNDECIDED = "undecided"


@dataclass(frozen=True)
class StaticPath:
    """One candidate fixed path and what routing must beat in it."""

    path: frozenset[str]
    success: float               # p_P: fraction of tasks this path fully serves
    rho_star: float              # routing beats this path iff rho > rho_star
    cost_per_success: float      # |P| * rho / p_P at the given rho; nan without one

    @property
    def length(self) -> int:
        return len(self.path)


@dataclass(frozen=True)
class Crossover:
    """The answer to "should this workload be routed?", with its working."""

    rho_star: float              # max over candidates; inf means routing never pays
    verdict: str                 # ROUTE, FIXED_PATH, or UNDECIDED (no price given)
    best_static: StaticPath      # cheapest at the given price, else the binding one
    path_variance: float         # 1 - frequency of the modal path
    n_tasks: int
    pool_size: int
    perception_accuracy: float
    hops_per_routed_task: float
    routing_calls_per_task: float
    price_ratio: Optional[float]
    dynamic_cost_per_success: float   # (H + E*rho) / a at the given rho; nan without one
    exact_search: bool           # every subset considered, or the greedy candidates
    candidates: tuple[StaticPath, ...]

    def render(self) -> str:
        lines = [
            f"crossover over {self.n_tasks} tasks, pool of {self.pool_size}, "
            f"path variance {self.path_variance:.2f}",
            f"  perception accuracy a={self.perception_accuracy:.3f}  "
            f"hops/routed task E={self.hops_per_routed_task:.2f}  "
            f"routing calls/task H={self.routing_calls_per_task:.2f}",
            f"  rho* = {'inf (routing never pays here)' if math.isinf(self.rho_star) else f'{self.rho_star:.2f}'}"
            f"   binding fixed path: {sorted(self.best_static.path)} "
            f"(serves {self.best_static.success:.0%} of tasks)",
        ]
        if self.price_ratio is None:
            lines.append("  verdict: undecided — pass price_ratio (hop cost / routing-call "
                         "cost) to compare against rho*")
        else:
            lines.append(
                f"  at rho={self.price_ratio:.2f}: routed {self.dynamic_cost_per_success:.2f} "
                f"vs fixed {self.best_static.cost_per_success:.2f} per successful task"
                f"  ->  verdict: {self.verdict}")
        lines.append(f"  search: {'exact over every subset' if self.exact_search else 'greedy candidates'}")
        return "\n".join(lines)


def _threshold(size: int, success: float, a: float, e: float, h: float) -> float:
    denom = size * a - e * success
    if denom <= 0:
        return math.inf
    return h * success / denom


def _greedy_candidates(freq: Counter, pool: frozenset[str]) -> list[frozenset[str]]:
    """Cumulative unions of observed paths in frequency order, plus the pool.
    Contains the modal path and the whole pool, so it can never be worse than
    the two-candidate rule that was validated."""
    out: list[frozenset[str]] = []
    acc: frozenset[str] = frozenset()
    for path, _ in freq.most_common():
        acc = acc | path
        if acc not in out:
            out.append(acc)
    if pool not in out:
        out.append(pool)
    return out


def _exact_candidates(pool: frozenset[str]) -> list[frozenset[str]]:
    names = sorted(pool)
    return [frozenset(c) for k in range(1, len(names) + 1)
            for c in combinations(names, k)]


def estimate(paths: Iterable[Iterable[str]], perception_accuracy: float, *,
             price_ratio: Optional[float] = None,
             hops_per_routed_task: Optional[float] = None,
             routing_calls_per_task: float = 1.0,
             static_candidates: Optional[Sequence[Iterable[str]]] = None,
             exact_search: Optional[bool] = None) -> Crossover:
    """Where routing starts to pay for THIS workload.

    Args:
        paths: one entry per past task — the set of agents it actually needed.
            Any iterable of iterables of names; a `RunResult.path` with the
            gate removed is one such entry.
        perception_accuracy: P(a routed run succeeds). Measure it; never guess.
        price_ratio: cost of one specialist hop divided by the cost of one
            routing call. Embedded routing makes this large (the decision
            rides on the work call); a separate supervisor makes it ~1.
            Optional — without it the verdict is UNDECIDED but rho* is not.
        hops_per_routed_task: specialist invocations a routed run makes.
            Defaults to the mean path size, which is what a perfect router
            spends and is slightly pessimistic for a real one.
        routing_calls_per_task: model calls spent deciding, per task. One for
            perceive-once-then-route; about E + 1 for per-hop routing with a
            separate call per decision; well under 1 when the decision is a
            few tokens on the work call.
        static_candidates: restrict the fixed paths considered. Default: every
            subset of the pool when the pool has at most EXACT_SEARCH_MAX_POOL
            agents, else the greedy cumulative-union candidates.
        exact_search: force the exact or greedy candidate set.

    Raises:
        ValueError: an input the arithmetic cannot honestly use.
    """
    sets = [frozenset(p) for p in paths]
    if not sets:
        raise ValueError("paths is empty — nothing to route")
    if any(not s for s in sets):
        raise ValueError("every path must name at least one agent")
    if not 0 < perception_accuracy <= 1:
        raise ValueError("perception_accuracy must be in (0, 1]")
    if routing_calls_per_task <= 0:
        raise ValueError("routing_calls_per_task must be > 0")
    if price_ratio is not None and price_ratio <= 0:
        raise ValueError("price_ratio must be > 0")
    n = len(sets)
    e = (sum(len(s) for s in sets) / n if hops_per_routed_task is None
         else float(hops_per_routed_task))
    if e < 0:
        raise ValueError("hops_per_routed_task must be >= 0")
    a, h = float(perception_accuracy), float(routing_calls_per_task)

    freq = Counter(sets)
    pool = frozenset().union(*sets)
    modal, modal_count = freq.most_common(1)[0]
    variance = 1.0 - modal_count / n

    if static_candidates is not None:
        cands = [frozenset(c) for c in static_candidates]
        used_exact = False
    else:
        use_exact = (len(pool) <= EXACT_SEARCH_MAX_POOL if exact_search is None
                     else exact_search)
        cands = _exact_candidates(pool) if use_exact else _greedy_candidates(freq, pool)
        used_exact = use_exact
    if not cands or any(not c for c in cands):
        raise ValueError("static_candidates must be non-empty paths")

    scored: list[StaticPath] = []
    for c in cands:
        served = sum(cnt for s, cnt in freq.items() if s <= c)
        p = served / n
        cost = (len(c) * price_ratio / p if price_ratio is not None and p > 0
                else (math.inf if price_ratio is not None else math.nan))
        scored.append(StaticPath(path=c, success=p,
                                 rho_star=_threshold(len(c), p, a, e, h),
                                 cost_per_success=cost))

    rho_star = max(sp.rho_star for sp in scored)
    if price_ratio is None:
        best = max(scored, key=lambda sp: (sp.rho_star, -sp.length))
        dyn = math.nan
        verdict = UNDECIDED
    else:
        best = min(scored, key=lambda sp: (sp.cost_per_success, sp.length))
        dyn = (h + e * price_ratio) / a
        verdict = ROUTE if price_ratio > rho_star else FIXED_PATH

    return Crossover(rho_star=rho_star, verdict=verdict, best_static=best,
                     path_variance=variance, n_tasks=n, pool_size=len(pool),
                     perception_accuracy=a, hops_per_routed_task=e,
                     routing_calls_per_task=h, price_ratio=price_ratio,
                     dynamic_cost_per_success=dyn, exact_search=used_exact,
                     candidates=tuple(scored))
