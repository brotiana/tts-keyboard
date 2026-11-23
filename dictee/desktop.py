"""Raccourci et collage X11, sans paquet Python ni privilèges supplémentaires."""
from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import threading
import time
from contextlib import contextmanager


class DesktopError(RuntimeError):
    pass


class X11Desktop:
    """Chaque opération possède sa connexion X11 ; aucun texte frappé n'est lu."""

    def __init__(self):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            raise DesktopError("Session Wayland : utilisez les boutons et Copier, ou ouvrez une session X11 pour F8.")
        if not os.environ.get("DISPLAY"):
            raise DesktopError("Écran X11 indisponible. Lancez l'application depuis votre bureau Linux.")
        try:
            self.x = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            self.xt = C.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        except OSError as exc:
            raise DesktopError("Bibliothèques X11/XTest absentes : installez libx11-6 et libxtst6.") from exc
        # Appelé avant la création de Tk, qui utilise aussi Xlib.
        self.x.XInitThreads.restype = C.c_int
        self.x.XInitThreads()
        self._bind(self.x, "XOpenDisplay", [C.c_char_p], C.c_void_p)
        self._bind(self.x, "XCloseDisplay", [C.c_void_p], C.c_int)
        self._bind(self.x, "XDefaultRootWindow", [C.c_void_p], C.c_ulong)
        self._bind(self.x, "XInternAtom", [C.c_void_p, C.c_char_p, C.c_int], C.c_ulong)
        self._bind(self.x, "XGetWindowProperty", [C.c_void_p, C.c_ulong, C.c_ulong,
                   C.c_long, C.c_long, C.c_int, C.c_ulong, C.POINTER(C.c_ulong),
                   C.POINTER(C.c_int), C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                   C.POINTER(C.POINTER(C.c_ubyte))], C.c_int)
        self._bind(self.x, "XFree", [C.c_void_p], C.c_int)
        self._bind(self.x, "XKeysymToKeycode", [C.c_void_p, C.c_ulong], C.c_ubyte)
        self._bind(self.x, "XQueryKeymap", [C.c_void_p, C.c_void_p], C.c_int)
        self._bind(self.x, "XFlush", [C.c_void_p], C.c_int)
        self._bind(self.xt, "XTestFakeKeyEvent", [C.c_void_p, C.c_uint, C.c_int, C.c_ulong], C.c_int)
        self._bind(self.xt, "XTestQueryExtension", [C.c_void_p] + [C.POINTER(C.c_int)] * 4, C.c_int)
        self._stop = threading.Event()
        self._thread = None
        with self._display() as display:
            values = [C.c_int() for _ in range(4)]
            if not self.xt.XTestQueryExtension(display, *(C.byref(v) for v in values)):
                raise DesktopError("L'extension XTest est indisponible ; utilisez Copier.")

    @staticmethod
    def _bind(lib, name, arguments, result):
        function = getattr(lib, name)
        function.argtypes = arguments
        function.restype = result

    @contextmanager
    def _display(self):
        display = self.x.XOpenDisplay(None)
        if not display:
            raise DesktopError("Connexion au bureau X11 impossible. Le mode Copier reste disponible.")
        try:
            yield display
        finally:
            self.x.XCloseDisplay(display)

    def _active(self, display):
        atom = self.x.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 1)
        if not atom:
            return None
        actual, fmt, count, remaining = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong()
        data = C.POINTER(C.c_ubyte)()
        result = self.x.XGetWindowProperty(display, self.x.XDefaultRootWindow(display),
                atom, 0, 1, 0, 0, C.byref(actual), C.byref(fmt), C.byref(count),
                C.byref(remaining), C.byref(data))
        try:
            if result == 0 and fmt.value == 32 and count.value and data:
                return int(C.cast(data, C.POINTER(C.c_ulong))[0]) or None
            return None
        finally:
            if data:
                self.x.XFree(data)

    def active_window(self):
        with self._display() as display:
            return self._active(display)

    @staticmethod
    def _pressed(keys, code):
        return bool(code and keys[code // 8] & (1 << (code % 8)))

    def start_hotkey(self, on_toggle, on_error):
        """Surveille uniquement F8 ; déclenche au relâchement, sans répétition."""
        def listen():
            try:
                with self._display() as display:
                    code = self.x.XKeysymToKeycode(display, 0xFFC5)  # XK_F8
                    if not code:
                        raise DesktopError("La touche F8 n'existe pas dans la disposition du clavier.")
                    keys = (C.c_ubyte * 32)()
                    was_pressed = False
                    while not self._stop.wait(0.02):
                        self.x.XQueryKeymap(display, keys)
                        pressed = self._pressed(keys, code)
                        if was_pressed and not pressed:
                            on_toggle()
                        was_pressed = pressed
            except Exception as exc:
                on_error(str(exc))

        self._thread = threading.Thread(target=listen, name="raccourci-f8", daemon=True)
        self._thread.start()

    def paste(self, target, terminal=False, cancel_event=None):
        """Le presse-papiers est préparé par Tk avant cet appel en arrière-plan."""
        if not target:
            raise DesktopError("Fenêtre cible inconnue : utilisez Copier puis Ctrl+V.")
        def check_cancelled():
            if cancel_event is not None and cancel_event.is_set():
                raise DesktopError("Collage annulé.")
        check_cancelled()
        with self._display() as display:
            keycode = lambda symbol: int(self.x.XKeysymToKeycode(display, symbol))
            control, shift, letter = keycode(0xFFE3), keycode(0xFFE1), keycode(ord("v"))
            modifiers = [keycode(s) for s in (0xFFE1, 0xFFE2, 0xFFE3, 0xFFE4,
                                             0xFFE7, 0xFFE8, 0xFFE9, 0xFFEA,
                                             0xFFEB, 0xFFEC, 0xFE03, 0xFF7E)]
            keys = (C.c_ubyte * 32)()
            deadline = time.monotonic() + 2
            while True:
                check_cancelled()
                self.x.XQueryKeymap(display, keys)
                if not any(self._pressed(keys, c) for c in modifiers):
                    break
                if time.monotonic() > deadline:
                    raise DesktopError("Collage reporté : relâchez Ctrl/Alt/Maj, puis utilisez Ctrl+V.")
                time.sleep(0.025)
            if self._active(display) != target:
                raise DesktopError("La fenêtre active a changé. Texte copié : collez-le avec Ctrl+V au bon endroit.")
            check_cancelled()
            sequence = [control] + ([shift] if terminal else []) + [letter]
            if not all(sequence):
                raise DesktopError("Raccourci de collage indisponible ; utilisez le menu Coller.")
            pressed = []
            try:
                for code in sequence:
                    if not self.xt.XTestFakeKeyEvent(display, code, 1, 0):
                        raise DesktopError("Le bureau a refusé le collage ; utilisez le menu Coller.")
                    pressed.append(code)
                self.x.XFlush(display)
            finally:
                for code in reversed(pressed):
                    self.xt.XTestFakeKeyEvent(display, code, 0, 0)
                self.x.XFlush(display)

    def submit(self, target, cancel_event):
        """Entrée uniquement si la cible et la session sont encore valides."""
        with self._display() as display:
            if cancel_event.is_set() or self._active(display) != target:
                raise DesktopError("Envoi annulé : le champ ou la fenêtre a changé.")
            keys = (C.c_ubyte * 32)()
            self.x.XQueryKeymap(display, keys)
            for symbol in (0xFFE1, 0xFFE2, 0xFFE3, 0xFFE4, 0xFFE9, 0xFFEA, 0xFFEB, 0xFFEC):
                if self._pressed(keys, self.x.XKeysymToKeycode(display, symbol)):
                    raise DesktopError("Envoi annulé : relâchez les touches de modification.")
            code = self.x.XKeysymToKeycode(display, 0xFF0D)
            if not code:
                raise DesktopError("Touche Entrée indisponible.")
            try:
                self.xt.XTestFakeKeyEvent(display, code, 1, 0)
            finally:
                self.xt.XTestFakeKeyEvent(display, code, 0, 0)
                self.x.XFlush(display)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)
