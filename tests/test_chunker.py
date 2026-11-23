"""Le découpage en direct préserve les phrases sans dépendre des blocs du micro."""
import importlib.util
import unittest

from dictee.audio import SpeechChunker


@unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy non installé")
class SpeechChunkerTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.rate = 16000

    def signal(self, seconds, level):
        return self.np.full(round(seconds * self.rate), level, dtype=self.np.float32)

    def test_continuous_speech_waits_for_silence_even_after_thirty_seconds(self):
        chunker = SpeechChunker()
        audio = self.signal(35, .1)
        self.assertEqual(chunker.feed(audio), [])
        self.assertEqual(chunker.feed(self.signal(.3, 0)), [])
        output = chunker.feed(self.signal(.4, 0))
        self.assertEqual(len(output), 1)
        self.np.testing.assert_array_equal(output[0][:audio.size], audio)
        self.assertEqual(chunker.flush(), [])

    def test_pause_emits_before_recording_is_stopped(self):
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed(self.signal(0.2, 0)), [])
        self.assertEqual(chunker.feed(self.signal(0.5, 0.1)), [])
        output = chunker.feed(self.signal(0.7, 0))
        self.assertEqual(len(output), 1)
        self.assertEqual(self.np.count_nonzero(output[0]), 8000)
        self.assertEqual(output[0].size, 14400)  # 0.2 s avant + 0.5 parole + 0.2 après
        self.assertEqual(chunker.flush(), [])

    def test_explicit_timed_mode_is_still_available(self):
        chunker = SpeechChunker(max_seconds=4)
        audio = self.np.linspace(0.1, 0.8, 9 * self.rate + 127, dtype=self.np.float32)
        output = chunker.feed(audio)
        self.assertEqual([part.size for part in output], [64000, 64000])
        output += chunker.flush()
        self.np.testing.assert_array_equal(self.np.concatenate(output), audio)
        self.assertTrue(all(part.size <= 64000 for part in output))
        self.assertEqual(chunker.flush(), [])

    def test_arbitrary_input_block_boundaries_do_not_change_results(self):
        audio = self.np.concatenate((self.signal(.37, 0), self.signal(.51, .1),
                                    self.signal(.8, 0), self.signal(4.12, .2), self.signal(.8, 0)))
        whole = SpeechChunker()
        expected = whole.feed(audio) + whole.flush()
        fragmented = SpeechChunker()
        actual = []
        offset = 0
        for size in [1, 317, 600, 2, 1000, 301] * 100:
            if offset >= audio.size:
                break
            actual += fragmented.feed(audio[offset:offset + size])
            offset += size
        actual += fragmented.feed(audio[offset:])
        actual += fragmented.flush()
        self.assertEqual(len(actual), len(expected))
        for first, second in zip(actual, expected):
            self.np.testing.assert_array_equal(first, second)

    def test_two_phrases_are_sent_once_with_no_speech_overlap(self):
        audio = self.np.concatenate((self.signal(.5, .1), self.signal(.8, 0),
                                    self.signal(.3, .2), self.signal(.8, 0)))
        chunker = SpeechChunker()
        output = chunker.feed(audio) + chunker.flush()
        self.assertEqual(len(output), 2)
        sent = self.np.concatenate(output)
        self.np.testing.assert_array_equal(sent[sent != 0], audio[audio != 0])

    def test_quiet_microphone_never_builds_an_unbounded_phrase(self):
        chunker = SpeechChunker()
        for _ in range(100):
            self.assertEqual(chunker.feed(self.signal(.5, .002)), [])
            self.assertLessEqual(chunker._pre_size, 3200)
            self.assertLess(chunker._pending.size, 320)
            self.assertFalse(chunker._parts)
        self.assertEqual(chunker.flush(), [])

    def test_short_click_is_not_transcribed(self):
        chunker = SpeechChunker()
        self.assertEqual(chunker.feed(self.signal(.08, .5)), [])
        self.assertEqual(chunker.feed(self.signal(.8, 0)), [])
        self.assertEqual(chunker.flush(), [])

    def test_short_final_speech_after_forced_cut_is_preserved(self):
        chunker = SpeechChunker(max_seconds=4)
        audio = self.signal(4.07, .1)
        output = chunker.feed(audio) + chunker.flush()
        self.assertEqual(len(output), 2)
        self.np.testing.assert_array_equal(self.np.concatenate(output), audio)

    def test_flush_keeps_last_incomplete_twenty_millisecond_window_once(self):
        chunker = SpeechChunker()
        audio = self.np.linspace(.1, .2, 3200 + 127, dtype=self.np.float32)
        self.assertEqual(chunker.feed(audio), [])
        output = chunker.flush()
        self.assertEqual(len(output), 1)
        self.np.testing.assert_array_equal(output[0], audio)
        self.assertEqual(chunker.flush(), [])

    def test_forced_cut_can_split_a_window_without_losing_samples(self):
        chunker = SpeechChunker(max_seconds=.251, pre_roll_seconds=.1)
        audio = self.np.linspace(.1, .3, 10000, dtype=self.np.float32)
        output = chunker.feed(audio) + chunker.flush()
        self.assertTrue(all(part.size <= round(.251 * self.rate) for part in output))
        self.np.testing.assert_array_equal(self.np.concatenate(output), audio)

    def test_invalid_audio_is_rejected(self):
        chunker = SpeechChunker()
        with self.assertRaisesRegex(ValueError, "mono"):
            chunker.feed(self.np.zeros((10, 2)))
        with self.assertRaisesRegex(ValueError, "finies"):
            chunker.feed(self.np.array([self.np.nan]))


if __name__ == "__main__":
    unittest.main()
