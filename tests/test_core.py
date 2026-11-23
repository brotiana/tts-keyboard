"""Contrats hors ligne et découverte, sans téléchargement ni dépendances lourdes."""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dictee.core import Engine, discover_models, validate_model


def write_model(root: Path, name: str = "base") -> Path:
    path = root / name
    path.mkdir()
    (path / "config.json").write_text(json.dumps({"is_multilingual": True}))
    (path / "tokenizer.json").write_text("{}")
    (path / "vocabulary.json").write_text('["bonjour"]')
    (path / "model.bin").write_bytes(b"binary-model-placeholder")
    return path


class ModelValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_missing_model_is_reported_without_creating_directory(self):
        models = discover_models(self.root / "absent")
        self.assertEqual([model.key for model in models], ["tiny", "base", "small", "medium"])
        self.assertTrue(all(not model.ready for model in models))
        self.assertFalse((self.root / "absent").exists())

    def test_alias_and_custom_models_are_discovered(self):
        alias = write_model(self.root, "faster-whisper-base")
        write_model(self.root, "mon-modele")
        models = discover_models(self.root)
        base = next(model for model in models if model.key == "base")
        self.assertTrue(base.ready)
        self.assertEqual(base.path, alias)
        self.assertEqual(len(models), 5)
        self.assertTrue(next(model for model in models if model.key == "mon-modele").ready)

    def test_valid_alias_is_used_when_canonical_directory_is_incomplete(self):
        (self.root / "base").mkdir()
        alias = write_model(self.root, "faster-whisper-base")
        self.assertEqual(discover_models(self.root)[1].path, alias)

    def test_lfs_pointer_and_incomplete_files_are_rejected(self):
        model = write_model(self.root)
        (model / "model.bin").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        with self.assertRaisesRegex(ValueError, "Git LFS"):
            validate_model(model)
        (model / "model.bin").unlink()
        with self.assertRaisesRegex(ValueError, "model.bin"):
            validate_model(model)

    def test_invalid_json_and_english_only_models_are_rejected(self):
        model = write_model(self.root)
        (model / "tokenizer.json").write_text("not-json")
        with self.assertRaisesRegex(ValueError, "JSON"):
            validate_model(model)
        (model / "tokenizer.json").write_text("{}")
        (model / "config.json").write_text('{"is_multilingual": false}')
        with self.assertRaisesRegex(ValueError, "anglais"):
            validate_model(model)
        english = write_model(self.root, "base.en")
        with self.assertRaisesRegex(ValueError, "anglais"):
            validate_model(english)

    def test_text_vocabulary_is_supported(self):
        model = write_model(self.root)
        (model / "vocabulary.json").unlink()
        (model / "vocabulary.txt").write_text("bonjour\n")
        validate_model(model)


class EngineOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = write_model(self.root)
        self.samples = SimpleNamespace(ndim=1, size=16000)
        self.numpy = SimpleNamespace(
            asarray=lambda audio, dtype: audio,
            float32="float32",
            isfinite=lambda audio: SimpleNamespace(all=lambda: True),
        )
        self.loaded_models = []
        self.load_calls = []
        self.engine = Engine()

        def factory(*args, **kwargs):
            # The previous model must have been released before loading another.
            self.assertIsNone(self.engine._model)
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            model = SimpleNamespace(supported_languages=["fr", "en"])
            model.transcribe = Mock(return_value=(
                iter([SimpleNamespace(text=" Bonjour "), SimpleNamespace(text=" le monde. ")]), None,
            ))
            self.loaded_models.append(model)
            self.load_calls.append((args, kwargs))
            return model

        self.modules = patch.dict(sys.modules, {
            "numpy": self.numpy,
            "faster_whisper": SimpleNamespace(WhisperModel=factory),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_local_files_cpu_int8_and_french_are_enforced(self):
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}):
            result = self.engine.transcribe(self.samples, self.model)
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(result, "Bonjour le monde.")
        args, kwargs = self.load_calls[0]
        self.assertEqual(args, (str(self.model.resolve()),))
        self.assertEqual(kwargs, dict(device="cpu", compute_type="int8", cpu_threads=4, num_workers=1, local_files_only=True))
        settings = self.loaded_models[0].transcribe.call_args.kwargs
        self.assertEqual(settings["language"], "fr")
        self.assertEqual(settings["task"], "transcribe")
        self.assertTrue(settings["vad_filter"])
        self.assertFalse(settings["condition_on_previous_text"])

    def test_switching_models_unloads_previous_model(self):
        self.engine.transcribe(self.samples, self.model)
        tiny = write_model(self.root, "tiny")
        self.engine.transcribe(self.samples, tiny)
        self.assertEqual(len(self.load_calls), 2)
        self.assertEqual(self.engine._model_path, tiny)
        self.engine.unload()
        self.assertIsNone(self.engine._model)

    def test_missing_model_never_invokes_model_loader(self):
        with self.assertRaises(ValueError):
            self.engine.transcribe(self.samples, self.root / "missing")
        self.assertEqual(self.load_calls, [])

    def test_generator_failure_is_reported_and_model_discarded(self):
        self.engine.transcribe(self.samples, self.model)

        def broken_segments():
            yield SimpleNamespace(text="début")
            raise RuntimeError("fake backend failure")

        self.loaded_models[0].transcribe.return_value = (broken_segments(), None)
        with self.assertRaisesRegex(RuntimeError, "fake backend failure"):
            self.engine.transcribe(self.samples, self.model)
        self.assertIsNone(self.engine._model)

    def test_model_is_reused_for_successive_dictations(self):
        self.engine.transcribe(self.samples, self.model)
        self.loaded_models[0].transcribe.return_value = (iter([SimpleNamespace(text="Encore.")]), None)
        self.assertEqual(self.engine.transcribe(self.samples, self.model), "Encore.")
        self.assertEqual(len(self.load_calls), 1)

    def test_renamed_english_model_is_rejected_after_loading(self):
        english = SimpleNamespace(supported_languages=["en"], transcribe=Mock())
        with patch("faster_whisper.WhisperModel", return_value=english):
            with self.assertRaisesRegex(ValueError, "français"):
                self.engine.transcribe(self.samples, self.model)
        english.transcribe.assert_not_called()
        self.assertIsNone(self.engine._model)


if __name__ == "__main__":
    unittest.main()
