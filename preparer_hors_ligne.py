#!/usr/bin/env python3
"""À exécuter au cybercafé : collecte des roues pour Linux Mint / Python 3.12."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def build_command(destination: Path) -> list[str]:
    # Mint 22 : glibc 2.39. Inclure les anciens tags et les tags PEP 600 :
    # av, onnxruntime et ctranslate2 ne ciblent pas tous la même glibc.
    platforms = [f"manylinux_2_{minor}_x86_64" for minor in range(39, 16, -1)]
    platforms += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    command = [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "download",
               "--only-binary=:all:", "--python-version", "312", "--implementation", "cp",
               "--abi", "cp312", "--dest", str(destination), "-r", str(ROOT / "requirements.txt")]
    for platform in platforms:
        command.extend(["--platform", platform])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossier", type=Path, default=ROOT / "wheelhouse",
                        help="Dossier des paquets téléchargés (défaut : wheelhouse/).")
    parser.add_argument("--commande", action="store_true",
                        help="Afficher la commande uniquement, sans connexion.")
    args = parser.parse_args(argv)
    command = build_command(args.dossier.resolve())
    if args.commande:
        print(subprocess.list2cmdline(command))
        return 0
    print("Téléchargement explicite des dépendances pour Linux x86_64 / CPython 3.12.", flush=True)
    print("Cette étape utilise Internet et ne télécharge aucun modèle.", flush=True)
    args.dossier.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"Impossible de lancer pip : {exc}", file=sys.stderr)
        return 1
    if result.returncode:
        print("Préparation incomplète. Python et pip doivent être installés au cybercafé.", file=sys.stderr)
        print("Corrigez l'erreur affichée ci-dessus puis relancez ce script avant de partir.", file=sys.stderr)
        return result.returncode
    print(f"Paquets prêts dans {args.dossier.resolve()}.")
    print("Rapportez tout ce dossier, les modèles et le code. Chez vous : bash installer.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
