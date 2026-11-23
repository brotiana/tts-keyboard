"""Vérifie la capture bornée avec un microphone simulé, sans matériel."""

import importlib.util
import math
import unittest
from unittest.mock import patch

from dictee.audio import AudioRecorder, _resample_mono, _StreamingResampler


class FakeArray:
    def __init__(self, values):
        self.values = list(values)

    @property
    def size(self):
        return len(self.values)

    def __getitem__(self, key):
        if isinstance(key, tuple):
            key = key[0]
        return FakeArray(self.values[key])

    def __mul__(self, other):
        return FakeArray(a * b for a, b in zip(self.values, other.values))

    def copy(self):
        return FakeArray(self.values)


class FakeNumpy:
    float32 = "float32"

    @staticmethod
    def asarray(data, dtype=None):
        return data if isinstance(data, FakeArray) else FakeArray(data)

    sqrt = staticmethod(math.sqrt)
    mean = staticmethod(lambda data: sum(data.values) / data.size)
    concatenate = staticmethod(lambda chunks: FakeArray(value for chunk in chunks for value in chunk.values))
    empty = staticmethod(lambda count, dtype=None: FakeArray([]))


class FakeSoundDevice:
    def __init__(self, supports_16k=True):
        self.supports_16k = supports_16k
        self.stream = None
        self.settings = []

    def query_devices(self, *args):
        info = {"name": "Micro simulé", "max_input_channels": 1, "default_samplerate": 48000}
        return info if args else [info, {"name": "Sortie", "max_input_channels": 0}]

    def check_input_settings(self, **kwargs):
        self.settings.append(kwargs)
        if kwargs["samplerate"] == 16000 and not self.supports_16k:
            raise RuntimeError("unsupported rate")

    def InputStream(self, **kwargs):
        self.stream = FakeStream(kwargs)
        return self.stream


class FakeStream:
    def __init__(self, settings):
        self.settings = settings
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class AudioCaptureTests(unittest.TestCase):
    def setUp(self):
        self.sd = FakeSoundDevice()
        self.dependencies = patch("dictee.audio._dependencies", return_value=(FakeNumpy(), self.sd))
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)

    def test_output_only_devices_are_not_listed(self):
        self.assertEqual(AudioRecorder.list_devices(), [(0, "Micro simulé")])

    def test_capture_is_bounded_and_further_callbacks_are_ignored(self):
        recorder = AudioRecorder(max_seconds=0.001)
        recorder.start()
        recorder._callback(FakeArray([0.5] * 12), 12, None, None)
        recorder._callback(FakeArray([0.5] * 12), 12, None, None)
        recorder._callback(FakeArray([0.5] * 12), 12, None, None)
        self.assertEqual(recorder._frames, 16)
        self.assertEqual(sum(chunk.size for chunk in recorder._chunks), 16)
        self.assertTrue(recorder.limit_reached)
        self.assertAlmostEqual(recorder.elapsed, 0.001)
        self.assertAlmostEqual(recorder.level, 0.5)
        with patch("dictee.audio._resample_mono", side_effect=lambda audio, source, target: audio):
            self.assertEqual(recorder.stop().size, 16)
        self.assertFalse(recorder.recording)
        self.assertTrue(self.sd.stream.closed)
        self.assertEqual(recorder._chunks, [])

    def test_input_overflow_is_reported_instead_of_returning_truncated_audio(self):
        recorder = AudioRecorder()
        recorder.start()
        recorder._callback(FakeArray([0.1] * 16), 16, None, "input overflow")
        self.assertTrue(recorder.errors)
        with self.assertRaisesRegex(RuntimeError, "overflow"):
            recorder.stop()
        self.assertFalse(recorder.recording)
        self.assertTrue(self.sd.stream.closed)

    def test_falls_back_to_device_rate_and_resamples_on_stop(self):
        self.sd.supports_16k = False
        recorder = AudioRecorder()
        recorder.start(device=0)
        self.assertEqual(self.sd.stream.settings["samplerate"], 48000)
        recorder._callback(FakeArray([0.1] * 48), 48, None, None)
        with patch("dictee.audio._resample_mono", return_value="resampled") as resample:
            self.assertEqual(recorder.stop(), "resampled")
        self.assertEqual(resample.call_args.args[1:], (48000, 16000))

    def test_cancel_releases_microphone_and_discards_audio(self):
        recorder = AudioRecorder()
        recorder.start()
        recorder._callback(FakeArray([0.1] * 10), 10, None, None)
        recorder.cancel()
        self.assertFalse(recorder.recording)
        self.assertEqual(recorder._chunks, [])
        self.assertTrue(self.sd.stream.stopped)
        self.assertTrue(self.sd.stream.closed)

    def test_capture_exception_does_not_escape_audio_callback(self):
        recorder = AudioRecorder()
        recorder.start()
        recorder._callback(None, 10, None, None)
        self.assertTrue(recorder.errors)
        with self.assertRaisesRegex(RuntimeError, "capture audio"):
            recorder.stop()

    def test_drain_leaves_microphone_open_and_returns_each_sample_once(self):
        recorder = AudioRecorder(max_seconds=None)
        recorder.start()
        recorder._callback(FakeArray([0.1] * 10), 10, None, None)
        with patch("dictee.audio._resample_mono", side_effect=lambda audio, source, target: audio):
            first = recorder.drain()
            self.assertEqual(first.values, [0.1] * 10)
            self.assertEqual(recorder.drain().size, 0)
            self.assertTrue(recorder.recording)
            self.assertFalse(self.sd.stream.stopped)
            self.assertEqual(recorder._buffer_frames, 0)
            recorder._callback(FakeArray([0.2] * 5), 5, None, None)
            self.assertEqual(recorder.stop().values, [0.2] * 5)
        self.assertAlmostEqual(recorder.elapsed, 15 / 16000)
        self.assertTrue(self.sd.stream.closed)

    def test_unlimited_recording_can_outlive_buffer_when_regularly_drained(self):
        recorder = AudioRecorder(max_seconds=None, max_buffer_seconds=0.002)
        recorder.start()
        with patch("dictee.audio._resample_mono", side_effect=lambda audio, source, target: audio):
            for _ in range(100):
                recorder._callback(FakeArray([0.1] * 20), 20, None, None)
                self.assertEqual(recorder.drain().size, 20)
        self.assertEqual(recorder._frames, 2000)
        self.assertFalse(recorder.limit_reached)
        self.assertEqual(recorder.errors, ())
        self.assertTrue(recorder.recording)
        recorder.cancel()

    def test_undrained_backlog_stops_capture_and_reports_overflow(self):
        recorder = AudioRecorder(max_seconds=None, max_buffer_seconds=0.002)
        recorder.start()
        recorder._callback(FakeArray([0.1] * 20), 20, None, None)
        recorder._callback(FakeArray([0.1] * 20), 20, None, None)
        recorder._callback(FakeArray([0.1] * 20), 20, None, None)
        self.assertEqual(recorder._buffer_frames, 20)
        self.assertFalse(recorder._accepting)
        self.assertEqual(len(recorder.errors), 1)
        with self.assertRaisesRegex(RuntimeError, "tampon audio est plein"):
            recorder.drain()
        self.assertEqual(recorder._buffer_frames, 0)
        recorder.cancel()

    def test_finite_capture_limit_counts_drained_samples_too(self):
        recorder = AudioRecorder(max_seconds=0.002)
        recorder.start()
        recorder._callback(FakeArray([0.1] * 20), 20, None, None)
        with patch("dictee.audio._resample_mono", side_effect=lambda audio, source, target: audio):
            self.assertEqual(recorder.drain().size, 20)
            recorder._callback(FakeArray([0.1] * 20), 20, None, None)
            self.assertEqual(recorder.stop().size, 12)
        self.assertEqual(recorder._frames, 32)
        self.assertTrue(recorder.limit_reached)


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy non installé : test numérique différé")
class RealResamplingTests(unittest.TestCase):
    def test_tiny_fragment_resampling_matches_final_duration_at_all_boundaries(self):
        import numpy as np

        for rate in (8000, 44100, 48000):
            for length in range(1, 65):
                source = np.linspace(-.2, .2, length, dtype=np.float32)
                converter = _StreamingResampler(rate, 16000)
                pieces = [converter.convert(source[i:i + 1]) for i in range(length)]
                pieces.append(converter.convert(np.empty(0, dtype=np.float32), final=True))
                actual = np.concatenate(pieces)
                expected = _resample_mono(source, rate, 16000)
                self.assertEqual(actual.size, expected.size, (rate, length))
                np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_streaming_44100_conversion_preserves_one_continuous_time_axis(self):
        import numpy as np

        source = np.sin(2 * np.pi * 440 * np.arange(44100 + 79) / 44100).astype(np.float32)
        converter = _StreamingResampler(44100, 16000)
        pieces = []
        for offset in range(0, source.size, 317):
            pieces.append(converter.convert(source[offset:offset + 317]))
        pieces.append(converter.convert(np.empty(0, dtype=np.float32), final=True))
        actual = np.concatenate(pieces)
        expected = _resample_mono(source, 44100, 16000)
        self.assertEqual(actual.size, expected.size)
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_recorder_drain_at_device_rate_does_not_repeat_boundary_samples(self):
        import numpy as np

        sound = FakeSoundDevice(supports_16k=False)
        source = np.linspace(-.1, .1, 10031, dtype=np.float32)
        with patch("dictee.audio._dependencies", return_value=(np, sound)):
            recorder = AudioRecorder(max_seconds=None)
            recorder.start()
            pieces = []
            for offset in range(0, source.size, 319):
                part = source[offset:offset + 319]
                recorder._callback(part[:, None], part.size, None, None)
                pieces.append(recorder.drain())
            pieces.append(recorder.stop())
        actual = np.concatenate(pieces)
        expected = _resample_mono(source, 48000, 16000)
        self.assertEqual(actual.size, expected.size)
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_preserves_duration_level_and_dtype(self):
        import numpy as np

        audio = np.ones(48000, dtype=np.float32) * 0.25
        result = _resample_mono(audio, 48000, 16000)
        self.assertEqual(result.size, 16000)
        self.assertEqual(result.dtype, np.float32)
        np.testing.assert_allclose(result, 0.25)

    def test_44100_hz_tone_is_resampled_on_correct_time_axis(self):
        import numpy as np

        audio = np.sin(2 * np.pi * 440 * np.arange(4410) / 44100).astype(np.float32)
        result = _resample_mono(audio, 44100, 16000)
        expected = np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)
        self.assertEqual(result.size, 1600)
        np.testing.assert_allclose(result, expected, atol=0.001)


if __name__ == "__main__":
    unittest.main()
