"""Capture microphone bornée en mémoire ; sortie mono à 16 kHz."""

from __future__ import annotations

import threading
from collections import deque


def _dependencies():
    try:
        import numpy as np
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "La capture audio nécessite numpy, sounddevice et PortAudio. "
            f"Consultez les instructions d'installation. Détail : {exc}"
        ) from exc
    return np, sd


def _resample_mono(samples, source_rate: float, target_rate: int):
    """Rééchantillonnage linéaire pour les micros refusant 16 kHz."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.float32)
    if samples.size == 0 or source_rate == target_rate:
        return samples
    count = max(1, round(samples.size * target_rate / source_rate))
    positions = np.arange(count, dtype=np.float64) * source_rate / target_rate
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


class _StreamingResampler:
    """Garde l'horloge du rééchantillonnage entre deux lectures du microphone."""

    def __init__(self, source_rate, target_rate):
        import numpy as np
        self._np = np
        self.source_rate, self.target_rate = float(source_rate), int(target_rate)
        self._total = self._emitted = 0
        self._tail = np.empty(0, dtype=np.float32)

    def convert(self, samples, final=False):
        np = self._np
        base = self._total - self._tail.size
        data = np.concatenate((self._tail, samples)) if self._tail.size else samples
        self._total += samples.size
        if not data.size:
            return np.empty(0, dtype=np.float32)
        # Avant l'arrêt, attendre l'échantillon suivant au lieu d'extrapoler
        # artificiellement à chaque frontière de drain().
        final_count = max(1, round(self._total * self.target_rate / self.source_rate))
        count = (final_count if final else
                 min(final_count, int((self._total - 1) * self.target_rate // self.source_rate) + 1))
        positions = np.arange(self._emitted, count, dtype=np.float64) * self.source_rate / self.target_rate - base
        output = np.interp(positions, np.arange(data.size), data).astype(np.float32)
        self._emitted = count
        keep_from = max(0, min(data.size - 1, int(count * self.source_rate / self.target_rate) - base))
        self._tail = data[keep_from:].copy()
        return output


class AudioRecorder:
    def __init__(self, sample_rate: int = 16000, max_seconds: float | None = 120,
                 max_buffer_seconds: float = 30) -> None:
        if sample_rate <= 0 or (max_seconds is not None and max_seconds <= 0) or max_buffer_seconds <= 0:
            raise ValueError("La fréquence et les durées maximales doivent être positives.")
        self.sample_rate = int(sample_rate)
        self.max_seconds = None if max_seconds is None else float(max_seconds)
        self.max_buffer_seconds = float(max_buffer_seconds)
        self._lock = threading.Lock()
        self._stream = None
        self._np = None
        self._resampler = None
        self._chunks = []
        self._source_rate = float(self.sample_rate)
        self._frames = 0
        self._buffer_frames = 0
        self._max_frames = None if self.max_seconds is None else int(self._source_rate * self.max_seconds)
        self._max_buffer_frames = max(1, int(self._source_rate * self.max_buffer_seconds))
        self._recording = False
        self._accepting = False
        self._level = 0.0
        self._limit_reached = False
        self._errors: list[str] = []

    @staticmethod
    def list_devices() -> list[tuple[int, str]]:
        _, sd = _dependencies()
        try:
            return [(index, str(device["name"])) for index, device in enumerate(sd.query_devices())
                    if device["max_input_channels"] > 0]
        except Exception as exc:
            raise RuntimeError(f"Impossible de lister les microphones : {exc}") from exc

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def level(self) -> float:
        with self._lock:
            return self._level

    @property
    def elapsed(self) -> float:
        with self._lock:
            return self._frames / self._source_rate

    @property
    def limit_reached(self) -> bool:
        with self._lock:
            return self._limit_reached

    @property
    def errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._errors)

    def start(self, device: int | None = None) -> None:
        if self.recording:
            raise RuntimeError("Un enregistrement est déjà en cours.")
        np, sd = _dependencies()
        try:
            info = sd.query_devices(device, "input")
            if info["max_input_channels"] < 1:
                raise RuntimeError("Le périphérique sélectionné ne possède pas d'entrée microphone.")
            rate = float(self.sample_rate)
            try:
                sd.check_input_settings(device=device, channels=1, dtype="float32", samplerate=rate)
            except Exception:
                rate = float(info["default_samplerate"])
                sd.check_input_settings(device=device, channels=1, dtype="float32", samplerate=rate)
            if rate <= 0:
                raise ValueError("Fréquence microphone invalide.")
            with self._lock:
                self._np = np
                self._resampler = None
                self._chunks = []
                self._frames = 0
                self._buffer_frames = 0
                self._source_rate = rate
                self._max_frames = None if self.max_seconds is None else max(1, int(rate * self.max_seconds))
                self._max_buffer_frames = max(1, int(rate * self.max_buffer_seconds))
                self._level = 0.0
                self._errors = []
                self._limit_reached = False
                self._recording = True
                self._accepting = True
            self._stream = sd.InputStream(
                samplerate=rate, device=device, channels=1, dtype="float32",
                callback=self._callback, blocksize=0,
            )
            self._stream.start()
        except Exception as exc:
            self.cancel()
            raise RuntimeError(f"Impossible d'ouvrir le microphone : {exc}") from exc

    def _callback(self, indata, frames, time_info, status) -> None:
        # PortAudio appelle cette fonction sur un autre thread. Aucune exception
        # ne doit s'en échapper ; l'interface peut consulter errors puis stop().
        try:
            with self._lock:
                if not self._accepting:
                    return
                if status:
                    self._errors.append(f"Capture audio interrompue ({status}). Réessayez l'enregistrement.")
                    self._accepting = False
                    return
                remaining = frames if self._max_frames is None else self._max_frames - self._frames
                data = self._np.asarray(indata[:remaining, 0], dtype=self._np.float32).copy()
                if data.size:
                    if self._buffer_frames + data.size > self._max_buffer_frames:
                        self._errors.append(
                            "La transcription ne suit plus le microphone : le tampon audio est plein. "
                            "La capture a été interrompue ; choisissez Tiny ou Base puis recommencez."
                        )
                        self._accepting = False
                        return
                    self._chunks.append(data)
                    self._frames += data.size
                    self._buffer_frames += data.size
                    self._level = min(1.0, max(0.0, float(self._np.sqrt(self._np.mean(data * data)))))
                if self._max_frames is not None and self._frames >= self._max_frames:
                    self._limit_reached = True
                    self._accepting = False
        except Exception as exc:
            with self._lock:
                self._errors.append(f"Erreur pendant la capture audio : {exc}")
                self._accepting = False

    def _finish_stream(self) -> None:
        with self._lock:
            self._accepting = False
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                with self._lock:
                    self._errors.append(f"Erreur à l'arrêt du microphone : {exc}")
            finally:
                try:
                    stream.close()
                except Exception as exc:
                    with self._lock:
                        self._errors.append(f"Erreur à la fermeture du microphone : {exc}")
        with self._lock:
            self._recording = False
            self._level = 0.0

    def drain(self):
        """Rend les nouveaux échantillons à 16 kHz, sans fermer le microphone.

        Les blocs quittent le tampon sous verrou ; la concaténation et le
        rééchantillonnage se font ensuite hors du callback PortAudio.
        Un seul consommateur doit appeler drain/stop/cancel.
        """
        return self._take_audio(final=False)

    def _take_audio(self, final):
        with self._lock:
            chunks, self._chunks = self._chunks, []
            self._buffer_frames = 0
            errors = tuple(self._errors)
            source_rate = self._source_rate
            np = self._np
        if errors:
            raise RuntimeError("\n".join(errors))
        if np is None:
            np, _ = _dependencies()
        audio = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
        if source_rate != self.sample_rate and (not final or self._resampler is not None):
            if self._resampler is None:
                self._resampler = _StreamingResampler(source_rate, self.sample_rate)
            return self._resampler.convert(audio, final=final)
        return _resample_mono(audio, source_rate, self.sample_rate)

    def stop(self):
        """Ferme la capture et retourne uniquement la fin non encore consommée."""
        self._finish_stream()
        return self._take_audio(final=True)

    def cancel(self) -> None:
        self._finish_stream()
        with self._lock:
            self._chunks = []
            self._buffer_frames = 0
        self._resampler = None


class SpeechChunker:
    """Découpe le flux sur les pauses ; aucune coupure périodique par défaut.

    L'énergie est mesurée sur des fenêtres de 20 ms : le résultat ne dépend
    pas de la taille des blocs PortAudio. Ce découpage léger est complété par
    le détecteur de voix du moteur Whisper. Aucun échantillon parlé n'est
    envoyé deux fois, y compris lors d'une coupure forcée au milieu d'une phrase.
    """

    def __init__(self, sample_rate: int = 16000, silence_seconds: float = 0.65,
                 max_seconds: float | None = None, threshold: float = 0.004,
                 min_speech_seconds: float = 0.15, pre_roll_seconds: float = 0.2):
        if sample_rate <= 0 or silence_seconds <= 0 or (max_seconds is not None and max_seconds < 0.02) or threshold <= 0:
            raise ValueError("Les paramètres de découpage audio doivent être positifs.")
        if min_speech_seconds < 0 or pre_roll_seconds < 0 or (max_seconds is not None and pre_roll_seconds >= max_seconds):
            raise ValueError("La marge avant parole doit être inférieure à la durée maximale.")
        import numpy as np

        self._np = np
        self.sample_rate = int(sample_rate)
        self._window = max(1, round(sample_rate * 0.02))
        self._silence_limit = max(1, round(silence_seconds * sample_rate))
        self._maximum = None if max_seconds is None else max(1, round(max_seconds * sample_rate))
        self._minimum_speech = round(min_speech_seconds * sample_rate)
        self._pre_limit = round(pre_roll_seconds * sample_rate)
        self._trailing = round(0.2 * sample_rate)
        self.threshold = float(threshold)
        self._pending = np.empty(0, dtype=np.float32)
        self._pre = deque()
        self._pre_size = 0
        self._active = False
        self._parts = []
        self._size = self._speech = self._silence = self._last_speech = 0
        self._continuation = False
        self._trusted = False
        self._idle_silence = 0

    def _remember(self, samples):
        if not self._pre_limit or not samples.size:
            return
        self._pre.append(samples.copy())
        self._pre_size += samples.size
        while self._pre_size > self._pre_limit:
            extra = self._pre_size - self._pre_limit
            first = self._pre.popleft()
            if first.size > extra:
                self._pre.appendleft(first[extra:].copy())
                self._pre_size -= extra
            else:
                self._pre_size -= first.size

    def _begin(self):
        self._parts = list(self._pre)
        self._size = self._pre_size
        self._pre.clear()
        self._pre_size = 0
        self._speech = self._silence = self._last_speech = 0
        self._active = True
        self._trusted = self._continuation
        self._idle_silence = 0

    def _finish(self, pause):
        audio = self._np.concatenate(self._parts)
        accepted = self._speech > 0 and (self._speech >= self._minimum_speech or self._trusted)
        end = min(audio.size, self._last_speech + self._trailing) if pause else audio.size
        result = audio[:end].copy() if accepted else None
        self._parts = []
        self._size = self._speech = self._silence = self._last_speech = 0
        self._active = False
        self._trusted = False
        self._continuation = accepted and not pause
        self._idle_silence = 0
        if pause:
            # Seul le silence NON envoyé peut servir d'amorce à la phrase suivante.
            self._remember(audio[end:])
        return result

    def _consume(self, frame):
        results = []
        voiced = float(self._np.sqrt(self._np.mean(frame * frame))) >= self.threshold
        while frame.size:
            if not self._active:
                if not voiced:
                    self._idle_silence += frame.size
                    if self._idle_silence >= self._silence_limit:
                        self._continuation = False
                    self._remember(frame)
                    break
                self._begin()
            count = frame.size if self._maximum is None else min(frame.size, self._maximum - self._size)
            part, frame = frame[:count], frame[count:]
            self._parts.append(part.copy())
            self._size += count
            if voiced:
                self._speech += count
                self._last_speech = self._size
                self._silence = 0
            else:
                self._silence += count
            pause = self._silence >= self._silence_limit
            if self._maximum is None and self._size > 300 * self.sample_rate:
                raise RuntimeError("Cinq minutes sans pause détectée : capture arrêtée. Vérifiez le bruit du microphone et faites une pause entre les phrases.")
            if pause or (self._maximum is not None and self._size >= self._maximum):
                result = self._finish(pause)
                if result is not None:
                    results.append(result)
        return results

    def feed(self, samples):
        """Consomme un bloc mono 16 kHz ; retourne les morceaux terminés."""
        samples = self._np.asarray(samples, dtype=self._np.float32)
        if samples.ndim != 1 or not self._np.isfinite(samples).all():
            raise ValueError("Le flux audio doit être mono et contenir des valeurs finies.")
        if not samples.size:
            return []
        data = self._np.concatenate((self._pending, samples)) if self._pending.size else samples
        end = data.size - data.size % self._window
        self._pending = data[end:].copy()
        result = []
        for offset in range(0, end, self._window):
            result.extend(self._consume(data[offset:offset + self._window]))
        return result

    def flush(self):
        """Termine la dernière phrase une seule fois, puis vide tout le découpeur."""
        result = []
        if self._pending.size:
            pending, self._pending = self._pending, self._np.empty(0, dtype=self._np.float32)
            result.extend(self._consume(pending))
        if self._active:
            last = self._finish(pause=True)
            if last is not None:
                result.append(last)
        self._pre.clear()
        self._pre_size = 0
        self._continuation = False
        self._idle_silence = 0
        return result
