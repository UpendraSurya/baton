"""
baton.trace — the append-only record of a run.

A dynamic route only becomes debuggable (and, later, learnable) if every hop is
on disk. Append-only is CHECKED, not assumed: before each write the file is
re-counted, so a truncation or an edit underneath us fails loudly instead of
producing a tidy trace that agrees with whatever happened.
"""
import json
import os
import time

from baton.errors import TraceCorrupt


class Trace:
    def __init__(self, path, trace_id):
        self.path = str(path)
        self.trace_id = trace_id
        self._seq = self._existing_lines()

    # -- internals ------------------------------------------------------------
    def _existing_lines(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except FileNotFoundError:
            return 0

    # -- api ------------------------------------------------------------------
    @property
    def count(self):
        return self._seq

    def append(self, record):
        """Stamp, serialise, then write. Serialising first means a bad record
        raises without leaving a half-line on disk."""
        stamped = dict(record)
        stamped.update(trace_id=self.trace_id, seq=self._seq,
                       ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        line = json.dumps(stamped, ensure_ascii=False) + "\n"   # raises TypeError first

        on_disk = self._existing_lines()
        if on_disk != self._seq:
            raise TraceCorrupt(
                f"{self.path}: expected {self._seq} lines, found {on_disk} — "
                "the trace was rewritten underneath the run")

        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self._seq += 1

    def records(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return [json.loads(ln) for ln in fh if ln.strip()]
        except FileNotFoundError:
            return []


class MemoryTrace:
    """Same interface, no file. For tests and dry runs that do not need durability."""

    def __init__(self, trace_id="mem"):
        self.trace_id = trace_id
        self._records = []

    @property
    def count(self):
        return len(self._records)

    def append(self, record):
        stamped = dict(record)
        stamped.update(trace_id=self.trace_id, seq=len(self._records),
                       ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        json.dumps(stamped)                     # same TypeError contract as Trace
        self._records.append(stamped)

    def records(self):
        return list(self._records)
