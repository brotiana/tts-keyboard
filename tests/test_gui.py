"""Contrats de l'interface de dictée continue, sans écran ni microphone."""
from collections import deque
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock
from app import Application

class ApplicationStateTests(unittest.TestCase):
    def make_app(self):
        app=Application.__new__(Application)
        app.closed=False
        app.cancelled=False
        app.session_done=False
        app.session_id=2
        app.session_text=[]
        app.state='recording'
        app.text=Mock()
        app.text.get.return_value=''
        app.status_var=Mock()
        app.paste_requested=False
        app.paste_warning=''
        app.paste_queue=deque()
        app.paste_abort=threading.Event()
        app.paste_busy=False
        app.session=Mock()
        app._controls=Mock()
        app._maybe_finish=Mock()
        return app

    def test_text_arrives_without_stopping_microphone(self):
        app=self.make_app()
        app._receive_text('Bonjour.')
        self.assertEqual(app.state,'recording')
        app.text.insert.assert_called_once_with('end','Bonjour.')
        app.session.stop.assert_not_called()

    def test_cancel_suppresses_unfinished_results(self):
        app=self.make_app()
        app.cancel()
        app._receive_text('À ignorer.')
        app.text.insert.assert_not_called()
        self.assertTrue(app.paste_abort.is_set())
        app.session.cancel.assert_called_once()

    def test_previous_session_cannot_insert_text(self):
        app=self.make_app()
        app._stream_event(1,'text','Ancien résultat')
        app.text.insert.assert_not_called()

    def test_explicit_stop_flushes_session(self):
        app=self.make_app()
        app.stop_recording()
        self.assertEqual(app.state,'finishing')
        app.session.stop.assert_called_once()
