import tempfile
import json
from pathlib import Path
from unittest.mock import Mock
import unittest
import threading
from app import Application

class DeliveryTests(unittest.TestCase):
    def make_app(self):
        app=Application.__new__(Application)
        app.closed=False
        app.session_id=1
        app.root=Mock()
        app.cancelled=False
        app.voice_target=12
        app.voice_finish=True
        app.session_send=False
        app.target=123
        app.desktop=Mock()
        app.paste_abort=threading.Event()
        app._pump_paste=Mock()
        return app

    def test_paste_only_never_sends_enter(self):
        app=self.make_app()
        app._paste_finished(1,None)
        app.root.after.call_args.args[1]()
        app.desktop.submit.assert_not_called()
        self.assertTrue(app.delivered)

    def test_send_mode_sends_once_after_clipboard_delay(self):
        app=self.make_app()
        app.session_send=True
        app._paste_finished(1,None)
        app.desktop.submit.assert_not_called()
        app.root.after.call_args.args[1]()
        app.desktop.submit.assert_called_once_with(None,app.paste_abort,check_target=False)

    def test_choice_is_saved(self):
        app=self.make_app()
        app.selected_model=Mock(return_value=None)
        app.auto_var=Mock(get=Mock(return_value=True))
        app.terminal_var=Mock(get=Mock(return_value=False))
        app.mic_var=Mock(get=Mock(return_value='default'))
        app.send_var=Mock(get=Mock(return_value=False))
        app.focus_var=Mock(get=Mock(return_value=True))
        with tempfile.TemporaryDirectory() as folder:
            app.config_path=Path(folder)/'preferences.json'
            app._save_preferences()
            self.assertIs(json.loads(app.config_path.read_text())['send_after_paste'],False)

    def test_failed_delivery_copies_text_for_manual_recovery(self):
        app=self.make_app()
        app.delivered=False
        app.session_text=['Bonjour.']
        app._clipboard=Mock()
        app.status_var=Mock()
        app.level={}
        app._controls=Mock()
        app._save_preferences=Mock()
        app._idle('Champ changé.')
        app._clipboard.assert_called_once_with('Bonjour.')
        self.assertIn('Ctrl+V',app.status_var.set.call_args.args[0])
