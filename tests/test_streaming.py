"""Capture/décodage concurrents : ordre, fin de phrase, annulation et surcharge."""

import importlib.util
from pathlib import Path
import threading
import unittest

from dictee.streaming import StreamingSession


class Recorder:
    sample_rate = 10
    limit_reached = False

    def __init__(self):
        self.lock = threading.Lock()
        self.buffer = []
        self.errors = ()
        self.recording = False
        self.started = threading.Event()
        self.closed = threading.Event()
        self.drained = threading.Event()
        self.stop_count = 0

    def start(self, device=None):
        self.recording = True
        self.started.set()

    def push(self, marker, count=10):
        with self.lock:
            self.buffer.extend([marker] * count)

    def drain(self):
        with self.lock:
            audio, self.buffer = self.buffer, []
        if audio:
            self.drained.set()
        return audio

    def stop(self):
        self.stop_count += 1
        self.recording = False
        self.closed.set()
        return self.drain()

    def cancel(self):
        self.recording = False
        with self.lock:
            self.buffer = []
        self.closed.set()


class ImmediateChunker:
    def __init__(self, sample_rate):
        pass

    def feed(self, audio):
        return [audio] if audio else []

    def flush(self):
        return []


class FlushChunker(ImmediateChunker):
    def __init__(self, sample_rate):
        self.pending = []

    def feed(self, audio):
        self.pending.extend(audio)
        return []

    def flush(self):
        audio, self.pending = self.pending, []
        return [audio] if audio else []


class Engine:
    def __init__(self):
        self.prepare_entered = threading.Event()
        self.decode_entered = threading.Event()
        self.prepare_gate = threading.Event()
        self.decode_gate = threading.Event()
        self.prepare_gate.set()
        self.decode_gate.set()
        self.transcriptions = []
        self.decode_error = None
        self.prepare_error = None

    def prepare(self, path, on_status=None):
        self.prepare_entered.set()
        if not self.prepare_gate.wait(3):
            raise RuntimeError("test prepare gate timeout")
        if self.prepare_error:
            raise RuntimeError(self.prepare_error)

    def transcribe(self, audio, path):
        self.decode_entered.set()
        if not self.decode_gate.wait(3):
            raise RuntimeError("test decode gate timeout")
        if self.decode_error:
            raise RuntimeError(self.decode_error)
        self.transcriptions.append(list(audio))
        return str(audio[0])


class Events:
    def __init__(self):
        self.condition = threading.Condition()
        self.items = []

    def emit(self, kind, value):
        with self.condition:
            self.items.append((kind, value))
            self.condition.notify_all()

    def wait(self, kind, timeout=2):
        with self.condition:
            if not self.condition.wait_for(lambda: any(k == kind for k, _ in self.items), timeout):
                raise AssertionError(f"Missing event {kind!r}; received {self.items!r}")
            return next(value for key, value in self.items if key == kind)

    def values(self, kind):
        with self.condition:
            return [value for key, value in self.items if key == kind]


class StreamingTests(unittest.TestCase):
    def make_session(self, sample_rate=10, **kwargs):
        self.engine, self.recorder, self.events = Engine(), Recorder(), Events()
        self.recorder.sample_rate = sample_rate
        kwargs.setdefault("chunker_factory", ImmediateChunker)
        self.session = StreamingSession(
            self.engine, self.recorder, self.events.emit, poll_interval=0.005, **kwargs,
        )
        self.addCleanup(self.cleanup_session)
        return self.session

    def cleanup_session(self):
        self.session.cancel()
        self.engine.prepare_gate.set()
        self.engine.decode_gate.set()
        self.assertTrue(self.session.wait(2), "worker leaked")

    def start(self):
        self.session.start(Path("models/tiny"))
        self.events.wait("started")

    def test_result_arrives_while_microphone_remains_open(self):
        self.make_session()
        self.start()
        self.recorder.push(1)
        self.assertEqual(self.events.wait("text"), "1")
        self.assertTrue(self.recorder.recording)
        self.assertTrue(self.session.active)
        self.session.stop()
        self.events.wait("done")
        self.assertFalse(self.session.active)
        self.assertEqual(self.events.values("text"), ["1"])

    def test_stop_flushes_final_phrase_once(self):
        self.make_session(chunker_factory=FlushChunker)
        self.start()
        self.recorder.push(2)
        self.assertTrue(self.recorder.drained.wait(1))
        self.assertEqual(self.events.values("text"), [])
        self.session.stop()
        self.events.wait("done")
        self.assertEqual(self.events.values("text"), ["2"])
        self.assertEqual(self.recorder.stop_count, 1)
        self.assertEqual(self.engine.transcriptions, [[2] * 10])

    def test_stop_closes_microphone_during_decode_and_preserves_fifo(self):
        self.make_session()
        self.engine.decode_gate.clear()
        self.start()
        self.recorder.push(1)
        self.assertTrue(self.engine.decode_entered.wait(1))
        self.recorder.drained.clear()
        self.recorder.push(2)
        self.assertTrue(self.recorder.drained.wait(1))
        self.session.stop()
        self.assertTrue(self.recorder.closed.wait(1))
        self.assertTrue(self.session.active)
        self.engine.decode_gate.set()
        self.events.wait("done")
        self.assertEqual(self.events.values("text"), ["1", "2"])

    def test_cancel_discards_inflight_result_and_queued_audio(self):
        self.make_session()
        self.engine.decode_gate.clear()
        self.start()
        self.recorder.push(1)
        self.assertTrue(self.engine.decode_entered.wait(1))
        self.recorder.push(2)
        self.session.cancel()
        self.assertTrue(self.recorder.closed.wait(1))
        self.engine.decode_gate.set()
        self.events.wait("cancelled")
        self.assertEqual(self.events.values("text"), [])
        self.assertEqual(self.events.values("done"), [])
        self.session.cancel()
        self.assertEqual(len(self.events.values("cancelled")), 1)

    def test_cancel_during_model_loading_does_not_open_microphone(self):
        self.make_session()
        self.engine.prepare_gate.clear()
        self.session.start(Path("models/tiny"))
        self.assertTrue(self.engine.prepare_entered.wait(1))
        self.session.cancel()
        self.engine.prepare_gate.set()
        self.events.wait("cancelled")
        self.assertFalse(self.recorder.started.is_set())
        self.assertEqual(self.events.values("started"), [])

    def test_stop_during_model_loading_finishes_without_opening_microphone(self):
        self.make_session()
        self.engine.prepare_gate.clear()
        self.session.start(Path("models/tiny"))
        self.assertTrue(self.engine.prepare_entered.wait(1))
        self.session.stop()
        self.engine.prepare_gate.set()
        self.events.wait("done")
        self.assertFalse(self.recorder.started.is_set())

    def test_overload_is_bounded_and_explicit_and_closes_microphone(self):
        self.make_session(max_pending_seconds=1.5)
        self.engine.decode_gate.clear()
        self.start()
        self.recorder.push(1)
        self.assertTrue(self.engine.decode_entered.wait(1))
        self.recorder.push(2)
        self.assertTrue(self.recorder.closed.wait(1))
        self.assertLessEqual(self.session.pending_seconds, 1.5)
        self.engine.decode_gate.set()
        self.assertIn("Tiny", self.events.wait("error"))
        self.assertEqual(self.events.values("text"), [])
        self.assertEqual(self.session.pending_seconds, 0)

    def test_inference_failure_closes_microphone_and_emits_one_error(self):
        self.make_session()
        self.engine.decode_error = "moteur simulé indisponible"
        self.start()
        self.recorder.push(1)
        self.assertEqual(self.events.wait("error"), "moteur simulé indisponible")
        self.assertTrue(self.recorder.closed.is_set())
        self.assertEqual(len(self.events.values("error")), 1)
        self.assertEqual(self.events.values("done"), [])

    def test_audio_failure_stops_capture_without_late_output(self):
        self.make_session()
        self.engine.decode_gate.clear()
        self.start()
        self.recorder.push(1)
        self.assertTrue(self.engine.decode_entered.wait(1))
        self.recorder.errors = ("input overflow",)
        self.assertTrue(self.recorder.closed.wait(1))
        self.engine.decode_gate.set()
        self.assertEqual(self.events.wait("error"), "input overflow")
        self.assertEqual(self.events.values("text"), [])

    def test_load_failure_never_opens_microphone(self):
        self.make_session()
        self.engine.prepare_error = "modèle absent"
        self.session.start(Path("models/tiny"))
        self.assertEqual(self.events.wait("error"), "modèle absent")
        self.assertFalse(self.recorder.started.is_set())

    def test_session_cannot_start_second_concurrent_worker(self):
        self.make_session()
        self.start()
        with self.assertRaisesRegex(RuntimeError, "déjà"):
            self.session.start(Path("models/tiny"))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy non installé")
    def test_real_pause_chunker_emits_before_stop_then_flushes_last_words(self):
        import numpy as np

        self.make_session(sample_rate=16000, chunker_factory=None)
        self.start()
        self.recorder.push(0.04, count=8000)
        self.recorder.push(0, count=12000)
        self.events.wait("text")
        self.assertTrue(self.recorder.recording)
        self.assertEqual(np.count_nonzero(self.engine.transcriptions[0]), 8000)
        self.recorder.push(0.07, count=4000)
        self.session.stop()
        self.events.wait("done")
        self.assertEqual(len(self.events.values("text")), 2)
        self.assertEqual(len(self.engine.transcriptions), 2)
        self.assertEqual(np.count_nonzero(self.engine.transcriptions[1]), 4000)
        self.session.stop()
        self.events.wait("done")
        with self.assertRaisesRegex(RuntimeError, "déjà"):
            self.session.start(Path("models/tiny"))


if __name__ == "__main__":
    unittest.main()
