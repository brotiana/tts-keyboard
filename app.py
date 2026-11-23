#!/usr/bin/env python3
"""Dictée française hors ligne. Démarrage possible avant installation des modèles."""
from __future__ import annotations

import argparse
from collections import deque
import ctypes.util
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from dictee.core import Engine, discover_models
from dictee.audio import AudioRecorder
from dictee.desktop import DesktopError, X11Desktop
from dictee.streaming import StreamingSession
from dictee.focus import FocusWatcher


def diagnostic():
    print("Dictée clavier — diagnostic local, aucun téléchargement")
    print(f"Python : {sys.version.split()[0]} | Session : {os.environ.get('XDG_SESSION_TYPE', 'inconnue')}")
    for name in ("tkinter", "numpy", "sounddevice", "faster_whisper"):
        print(f"{name} : {'installé' if importlib.util.find_spec(name) else 'absent'}")
    print(f"PortAudio : {ctypes.util.find_library('portaudio') or 'absent'}")
    for model in discover_models(ROOT / "models"):
        print(f"{model.key} : {'prêt' if model.ready else model.problem} — {model.path}")


class Application:
    def __init__(self, root, desktop, desktop_problem=""):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.desktop = root, desktop
        self.events = queue.Queue()
        self.engine, self.recorder = Engine(), AudioRecorder(max_seconds=None)
        self.focus_watcher = None
        self.voice_target = None
        self.voice_finish = False
        self.session = None
        self.session_id = 0
        self.session_done = False
        self.session_text = []
        self.paste_queue = deque()
        self.paste_busy = False
        self.paste_blocked = False
        self.paste_abort = threading.Event()
        self.paste_warning = ""
        self.finish_message = ""
        self.state = "idle"
        self.closed = False
        self.target = None
        self.paste_requested = False
        self.cancelled = False
        self.models = []
        self.devices = [(None, "Microphone par défaut")]
        self.last_text = ""
        self.config_path = ROOT / "preferences.json"
        self.preferences = self._read_preferences()
        self.focus_var = tk.BooleanVar(value=True)
        self.send_var = tk.BooleanVar(value=bool(self.preferences.get("send_after_paste", False)))
        self.model_var = tk.StringVar()
        self.mic_var = tk.StringVar(value=self.devices[0][1])
        self.auto_var = tk.BooleanVar(value=bool(self.preferences.get("auto_paste", True)))
        self.terminal_var = tk.BooleanVar(value=bool(self.preferences.get("terminal", False)))
        self.status_var = tk.StringVar(value="Ajoutez un modèle dans models/, puis cliquez sur Actualiser.")
        self.model_status = tk.StringVar()
        self.device_status = tk.StringVar(value="")
        self.clock_var = tk.StringVar(value="00:00 · En direct")
        root.title("Dictée clavier · Français hors ligne")
        root.geometry("800x730")
        root.minsize(640, 650)
        root.configure(bg="#f3f5f8")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", foreground="#17233b", font=("Sans", 10))
        style.configure("Title.TLabel", font=("Sans", 23, "bold"))
        style.configure("Muted.TLabel", foreground="#53627a")
        style.configure("TButton", font=("Sans", 10), padding=(10, 7))
        style.configure("Accent.TButton", background="#176455", foreground="white", font=("Sans", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#237e6d")])
        style.configure("TCheckbutton", background="#f3f5f8", font=("Sans", 10))
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(12, weight=1)
        ttk.Label(frame, text="Dictée clavier", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Français · Hors ligne · Transcription progressive", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 18))
        ttk.Label(frame, text="1. Choisissez un modèle local").grid(row=2, column=0, sticky="w")
        model_row = ttk.Frame(frame)
        model_row.grid(row=3, column=0, sticky="ew", pady=(6, 4))
        model_row.columnconfigure(0, weight=1)
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var, state="readonly", width=40)
        self.model_box.grid(row=0, column=0, sticky="ew")
        self.model_box.bind("<<ComboboxSelected>>", self._model_changed)
        self.refresh_button = ttk.Button(model_row, text="Actualiser", command=self.refresh_models)
        self.refresh_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(frame, textvariable=self.model_status, style="Muted.TLabel", wraplength=690).grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text="2. Choisissez votre microphone").grid(row=5, column=0, sticky="w", pady=(15, 5))
        mic_row = ttk.Frame(frame)
        mic_row.grid(row=6, column=0, sticky="ew")
        mic_row.columnconfigure(0, weight=1)
        self.mic_box = ttk.Combobox(mic_row, textvariable=self.mic_var, values=[self.devices[0][1]], state="readonly")
        self.mic_box.grid(row=0, column=0, sticky="ew")
        self.mic_button = ttk.Button(mic_row, text="Détecter", command=self.refresh_devices)
        self.mic_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(frame, textvariable=self.device_status, style="Muted.TLabel", wraplength=690).grid(row=7, column=0, sticky="w", pady=(4, 8))
        options = ttk.Frame(frame)
        options.grid(row=8, column=0, sticky="ew")
        ttk.Checkbutton(options, text="Coller au fur et à mesure pendant une dictée avec F8", variable=self.auto_var).pack(anchor="w")
        ttk.Checkbutton(options, text="Collage dans un terminal (Ctrl+Maj+V)", variable=self.terminal_var).pack(anchor="w", pady=(3, 7))
        ttk.Checkbutton(options, text="Dictée automatique dans Firefox / VS Code", variable=self.focus_var).pack(anchor="w")
        delivery = ttk.Frame(options)
        delivery.pack(anchor="w", pady=(5, 3))
        ttk.Label(delivery, text="Après « terminé » : ").pack(side="left")
        ttk.Radiobutton(delivery, text="Coller seulement", variable=self.send_var, value=False, command=self._save_preferences).pack(side="left")
        ttk.Radiobutton(delivery, text="Coller et envoyer (Entrée)", variable=self.send_var, value=True, command=self._save_preferences).pack(side="left", padx=10)
        controls = ttk.Frame(frame)
        controls.grid(row=9, column=0, sticky="ew", pady=(5, 10))
        self.record_button = ttk.Button(controls, text="● Dicter ici", style="Accent.TButton", command=lambda: self.toggle(False))
        self.record_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Annuler la suite", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.clock_var).pack(side="right")
        self.level = ttk.Progressbar(frame, maximum=100, mode="determinate")
        self.level.grid(row=10, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(frame, textvariable=self.status_var, wraplength=690).grid(row=11, column=0, sticky="w", pady=(0, 10))
        result_frame = ttk.Frame(frame)
        result_frame.grid(row=12, column=0, sticky="nsew")
        self.text = tk.Text(result_frame, wrap="word", height=6, font=("Sans", 12), relief="flat", padx=12, pady=12, background="white", foreground="#17233b", undo=True)
        scrollbar = ttk.Scrollbar(result_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", expand=True, fill="both")
        footer = ttk.Frame(frame)
        footer.grid(row=13, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(footer, text="Copier le texte", command=self.copy).pack(side="left")
        ttk.Button(footer, text="Effacer", command=lambda: self.text.delete("1.0", "end")).pack(side="left", padx=6)
        ttk.Button(footer, text="Modèles et installation", command=self.open_guide).pack(side="right")
        ttk.Label(frame, text="F8 → parlez : le texte apparaît automatiquement à mesure. F8 coupe le micro.\nTranscription après une pause sans voix · Aucune coupure périodique.", style="Muted.TLabel", wraplength=690).grid(row=14, column=0, sticky="w", pady=(14, 0))
        self.mic_indicator = tk.Toplevel(root)
        self.mic_indicator.withdraw()
        self.mic_indicator.overrideredirect(True)
        self.mic_indicator.attributes("-topmost", True)
        self.mic_indicator.attributes("-type", "tooltip")
        self.mic_indicator.configure(takefocus=False)
        self.mic_indicator.geometry(f"+{max(0, root.winfo_screenwidth()-340)}+35")
        tk.Label(self.mic_indicator, text="● Dictée active — dites « terminé »", bg="#176455", fg="white", padx=15, pady=12).pack()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_models()
        if desktop:
            desktop.start_hotkey(lambda: self.events.put(("hotkey", None)), lambda e: self.events.put(("desktop_error", e)))
        else:
            self.device_status.set(desktop_problem)
            self.auto_var.set(False)
        self.focus_watcher = FocusWatcher(
            lambda message: self.events.put(("focus", message)),
            lambda: self.paste_abort.set() if self.voice_target is not None else None,
        )
        try:
            self.focus_watcher.start()
        except OSError as exc:
            self.device_status.set(f"Mode automatique indisponible : {exc}")
        self.root.after(60, self.poll)
        self.root.after(250, self.refresh_devices)

    def _read_preferences(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_preferences(self):
        model = self.selected_model()
        value = {"model": model.key if model else "base", "auto_paste": self.auto_var.get(), "terminal": self.terminal_var.get(), "microphone": self.mic_var.get(), "send_after_paste": self.send_var.get()}
        try:
            temporary = self.config_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.config_path)
        except OSError:
            pass

    def selected_model(self):
        index = self.model_box.current()
        return self.models[index] if 0 <= index < len(self.models) else None

    def refresh_models(self):
        if self.state != "idle":
            return
        previous = self.selected_model()
        preferred = previous.key if previous else self.preferences.get("model", "base")
        self.models = discover_models(ROOT / "models")
        self.model_box["values"] = [m.label + (" — prêt" if m.ready else " — à installer / vérifier") for m in self.models]
        wanted = next((i for i, m in enumerate(self.models) if m.key == preferred and m.ready), None)
        if wanted is None:
            wanted = next((i for key in ("base", "tiny", "small", "medium") for i, m in enumerate(self.models) if m.key == key and m.ready), None)
        if wanted is None:
            fallback = next((i for i, m in enumerate(self.models) if m.key == preferred), 0)
            wanted = next((i for i, m in enumerate(self.models) if m.ready), fallback)
        if self.models:
            self.model_box.current(wanted)
        self._model_changed()

    def _model_changed(self, event=None):
        model = self.selected_model()
        if model:
            self.model_status.set(str(model.path.relative_to(ROOT)) + " · " + ("Disponible, calcul sur processeur." if model.ready else model.problem))
            if self.state == "idle":
                self.status_var.set("Prêt. F8 ou « Dicter ici » pour transcrire en continu." if model.ready else "Modèle absent ou incomplet. Consultez « Modèles et installation ».")

    def refresh_devices(self):
        if self.state != "idle":
            return
        self.mic_button.configure(state="disabled")
        def work():
            try:
                self.events.put(("devices", AudioRecorder.list_devices()))
            except Exception as exc:
                self.events.put(("device_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _controls(self):
        busy = self.state != "idle"
        for widget in (self.model_box, self.mic_box):
            widget.configure(state="disabled" if busy else "readonly")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self.mic_button.configure(state="disabled" if busy else "normal")
        self.record_button.configure(
            text="■ Couper le micro" if self.state == "recording" else "● Dicter ici",
            state="normal" if self.state in ("idle", "recording") else "disabled",
        )
        self.cancel_button.configure(state="normal" if self.state in ("starting", "recording", "finishing") else "disabled")

    def toggle(self, global_hotkey=False, voice_target=None):
        if self.state == "recording":
            self.stop_recording()
            return
        if self.state != "idle":
            return
        model = self.selected_model()
        if not model or not model.ready:
            self.status_var.set("Installez un modèle complet dans models/ puis cliquez sur Actualiser.")
            return
        self.target = None
        self.paste_requested = bool(global_hotkey and (voice_target is not None or self.auto_var.get()) and self.desktop)
        self.paste_terminal = self.terminal_var.get() if voice_target is None else False
        if self.paste_requested:
            try:
                if voice_target is not None or self.root.focus_displayof() is None:
                    self.target = self.desktop.active_window()
            except (DesktopError, self.tk.TclError):
                pass
        self.voice_target = voice_target
        self.voice_finish = False
        self.delivered = False
        self.session_send = self.send_var.get()
        self.session_model = model.path
        self.session_id += 1
        session_id = self.session_id
        self.cancelled = False
        self.session_done = False
        self.session_text = []
        self.finish_message = ""
        self.paste_warning = ""
        self.paste_blocked = False
        self.paste_busy = False
        self.paste_abort = threading.Event()
        self.paste_queue.clear()
        self.clock_var.set("00:00 · En direct")
        self.state = "starting"
        self._controls()
        self.status_var.set("Préparation du modèle… Le microphone s'ouvrira ensuite.")
        index = self.mic_box.current()
        device = self.devices[index][0] if 0 <= index < len(self.devices) else None
        self.recorder = AudioRecorder(max_seconds=None)
        self.session = StreamingSession(
            self.engine, self.recorder,
            lambda kind, value: self.events.put(("stream", (session_id, kind, value))),
        )
        try:
            self.session.start(self.session_model, device)
        except Exception as exc:
            self.session_done = True
            self.finish_message = f"Impossible de démarrer : {exc}"
            self._maybe_finish()

    def stop_recording(self):
        if self.state not in ("starting", "recording"):
            return
        self.state = "finishing"
        self._controls()
        self.status_var.set("Fermeture du microphone et transcription des derniers mots…")
        if self.session:
            self.session.stop()

    def cancel(self):
        if self.state == "idle":
            return
        self.cancelled = True
        self.paste_abort.set()
        self.paste_queue.clear()
        self.state = "cancelling"
        self._controls()
        self.status_var.set("Arrêt en cours. Les passages déjà transcrits sont conservés.")
        if self.session:
            self.session.cancel()
        self._maybe_finish()

    def _receive_text(self, value):
        if self.cancelled or self.session_done or not value.strip():
            return
        value = value.strip()
        finish_command = False
        if getattr(self, "voice_target", None) is not None:
            if self.voice_finish:
                return
            command = re.search(r"(?:^|\s)(?:terminé|terminée|termine)[\s.!?,;:…]*$", value, re.IGNORECASE)
            if command:
                finish_command = True
                value = value[:command.start()].rstrip(" ,;:")
                if not value:
                    self.voice_finish = True
                    self.stop_recording()
                    return
        existing = self.text.get("1.0", "end-1c")
        prefix = ("\n" if not self.session_text else " ") if existing and not existing[-1].isspace() else ""
        self.text.insert("end", prefix + value)
        self.text.see("end")
        self.session_text.append(value)
        self.last_text = " ".join(self.session_text)
        if finish_command:
            self.voice_finish = True
            self.stop_recording()
        if getattr(self, "voice_target", None) is None and self.paste_requested and self.target and not self.paste_blocked and self.auto_var.get():
            self.paste_queue.append(value + " ")
            self._pump_paste()
        if self.state == "recording":
            self.status_var.set("● Dictée en cours : continuez à parler." + (" " + self.paste_warning if self.paste_warning else ""))

    def _pump_paste(self):
        # Une seule écriture de presse-papiers / injection à la fois.
        if self.closed or self.paste_busy:
            return
        if self.cancelled or self.paste_blocked or (self.voice_target is None and not self.auto_var.get()):
            self.paste_queue.clear()
        if not self.paste_queue:
            self._maybe_finish()
            return
        value = "".join(self.paste_queue)
        self.paste_queue.clear()
        try:
            self._clipboard(value)
        except self.tk.TclError as exc:
            self.paste_blocked = True
            self.paste_warning = f"Collage suspendu : {exc}. Le texte reste dans cette fenêtre."
            self.device_status.set(self.paste_warning)
            self._maybe_finish()
            return
        self.paste_busy = True
        session_id = self.session_id
        self.root.after(120, lambda: self._paste(session_id))

    def _paste(self, session_id):
        if self.closed or session_id != self.session_id:
            return
        if self.cancelled or self.paste_abort.is_set():
            self.paste_warning = "Collage annulé : le champ cible a perdu le focus."
            self.paste_busy = False
            self._maybe_finish()
            return
        target, terminal, abort = self.target, self.paste_terminal, self.paste_abort
        def work():
            error = None
            try:
                self.desktop.paste(target, terminal, cancel_event=abort)
            except Exception as exc:
                error = str(exc)
            self.events.put(("paste_finished", (session_id, error)))
        threading.Thread(target=work, name="collage-dictee", daemon=True).start()

    def _paste_finished(self, session_id, error):
        if self.closed or session_id != self.session_id:
            return
        if not error:
            self.delivered = True
        if error and not self.cancelled:
            # Pas de reprise automatique si la fenêtre cible redevient active.
            self.paste_blocked = True
            self.paste_queue.clear()
            self.paste_warning = "Collage suspendu ; le texte reste dans cette fenêtre."
            self.device_status.set(f"{error} Les passages suivants restent dans le résultat.")
        # Laisser le logiciel destinataire lire la sélection X11 avant de la remplacer.
        def release():
            if self.closed or session_id != self.session_id:
                return
            self.paste_busy = False
            if not error and not self.cancelled and self.voice_target is not None and self.voice_finish and self.session_send:
                try:
                    self.desktop.submit(self.target, self.paste_abort)
                except DesktopError as exc:
                    self.paste_warning = str(exc)
            self._pump_paste()
        self.root.after(350, release)

    def _maybe_finish(self):
        if not self.session_done or self.paste_busy or self.paste_queue:
            return
        if self.cancelled:
            message = "Dictée arrêtée. Les passages déjà transcrits sont conservés." + (" " + self.paste_warning if self.paste_warning else "")
        elif self.finish_message:
            message = self.finish_message
        elif self.session_text:
            message = "Dictée terminée." + (" " + self.paste_warning if self.paste_warning else " F8 pour recommencer.")
        else:
            message = "Aucune parole reconnue. Vérifiez le microphone ou essayez Tiny."
        self._idle(message)

    def _idle(self, message):
        if getattr(self, "voice_target", None) is not None and self.session_text and not getattr(self, "delivered", False):
            try:
                self._clipboard(" ".join(self.session_text))
                message += " Texte copié : cliquez dans le champ puis Ctrl+V."
            except self.tk.TclError:
                message += " Utilisez le bouton Copier le texte."
        self.state = "idle"
        self.session = None
        self.level["value"] = 0
        self.status_var.set(message)
        self._controls()
        self._save_preferences()

    def _clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def copy(self):
        if self.paste_busy:
            self.status_var.set("Collage en cours ; attendez un instant avant de copier.")
            return
        text = self.text.get("1.0", "end-1c").strip()
        if text:
            try:
                self._clipboard(text)
                self.status_var.set("Texte copié. Collez-le dans votre logiciel avec Ctrl+V.")
            except self.tk.TclError as exc:
                self.status_var.set(f"Copie impossible : {exc}")

    def open_guide(self):
        webbrowser.open((ROOT / "TELECHARGEMENTS.html").as_uri())

    def _stream_event(self, session_id, kind, value):
        if session_id != self.session_id or self.closed or self.session_done:
            return
        if kind == "started" and self.state == "starting":
            self.state = "recording"
            if self.voice_target is not None:
                self.mic_indicator.deiconify()
            self._controls()
            self.status_var.set("● Microphone ouvert. Parlez : le texte arrive automatiquement.")
        elif kind == "text":
            self._receive_text(value)
        elif kind == "status" and not self.cancelled:
            if self.state == "starting":
                self.status_var.set(value)
            elif self.state == "recording" and not self.paste_warning:
                self.status_var.set("● Microphone ouvert · " + value)
        elif kind == "backlog" and self.state == "recording" and float(value) >= 6:
            self.status_var.set(f"● Microphone ouvert · {float(value):.0f} s de parole en attente. Tiny réduit le délai.")
        elif kind in ("done", "cancelled", "error"):
            self.session_done = True
            self.mic_indicator.withdraw()
            if kind == "cancelled":
                self.cancelled = True
                self.paste_abort.set()
                self.paste_queue.clear()
            elif kind == "error":
                self.finish_message = f"Dictée interrompue : {value}"
            self.state = "finishing" if not self.cancelled else "cancelling"
            self._controls()
            if kind == "done" and self.voice_target is not None and self.voice_finish and not self.cancelled:
                if self.focus_watcher.token == self.voice_target and not self.paste_abort.is_set() and self.session_text:
                    self.paste_queue.append(" ".join(self.session_text))
                else:
                    self.paste_blocked = True
                    self.paste_warning = "Champ changé : texte conservé ici."
            self._pump_paste()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "focus":
                    if 'error' in value:
                        self.device_status.set("Mode automatique indisponible : " + value['error'])
                    if 'focus' in value:
                        token = value['focus']
                        if token != self.focus_watcher.token:
                            continue  # Événement dépassé : ne pas démarrer sur un ancien champ.
                        if self.voice_target is not None and token != self.voice_target and self.state != "idle":
                            self.paste_warning = "Champ cible changé : collage interrompu."
                            self.cancel()
                        elif token is not None and self.state == "idle" and self.focus_var.get() and self.desktop:
                            self.toggle(True, voice_target=token)
                elif kind == "hotkey":
                    self.toggle(True)
                elif kind == "stream":
                    self._stream_event(*value)
                elif kind == "paste_finished":
                    self._paste_finished(*value)
                elif kind == "desktop_error":
                    self.device_status.set(value)
                    self.auto_var.set(False)
                    self.paste_abort.set()
                    self.paste_queue.clear()
                elif kind == "devices":
                    previous = self.mic_var.get()
                    self.devices = [(None, "Microphone par défaut")] + value
                    labels = [name for _, name in self.devices]
                    self.mic_box["values"] = labels
                    preferred = self.preferences.get("microphone", previous)
                    self.mic_box.current(labels.index(preferred) if preferred in labels else 0)
                    self.mic_button.configure(state="normal" if self.state == "idle" else "disabled")
                    self.device_status.set("Microphones détectés. La jauge s'active pendant la dictée." if value else "Aucun microphone détecté. Branchez-le puis cliquez sur Détecter.")
                elif kind == "device_error":
                    self.device_status.set(value)
                    self.mic_button.configure(state="normal" if self.state == "idle" else "disabled")
        except queue.Empty:
            pass
        if self.state in ("recording", "finishing", "cancelling"):
            elapsed = int(self.recorder.elapsed)
            self.clock_var.set(f"{elapsed // 60:02d}:{elapsed % 60:02d} · En direct")
            self.level["value"] = min(100, self.recorder.level * 100)
        self.root.after(60, self.poll)

    def close(self):
        self.closed = True
        if self.focus_watcher:
            self.focus_watcher.close()
        self.paste_abort.set()
        self.paste_queue.clear()
        self._save_preferences()
        if self.session:
            self.session.close()
        if self.desktop:
            self.desktop.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Dictée française avec Whisper, entièrement hors ligne.")
    parser.add_argument("--diagnostic", action="store_true", help="Vérifie les dépendances et modèles sans ouvrir de fenêtre.")
    args = parser.parse_args()
    if args.diagnostic:
        diagnostic()
        return 0
    try:
        desktop, problem = X11Desktop(), ""
    except (DesktopError, OSError) as exc:
        desktop, problem = None, str(exc)
    try:
        import tkinter as tk
        root = tk.Tk()
    except ImportError:
        print("Tkinter manque. Installez python3-tk (voir README.md).", file=sys.stderr)
        return 1
    except tk.TclError as exc:
        print(f"Impossible d'ouvrir une fenêtre : {exc}\nLancez bash lancer.sh depuis votre bureau Linux.", file=sys.stderr)
        return 1
    Application(root, desktop, problem)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
