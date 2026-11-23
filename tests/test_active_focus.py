import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from dictee.focus_watch import active_window
from app import Application

class FocusTests(unittest.TestCase):
    def test_focused_child_in_inactive_window_is_rejected(self):
        api=SimpleNamespace(ROLE_FRAME=1, ROLE_DIALOG=2, STATE_ACTIVE=3)
        frame=Mock()
        frame.getRole.return_value=1
        frame.getState.return_value.contains.return_value=False
        child=Mock()
        child.parent=frame
        child.getRole.return_value=5
        self.assertFalse(active_window(child,api))
        frame.getState.return_value.contains.return_value=True
        self.assertTrue(active_window(child,api))

    def test_pending_field_starts_after_previous_session_finishes(self):
        app=Application.__new__(Application)
        app.closed=False
        app.state='idle'
        app.focus_var=Mock(get=Mock(return_value=True))
        app.desktop=Mock()
        app.focus_watcher=Mock(token=17)
        app.toggle=Mock()
        app._resume_pending_focus(17)
        app.toggle.assert_called_once_with(True,voice_target=17)

    def test_old_pending_field_never_starts(self):
        app=Application.__new__(Application)
        app.closed=False
        app.state='idle'
        app.focus_var=Mock(get=Mock(return_value=True))
        app.desktop=Mock()
        app.focus_watcher=Mock(token=18)
        app.toggle=Mock()
        app._resume_pending_focus(17)
        app.toggle.assert_not_called()
