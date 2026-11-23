import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from dictee.desktop import DesktopError, X11Desktop


class DesktopTests(unittest.TestCase):
    def make_desktop(self, active_window=42):
        desktop = X11Desktop.__new__(X11Desktop)
        display = object()
        keycodes = {0xFFE3: 37, 0xFFE1: 50, ord("v"): 55}
        desktop.x = SimpleNamespace(
            XKeysymToKeycode=Mock(side_effect=lambda connection, symbol: keycodes.get(symbol, 0)),
            XQueryKeymap=Mock(return_value=1),
            XFlush=Mock(),
        )
        desktop.xt = SimpleNamespace(XTestFakeKeyEvent=Mock(return_value=1))
        desktop._display = lambda: nullcontext(display)
        desktop._active = Mock(return_value=active_window)
        return desktop, display

    def test_wayland_explicitly_falls_back_to_copy(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}):
            with self.assertRaisesRegex(DesktopError, "Wayland"):
                X11Desktop()

    def test_only_requested_key_bit_is_read(self):
        keys = bytearray(32)
        keys[74 // 8] = 1 << (74 % 8)
        self.assertTrue(X11Desktop._pressed(keys, 74))
        self.assertFalse(X11Desktop._pressed(keys, 73))
        self.assertFalse(X11Desktop._pressed(keys, 0))

    def test_unknown_target_never_generates_keystrokes(self):
        desktop = X11Desktop.__new__(X11Desktop)
        with self.assertRaisesRegex(DesktopError, "cible inconnue"):
            desktop.paste(None)

    def test_changed_active_window_never_generates_keystrokes(self):
        desktop, _ = self.make_desktop(active_window=99)
        with self.assertRaisesRegex(DesktopError, "fenêtre active a changé"):
            desktop.paste(target=42)
        desktop.xt.XTestFakeKeyEvent.assert_not_called()

    def test_failed_press_releases_previously_pressed_modifier(self):
        desktop, display = self.make_desktop()
        desktop.xt.XTestFakeKeyEvent.side_effect = [1, 0, 1]
        with self.assertRaisesRegex(DesktopError, "refusé le collage"):
            desktop.paste(target=42)
        self.assertEqual(desktop.xt.XTestFakeKeyEvent.call_args_list, [
            call(display, 37, 1, 0),
            call(display, 55, 1, 0),
            call(display, 37, 0, 0),
        ])
        desktop.x.XFlush.assert_called_once_with(display)

    def test_terminal_paste_presses_control_shift_v_and_releases_in_reverse(self):
        desktop, display = self.make_desktop()
        desktop.paste(target=42, terminal=True)
        self.assertEqual(desktop.xt.XTestFakeKeyEvent.call_args_list, [
            call(display, 37, 1, 0),
            call(display, 50, 1, 0),
            call(display, 55, 1, 0),
            call(display, 55, 0, 0),
            call(display, 50, 0, 0),
            call(display, 37, 0, 0),
        ])

    def test_held_modifier_times_out_without_injecting_keys(self):
        desktop, _ = self.make_desktop()

        def held_shift(display, keys):
            keys[50 // 8] = 1 << (50 % 8)
            return 1

        desktop.x.XQueryKeymap.side_effect = held_shift
        with patch("dictee.desktop.time.monotonic", side_effect=[0, 3]):
            with self.assertRaisesRegex(DesktopError, "relâchez"):
                desktop.paste(target=42)
        desktop.xt.XTestFakeKeyEvent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
