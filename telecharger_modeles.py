#!/usr/bin/env python3
"""Télécharge explicitement des modèles Whisper multilingues, avec reprise."""
from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MODELS = {"tiny": "78,2 Mo", "base": "148 Mo", "small": "486 Mo", "medium": "1,53 Go"}
# Métadonnées LFS officielles Systran, vérifiées le 21 novembre 2026.
MODEL_BIN_DETAILS = {
    "tiny": (75538270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
    "base": (145217532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
    "small": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    "medium": (1527906378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
}
FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
CHUNK_SIZE = 1024 * 1024


def model_url(model: str, filename: str) -> str:
    return f"https://huggingface.co/Systran/faster-whisper-{model}/resolve/main/{filename}?download=true"


def validate_file(path: Path, filename: str, expected_size: int | None = None,
                  expected_sha256: str | None = None) -> None:
    size = path.stat().st_size
    if expected_size is not None and size != expected_size:
        raise ValueError(f"Taille incomplète : {size} octets reçus, {expected_size} attendus.")
    if filename == "model.bin":
        with path.open("rb") as source:
            prefix = source.read(128)
        if size < 1_000_000 or prefix.startswith((b"version https://git-lfs", b"<!DOCTYPE", b"<html")):
            raise ValueError("model.bin est trop petit ou n'est pas un véritable modèle binaire.")
        if expected_sha256:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != expected_sha256:
                raise ValueError("SHA256 incorrect : fichier corrompu ou version distante modifiée. "
                                 "Supprimez le fichier indiqué puis recommencez ; consultez le dépôt officiel si l'erreur persiste.")
    elif filename.endswith(".json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"{filename} n'est pas un fichier JSON valide.") from exc
        if not isinstance(data, dict) or not data:
            raise ValueError(f"{filename} est vide ou invalide.")
        if filename == "tokenizer.json" and "model" not in data:
            raise ValueError("tokenizer.json ne contient pas le tokenizer attendu.")
    else:
        text = path.read_text(encoding="utf-8")
        if len(text.splitlines()) < 1000 or text.lstrip().lower().startswith(("<!doctype", "<html")):
            raise ValueError("vocabulary.txt est incomplet ou invalide.")


def read_metadata(path: Path, url: str) -> dict:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(metadata, dict) and metadata.get("url") == url:
            return metadata
    except (OSError, ValueError):
        pass
    return {}


def download_file(url: str, destination: Path, timeout: int = 60,
                  expected_size: int | None = None, expected_sha256: str | None = None) -> None:
    """Conserve les fichiers partiels et ne publie qu'un téléchargement validé.

    Un ETag/Last-Modified permet If-Range pour éviter de réunir deux versions.
    Si le serveur ignore Range, on repart proprement de zéro.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    metadata_path = destination.with_name(destination.name + ".part.json")
    if destination.exists():
        validate_file(destination, destination.name, expected_size, expected_sha256)
        print(f"  Déjà présent : {destination.name}")
        return

    metadata = read_metadata(metadata_path, url)
    offset = partial.stat().st_size if partial.exists() else 0
    validator = metadata.get("validator")
    if not isinstance(validator, str) or not validator:
        offset = 0  # Un fichier partiel sans identité fiable doit être repris à zéro.
    headers = {"User-Agent": "Dictee-clavier/1.0", "Accept-Encoding": "identity"}
    if offset:
        headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
        print(f"  Reprise de {destination.name} à {offset / 1_000_000:.1f} Mo")
    try:
        response = urlopen(Request(url, headers=headers), timeout=timeout)
    except HTTPError as exc:
        if exc.code != 416 or not offset:
            raise
        # Par exemple : le transfert a fini, mais la validation a été interrompue.
        match = re.fullmatch(r"bytes \*/(\d+)", exc.headers.get("Content-Range", ""))
        total = int(match.group(1)) if match else None
        exc.close()
        if total == offset and metadata.get("total") == total:
            validate_file(partial, destination.name, expected_size or total, expected_sha256)
            partial.replace(destination)
            metadata_path.unlink(missing_ok=True)
            print(f"  Terminé : {destination.name}")
            return
        # La ressource a changé : nouvelle requête complète, jamais d'ajout aveugle.
        headers.pop("Range", None)
        headers.pop("If-Range", None)
        offset = 0
        response = urlopen(Request(url, headers=headers), timeout=timeout)

    with response:
        status = response.status
        if status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match:
                raise ValueError("Le serveur a envoyé un Content-Range absent ou invalide.")
            start, end, total = map(int, match.groups())
            if start != offset or end < start or end >= total:
                raise ValueError("Le serveur a envoyé une plage incohérente ; reprise refusée.")
            if offset and metadata.get("total") != total:
                raise ValueError("La taille du fichier distant a changé ; supprimez le .part et recommencez.")
            expected_response = end - start + 1
        elif status == 200:
            offset = 0
            length = response.headers.get("Content-Length")
            total = int(length) if length is not None else None
            expected_response = total
        else:
            raise ValueError(f"Réponse HTTP inattendue : {status}.")
        if expected_size is not None and total is not None and expected_size != total:
            raise ValueError("La taille distante diffère de celle du modèle officiel attendu.")
        encoding = response.headers.get("Content-Encoding", "identity")
        if encoding.lower() != "identity":
            raise ValueError("Réponse compressée inattendue ; reprise refusée.")
        response_length = response.headers.get("Content-Length")
        if response_length is not None and expected_response is not None and int(response_length) != expected_response:
            raise ValueError("Content-Length et Content-Range sont incohérents.")
        new_validator = response.headers.get("ETag")
        if new_validator and new_validator.startswith("W/"):
            new_validator = None  # If-Range n'accepte pas les validateurs faibles.
        new_validator = new_validator or response.headers.get("Last-Modified")
        if status == 206 and offset and new_validator and new_validator != validator:
            raise ValueError("Le fichier distant a changé ; supprimez le .part et recommencez.")
        metadata_path.write_text(json.dumps({"url": url, "validator": new_validator, "total": total}), encoding="utf-8")
        received = 0
        last_report = time.monotonic()
        with partial.open("ab" if offset else "wb") as target:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last_report >= 2:
                    progress = (offset + received) / 1_000_000
                    suffix = f" / {total / 1_000_000:.1f} Mo" if total else " Mo"
                    print(f"    {destination.name} : {progress:.1f}{suffix}", flush=True)
                    last_report = now
        if expected_response is not None and received != expected_response:
            raise ValueError(f"Connexion interrompue : {received} octets reçus sur {expected_response}.")
        validate_file(partial, destination.name, expected_size or total, expected_sha256)
    partial.replace(destination)
    metadata_path.unlink(missing_ok=True)
    print(f"  Terminé : {destination.name} ({destination.stat().st_size / 1_000_000:.1f} Mo)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modeles", nargs="*", metavar="MODELE", help="tiny, base, small ou medium (plusieurs possibles).")
    parser.add_argument("--liens", action="store_true", help="Afficher les liens seulement, sans connexion.")
    parser.add_argument("--dossier", type=Path, default=ROOT / "models", help="Dossier parent des modèles.")
    args = parser.parse_args(argv)
    unknown = set(args.modeles) - MODELS.keys()
    if unknown:
        parser.error(f"Modèle inconnu : {', '.join(sorted(unknown))}. Choisissez tiny, base, small ou medium.")
    selected = list(dict.fromkeys(args.modeles))
    if args.liens:
        for model in selected or MODELS:
            print(f"\n{model} — environ {MODELS[model]} — dossier models/{model}/")
            for filename in FILES:
                print(f"  {filename}\n    {model_url(model, filename)}")
        return 0
    if not selected:
        parser.print_help()
        print("\nAucun téléchargement. Au cybercafé, exemple : python3 telecharger_modeles.py tiny base")
        return 0
    try:
        for model in selected:
            print(f"\nTéléchargement explicite de {model} (environ {MODELS[model]}).", flush=True)
            for filename in FILES:
                size, digest = MODEL_BIN_DETAILS[model] if filename == "model.bin" else (None, None)
                download_file(model_url(model, filename), args.dossier / model / filename,
                              expected_size=size, expected_sha256=digest)
    except KeyboardInterrupt:
        print("\nInterrompu. Relancez la même commande pour reprendre.", file=sys.stderr)
        return 130
    except (OSError, URLError, HTTPException, ValueError) as exc:
        print(f"\nTéléchargement arrêté : {exc}", file=sys.stderr)
        print("Les fichiers .part sont conservés. Relancez la même commande pour reprendre.", file=sys.stderr)
        return 1
    print("\nModèles prêts. Rapportez le dossier models/ complet avec le code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
