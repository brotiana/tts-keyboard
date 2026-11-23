"""Vérifications hors ligne des téléchargements, sans connexion ni modèle réel."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import preparer_hors_ligne as wheels
import telecharger_modeles as downloads


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.destination = self.root / "config.json"
        self.partial = self.root / "config.json.part"
        self.metadata = self.root / "config.json.part.json"
        self.url = "https://example.invalid/config.json"
        self.body = b'{"test": "complete"}'
        self.output = io.StringIO()
        self.silence = contextlib.redirect_stdout(self.output)
        self.silence.__enter__()

    def tearDown(self):
        self.silence.__exit__(None, None, None)
        self.temporary.cleanup()

    def prepare_partial(self, length=5):
        self.partial.write_bytes(self.body[:length])
        self.metadata.write_text(json.dumps({
            "url": self.url, "validator": '"same-file"', "total": len(self.body)
        }))

    def test_no_model_or_links_never_connect(self):
        with patch.object(downloads, "urlopen") as opened:
            self.assertEqual(downloads.main([]), 0)
            self.assertEqual(downloads.main(["--liens"]), 0)
            opened.assert_not_called()

    def test_complete_download_is_published_atomically(self):
        response = Response(self.body, headers={"Content-Length": str(len(self.body)), "ETag": '"same-file"'})
        with patch.object(downloads, "urlopen", return_value=response):
            downloads.download_file(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.body)
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.metadata.exists())

    def test_short_response_is_not_published(self):
        response = Response(self.body[:5], headers={"Content-Length": str(len(self.body)), "ETag": '"same-file"'})
        with patch.object(downloads, "urlopen", return_value=response), self.assertRaises(ValueError):
            downloads.download_file(self.url, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.partial.read_bytes(), self.body[:5])
        self.assertTrue(self.metadata.exists())

    def test_resume_requests_correct_range_and_validator(self):
        self.prepare_partial()
        response = Response(self.body[5:], status=206, headers={
            "Content-Length": str(len(self.body) - 5),
            "Content-Range": f"bytes 5-{len(self.body)-1}/{len(self.body)}", "ETag": '"same-file"'
        })
        with patch.object(downloads, "urlopen", return_value=response) as opened:
            downloads.download_file(self.url, self.destination)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=5-")
        self.assertEqual(request.get_header("If-range"), '"same-file"')
        self.assertEqual(self.destination.read_bytes(), self.body)

    def test_server_ignoring_range_restarts_without_duplicate_prefix(self):
        self.prepare_partial()
        response = Response(self.body, headers={"Content-Length": str(len(self.body))})
        with patch.object(downloads, "urlopen", return_value=response):
            downloads.download_file(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.body)

    def test_wrong_resume_range_leaves_partial_untouched(self):
        self.prepare_partial()
        response = Response(self.body[4:], status=206, headers={
            "Content-Range": f"bytes 4-{len(self.body)-1}/{len(self.body)}"
        })
        with patch.object(downloads, "urlopen", return_value=response), self.assertRaises(ValueError):
            downloads.download_file(self.url, self.destination)
        self.assertEqual(self.partial.read_bytes(), self.body[:5])
        self.assertFalse(self.destination.exists())

    def test_changed_validator_is_rejected(self):
        self.prepare_partial()
        response = Response(self.body[5:], status=206, headers={
            "Content-Range": f"bytes 5-{len(self.body)-1}/{len(self.body)}", "ETag": '"changed-file"'
        })
        with patch.object(downloads, "urlopen", return_value=response), self.assertRaises(ValueError):
            downloads.download_file(self.url, self.destination)
        self.assertEqual(self.partial.read_bytes(), self.body[:5])

    def test_partial_without_validator_restarts(self):
        self.partial.write_bytes(b"old-content")
        with patch.object(downloads, "urlopen", return_value=Response(self.body)) as opened:
            downloads.download_file(self.url, self.destination)
        self.assertIsNone(opened.call_args.args[0].get_header("Range"))
        self.assertEqual(self.destination.read_bytes(), self.body)

    def test_complete_partial_can_finish_after_416(self):
        self.prepare_partial(length=len(self.body))
        error = HTTPError(self.url, 416, "Range", {"Content-Range": f"bytes */{len(self.body)}"}, None)
        with patch.object(downloads, "urlopen", side_effect=error):
            downloads.download_file(self.url, self.destination)
        self.assertEqual(self.destination.read_bytes(), self.body)

    def test_binary_hash_and_size_are_verified(self):
        binary = self.root / "model.bin"
        body = b"0123456789" * 100_001
        binary.write_bytes(body)
        downloads.validate_file(binary, "model.bin", len(body), hashlib.sha256(body).hexdigest())
        with self.assertRaises(ValueError):
            downloads.validate_file(binary, "model.bin", len(body), "0" * 64)
        with self.assertRaises(ValueError):
            downloads.validate_file(binary, "model.bin", len(body) + 1)

    def test_stub_binary_is_rejected(self):
        binary = self.root / "model.bin"
        binary.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
        with self.assertRaises(ValueError):
            downloads.validate_file(binary, "model.bin")

    def test_all_models_have_four_direct_links_and_pinned_binary_hash(self):
        for model in downloads.MODELS:
            size, digest = downloads.MODEL_BIN_DETAILS[model]
            self.assertGreater(size, 70_000_000)
            self.assertRegex(digest, r"^[a-f0-9]{64}$")
            for filename in downloads.FILES:
                self.assertTrue(downloads.model_url(model, filename).startswith("https://huggingface.co/Systran/"))

    def test_wheels_always_target_mint_and_python312(self):
        command = wheels.build_command(self.root / "wheelhouse")
        self.assertIn("--only-binary=:all:", command)
        self.assertEqual(command[command.index("--python-version") + 1], "312")
        self.assertEqual(command[command.index("--abi") + 1], "cp312")
        self.assertIn("manylinux_2_39_x86_64", command)
        self.assertIn("manylinux_2_28_x86_64", command)
        self.assertIn("manylinux2014_x86_64", command)
        self.assertNotIn("win_amd64", command)
        with patch.object(wheels.subprocess, "run") as process:
            self.assertEqual(wheels.main(["--commande"]), 0)
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
