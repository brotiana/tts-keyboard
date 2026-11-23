"""Découverte et transcription de modèles Whisper locaux au format CTranslate2."""

from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import re
import threading
from dataclasses import dataclass
from typing import Callable


def _offline_environment() -> None:
    # Affectations intentionnelles : même un environnement préexistant ne doit
    # pas autoriser un téléchargement implicite dans cette application.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


_offline_environment()


@dataclass(frozen=True)
class ModelInfo:
    key: str
    path: Path
    label: str
    problem: str = ""

    @property
    def ready(self) -> bool:
        return not self.problem


def _check_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Fichier manquant : {path.name} dans {path.parent}.")
    try:
        with path.open("rb") as handle:
            first_bytes = handle.read(256)
    except OSError as exc:
        raise ValueError(f"Impossible de lire {path.name} : {exc}") from exc
    if not first_bytes:
        raise ValueError(f"Le fichier {path.name} est vide. Téléchargez-le à nouveau.")
    if first_bytes.lstrip().startswith(b"version https://git-lfs.github.com/spec/"):
        raise ValueError(
            f"{path.name} est un pointeur Git LFS, pas le vrai fichier. "
            "Utilisez le lien de téléchargement direct (resolve/main)."
        )
    if first_bytes.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError(f"{path.name} contient une page HTML au lieu du modèle.")


def _read_json(path: Path) -> dict:
    _check_file(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Le fichier {path.name} est illisible ou son JSON est invalide.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Le fichier {path.name} doit contenir un objet JSON.")
    return value


def validate_model(path: Path) -> None:
    """Vérifie les fichiers nécessaires, sans réseau et sans charger les poids."""
    path = Path(path).expanduser()
    if not path.is_dir():
        raise ValueError(f"Dossier absent : {path}. Placez-y le modèle téléchargé.")
    if re.search(r"(?:^|[-_])(?:tiny|base|small|medium)\.en(?:$|[-_])", path.name.lower()) or path.name.lower().endswith(".en"):
        raise ValueError("Ce modèle .en est réservé à l'anglais. Utilisez un modèle multilingue pour le français.")
    config = _read_json(path / "config.json")
    _check_file(path / "model.bin")
    _read_json(path / "tokenizer.json")
    vocab_json, vocab_txt = path / "vocabulary.json", path / "vocabulary.txt"
    if vocab_json.is_file():
        _check_file(vocab_json)
        try:
            vocabulary = json.loads(vocab_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Le fichier vocabulary.json est invalide.") from exc
        if not isinstance(vocabulary, (dict, list)) or not vocabulary:
            raise ValueError("Le fichier vocabulary.json ne contient pas de vocabulaire.")
    elif vocab_txt.is_file():
        _check_file(vocab_txt)
    else:
        raise ValueError("Fichier manquant : vocabulary.json ou vocabulary.txt.")
    if config.get("is_multilingual") is False:
        raise ValueError("Ce modèle est réservé à l'anglais. Choisissez un modèle Whisper multilingue.")
    languages = config.get("supported_languages", config.get("languages"))
    if isinstance(languages, (list, tuple)) and languages and not any(
        str(language).lower() in {"fr", "fra", "french", "français"} for language in languages
    ):
        raise ValueError("La configuration de ce modèle n'annonce pas le français.")


def discover_models(models_dir: Path) -> list[ModelInfo]:
    """Liste quatre choix connus, même absents, puis les dossiers personnalisés."""
    models_dir = Path(models_dir).expanduser().resolve()
    descriptions = {
        "tiny": "Tiny · très léger",
        "base": "Base · léger",
        "small": "Small · plus précis",
        "medium": "Medium · lourd sur processeur",
    }
    results: list[ModelInfo] = []
    known_paths: set[Path] = set()

    def inspect(key: str, path: Path, label: str) -> ModelInfo:
        try:
            validate_model(path)
        except (ValueError, OSError) as exc:
            return ModelInfo(key, path, label, str(exc))
        return ModelInfo(key, path, label)

    for key, label in descriptions.items():
        candidates = [models_dir / key, models_dir / f"faster-whisper-{key}"]
        known_paths.update(candidates)
        available = [inspect(key, candidate, label) for candidate in candidates if candidate.is_dir()]
        chosen = next((item for item in available if item.ready), None)
        results.append(chosen or (available[0] if available else inspect(key, candidates[0], label)))
    if models_dir.is_dir():
        try:
            children = sorted(models_dir.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and child not in known_paths and not child.name.startswith("."):
                results.append(inspect(child.name, child, f"{child.name} · personnalisé"))
    return results


class Engine:
    """Un seul modèle en mémoire, inférence CPU int8 et français explicite."""

    def __init__(self) -> None:
        self._model = None
        self._model_path: Path | None = None
        self._lock = threading.RLock()

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._model_path = None
            gc.collect()

    def prepare(self, model_path: Path, on_status: Callable[[str], None] | None = None) -> None:
        """Charge le modèle avant l'ouverture du micro ; réutilise le modèle chargé."""
        with self._lock:
            model_path = Path(model_path).expanduser().resolve()
            validate_model(model_path)
            _offline_environment()
            if self._model_path == model_path and self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except (ImportError, OSError) as exc:
                raise RuntimeError(f"Moteur de dictée indisponible. Installez requirements.txt. Détail : {exc}") from exc
            self.unload()
            if on_status:
                on_status(f"Chargement de {model_path.name}… Attendez l'ouverture du microphone.")
            try:
                self._model = WhisperModel(
                    str(model_path), device="cpu", compute_type="int8",
                    cpu_threads=4, num_workers=1, local_files_only=True,
                )
                if "fr" not in self._model.supported_languages:
                    raise ValueError("Ce modèle ne prend pas en charge le français : choisissez un modèle multilingue.")
                self._model_path = model_path
            except Exception:
                self.unload()
                raise

    def transcribe(self, audio, model_path: Path, on_status: Callable[[str], None] | None = None) -> str:
        def status(message: str) -> None:
            if on_status:
                on_status(message)

        with self._lock:
            model_path = Path(model_path).expanduser().resolve()
            validate_model(model_path)
            _offline_environment()
            try:
                import numpy as np
            except (ImportError, OSError) as exc:
                raise RuntimeError(
                    "Le moteur de dictée ou une bibliothèque native manque. "
                    "Installez les dépendances du fichier requirements.txt. "
                    f"Détail : {exc}"
                ) from exc
            samples = np.asarray(audio, dtype=np.float32)
            if samples.ndim != 1:
                raise ValueError("L'audio doit être mono, sous forme d'un tableau à une dimension.")
            if samples.size == 0:
                return ""
            if not np.isfinite(samples).all():
                raise ValueError("L'enregistrement contient des valeurs audio invalides.")
            try:
                self.prepare(model_path, on_status)
                status("Transcription en français…")
                segments, _info = self._model.transcribe(
                    samples, language="fr", task="transcribe", beam_size=1,
                    best_of=1, temperature=0.0, condition_on_previous_text=False,
                    vad_filter=True,
                )
                return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            except ValueError:
                self.unload()
                raise
            except MemoryError as exc:
                self.unload()
                raise RuntimeError("Mémoire insuffisante. Essayez Base ou Tiny, et fermez les applications inutiles.") from exc
            except Exception as exc:
                # Un objet ayant échoué pendant une inférence ne sera pas réutilisé.
                self.unload()
                raise RuntimeError(
                    "La transcription a échoué. Vérifiez les fichiers du modèle "
                    f"ou essayez un modèle plus léger. Détail : {exc}"
                ) from exc
