"""The trace is append-only, or it is not evidence."""
import json
import pathlib
import tempfile
import unittest

from baton.errors import TraceCorrupt
from baton.trace import MemoryTrace, Trace


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.dir.name) / "run.jsonl"
        self.trace = Trace(self.path, trace_id="t1")

    def tearDown(self):
        self.dir.cleanup()

    def test_append_writes_one_json_line_per_record(self):
        self.trace.append({"event": "dispatch", "hop": 1})
        self.trace.append({"event": "decision", "hop": 1})
        lines = self.path.read_text().splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual("dispatch", json.loads(lines[0])["event"])

    def test_every_record_is_stamped_with_trace_id_seq_and_time(self):
        self.trace.append({"event": "dispatch"})
        rec = json.loads(self.path.read_text().splitlines()[0])
        self.assertEqual("t1", rec["trace_id"])
        self.assertEqual(0, rec["seq"])
        self.assertIn("ts", rec)

    def test_seq_increments(self):
        for _ in range(3):
            self.trace.append({"event": "x"})
        self.assertEqual([0, 1, 2], [r["seq"] for r in self.trace.records()])

    def test_earlier_lines_are_byte_identical_after_later_appends(self):
        self.trace.append({"event": "first"})
        before = self.path.read_bytes()
        self.trace.append({"event": "second"})
        after = self.path.read_bytes()
        self.assertTrue(after.startswith(before), "an earlier line was rewritten")

    def test_truncating_the_file_underneath_is_detected(self):
        self.trace.append({"event": "a"})
        self.trace.append({"event": "b"})
        self.path.write_text("")                    # somebody rewrote history
        with self.assertRaises(TraceCorrupt):
            self.trace.append({"event": "c"})

    def test_rewriting_a_line_underneath_is_detected(self):
        self.trace.append({"event": "a"})
        self.trace.append({"event": "b"})
        self.path.write_text(json.dumps({"event": "tampered"}) + "\n")
        with self.assertRaises(TraceCorrupt):
            self.trace.append({"event": "c"})

    def test_records_survive_a_new_reader(self):
        self.trace.append({"event": "a"})
        self.assertEqual(1, len(Trace(self.path, trace_id="t1").records()))

    def test_reopening_an_existing_trace_continues_the_sequence(self):
        self.trace.append({"event": "a"})
        again = Trace(self.path, trace_id="t1")
        again.append({"event": "b"})
        self.assertEqual([0, 1], [r["seq"] for r in again.records()])

    def test_nonserialisable_record_raises_before_touching_the_file(self):
        with self.assertRaises(TypeError):
            self.trace.append({"event": "x", "obj": object()})
        self.assertEqual(0, self.trace.count)
        self.assertFalse(self.path.exists() and self.path.read_text().strip())


class MemoryTraceTests(unittest.TestCase):
    def test_same_interface_no_file(self):
        t = MemoryTrace(trace_id="t9")
        t.append({"event": "a"})
        self.assertEqual(1, t.count)
        self.assertEqual("t9", t.records()[0]["trace_id"])


if __name__ == "__main__":
    unittest.main()
