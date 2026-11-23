"""Capture continue et transcription ordonnée, sans accès à Tk ni au clavier."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import threading
from typing import Callable


class StreamingSession:
    """Une session de dictée, avec un producteur audio et un seul décodeur.

    ``emit(kind, value)`` doit être rapide (par exemple ``queue.put``). Les
    résultats arrivent pendant la capture. ``stop`` ferme le microphone puis
    termine les phrases en attente ; ``cancel`` ignore tout résultat restant.
    Un événement terminal unique, done/cancelled/error, indique que les deux
    threads ont fini. Une nouvelle dictée doit utiliser une nouvelle session.
    """

    def __init__(
        self,
        engine,
        recorder,
        emit: Callable[[str, object], None],
        *,
        poll_interval: float = 0.05,
        max_pending_seconds: float = 300,
        chunker_factory=None,
    ):
        if poll_interval <= 0 or max_pending_seconds <= 0:
            raise ValueError("Les limites de la session doivent être positives.")
        self.engine = engine
        self.recorder = recorder
        self.emit = emit
        self.poll_interval = float(poll_interval)
        self.max_pending_seconds = float(max_pending_seconds)
        self._chunker_factory = chunker_factory
        self._condition = threading.Condition(threading.RLock())
        self._stop_requested = threading.Event()
        self._cancel_requested = False
        self._failure: str | None = None
        self._started_once = False
        self._active = False
        self._closed = False
        self._capture_done = False
        self._queue = deque()
        self._queued_samples = 0
        self._inflight_samples = 0
        self._sample_rate = int(getattr(recorder, "sample_rate", 16000))
        self._capture_thread = None
        self._decode_thread = None
        self._model_path = None

    @property
    def active(self) -> bool:
        with self._condition:
            return self._active

    @property
    def pending_seconds(self) -> float:
        with self._condition:
            return (self._queued_samples + self._inflight_samples) / self._sample_rate

    def start(self, model_path: Path, device=None) -> None:
        with self._condition:
            if self._started_once or self._closed:
                raise RuntimeError("Cette session a déjà été utilisée. Créez une nouvelle session de dictée.")
            self._started_once = True
            self._active = True
            self._model_path = Path(model_path)
            self._capture_thread = threading.Thread(
                target=self._capture, args=(device,), name="capture-continue", daemon=True,
            )
            self._capture_thread.start()

    def stop(self) -> None:
        """Non bloquant : ferme la capture rapidement, puis vide la file audio."""
        self._stop_requested.set()
        with self._condition:
            self._condition.notify_all()

    def cancel(self) -> None:
        """Non bloquant ; les textes déjà émis restent acquis à l'interface."""
        with self._condition:
            self._cancel_requested = True
            self._queue.clear()
            self._queued_samples = 0
            self._stop_requested.set()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
        self.cancel()

    def wait(self, timeout: float | None = None) -> bool:
        """Attente optionnelle pour les tests/CLI ; jamais nécessaire dans Tk."""
        thread = self._capture_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return not self.active

    def _emit(self, kind: str, value=None) -> None:
        # Sérialise l'émission d'un texte et l'annulation : aucun nouveau texte
        # ne peut être envoyé après le retour de cancel().
        with self._condition:
            if self._cancel_requested or self._failure:
                return
            self.emit(kind, value)

    def _fail(self, message: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = message
            self._queue.clear()
            self._queued_samples = 0
            self._stop_requested.set()
            self._condition.notify_all()

    def _aborted(self) -> bool:
        with self._condition:
            return self._cancel_requested or self._failure is not None

    def _make_chunker(self):
        if self._chunker_factory is None:
            # Import différé : l'interface reste ouvrable avant installation.
            from .audio import SpeechChunker
            factory = SpeechChunker
        else:
            factory = self._chunker_factory
        return factory(sample_rate=self._sample_rate)

    def _enqueue(self, chunks) -> None:
        for audio in chunks:
            count = len(audio)
            if count == 0:
                continue
            with self._condition:
                if self._cancel_requested or self._failure:
                    return
                total = self._queued_samples + self._inflight_samples + count
                if total > self.max_pending_seconds * self._sample_rate:
                    raise RuntimeError(
                        "Le modèle ne suit pas la vitesse de la dictée : microphone arrêté. "
                        "Le texte déjà affiché est conservé, mais les dernières phrases en attente "
                        "n'ont pas été transcrites. Recommencez avec Tiny ou Base."
                    )
                self._queue.append(audio)
                self._queued_samples += count
                self._condition.notify_all()
                self._emit("backlog", self.pending_seconds)

    def _capture(self, device) -> None:
        microphone_may_be_open = False
        try:
            self.engine.prepare(self._model_path, lambda message: self._emit("status", message))
            if self._stop_requested.is_set() or self._aborted():
                return
            chunker = self._make_chunker()
            # Le drapeau précède start() pour nettoyer aussi une ouverture
            # interrompue ou un périphérique échouant après ouverture.
            microphone_may_be_open = True
            self.recorder.start(device)
            if self._aborted():
                return
            self._decode_thread = threading.Thread(
                target=self._decode, name="transcription-continue", daemon=True,
            )
            self._decode_thread.start()
            if not self._stop_requested.is_set():
                self._emit("started")

            while not self._stop_requested.is_set() and not self._aborted():
                errors = self.recorder.errors
                if errors:
                    raise RuntimeError("\n".join(errors))
                self._enqueue(chunker.feed(self.recorder.drain()))
                if self.recorder.limit_reached:
                    self._stop_requested.set()
                    break
                self._stop_requested.wait(self.poll_interval)

            if not self._aborted():
                # stop() retourne uniquement les échantillons pas encore drainés.
                tail = self.recorder.stop()
                microphone_may_be_open = False
                self._enqueue(chunker.feed(tail))
                self._enqueue(chunker.flush())
        except Exception as exc:
            self._fail(str(exc))
        finally:
            if microphone_may_be_open:
                try:
                    self.recorder.cancel()
                except Exception as exc:
                    self._fail(f"Impossible de fermer le microphone : {exc}")
            with self._condition:
                self._capture_done = True
                self._condition.notify_all()
            if self._decode_thread:
                self._decode_thread.join()
            with self._condition:
                self._active = False
                self._queue.clear()
                self._queued_samples = 0
                self._inflight_samples = 0
                if self._cancel_requested:
                    kind, value = "cancelled", None
                elif self._failure:
                    kind, value = "error", self._failure
                else:
                    kind, value = "done", None
                self.emit(kind, value)
                self._condition.notify_all()

    def _decode(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._queue or self._capture_done or self._cancel_requested or self._failure,
                    )
                    if self._cancel_requested or self._failure:
                        return
                    if not self._queue:
                        return  # Capture terminée et tous les morceaux ont été traités.
                    audio = self._queue.popleft()
                    count = len(audio)
                    self._queued_samples -= count
                    self._inflight_samples = count
                text = self.engine.transcribe(audio, self._model_path)
                with self._condition:
                    self._inflight_samples = 0
                    if self._cancel_requested or self._failure:
                        return
                    if text.strip():
                        self._emit("text", text.strip())
                    self._emit("backlog", self.pending_seconds)
        except Exception as exc:
            self._fail(str(exc))
