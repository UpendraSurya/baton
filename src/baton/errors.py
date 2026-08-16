"""
baton.errors — every way a run can go wrong, named.

One base class so a consumer can catch everything with one except clause, and
five specific ones so the runtime's validation ladder can tell "the model wrote
gibberish" apart from "the model named an agent it is not allowed to reach".
"""


class BatonError(Exception):
    """Base for every error this kernel raises."""


class ParseFailure(BatonError):
    """No usable routing block was found in the agent's output."""


class IllegalTarget(BatonError):
    """The named next agent is outside can_hand_to ∩ charter.agent_pool."""


class RoleViolation(BatonError):
    """A worker used a gate-only verb, or the gate used a worker-only verb."""


class CharterInvalid(BatonError):
    """The pre-flight envelope does not describe a runnable run."""


class TraceCorrupt(BatonError):
    """The trace file changed underneath us — append-only was violated."""
