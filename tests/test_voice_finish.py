import unittest
import threading
from collections import deque
from unittest.mock import Mock
from app import Application

class VoiceFinishTests(unittest.TestCase):
    def app(self):
        app=Application.__new__(Application)
        app.cancelled=False
        app.session_done=False
        app.voice_target=12
        app.voice_finish=False
        app.stop_recording=Mock()
        app.text=Mock()
        app.text.get.return_value=''
        app.session_text=[]
        app.state='recording'
        app.status_var=Mock()
        app.paste_warning=''
        app.paste_requested=True
        app._pump_paste=Mock()
        app.desktop=Mock()
        app.desktop.active_window.return_value=123
        return app
    def test_command_stops_without_becoming_message_text(self):
        app=self.app()
        app._receive_text('Terminé !')
        self.assertTrue(app.voice_finish)
        app.stop_recording.assert_called_once()
        app.text.insert.assert_not_called()
    def test_word_in_sentence_does_not_submit(self):
        app=self.app()
        app._receive_text("J’ai terminé mon travail.")
        app.stop_recording.assert_not_called()
        app._pump_paste.assert_not_called()
        self.assertEqual(app.session_text,["J’ai terminé mon travail."])
    def test_late_text_after_command_is_ignored(self):
        app=self.app()
        app.voice_finish=True
        app._receive_text('Bruit après la commande')
        app.text.insert.assert_not_called()

    def test_command_at_end_of_same_segment_keeps_message(self):
        app=self.app()
        app._receive_text('Bonjour, voici mon message. Terminé.')
        self.assertEqual(app.session_text, ['Bonjour, voici mon message.'])
        self.assertTrue(app.voice_finish)
        app.stop_recording.assert_called_once()

    def test_finish_event_queues_entire_message_for_original_field(self):
        app=self.app()
        app.closed=False
        app.session_id=3
        app.mic_indicator=Mock()
        app._controls=Mock()
        app.focus_watcher=Mock(token=12)
        app.paste_abort=threading.Event()
        app.paste_queue=deque()
        app._receive_text('Bonjour. Terminé !')
        app._stream_event(3, 'done', None)
        self.assertEqual(list(app.paste_queue), ['Bonjour.'])
        app._pump_paste.assert_called_once()

    def test_no_field_detection_is_needed_to_queue_paste(self):
        app=self.app()
        app.focus_watcher=None
        app.paste_queue=deque()
        app.session_text=['Bonjour.']
        app._deliver_to_current_field()
        self.assertEqual(list(app.paste_queue),['Bonjour.'])
        app.desktop.active_window.assert_not_called()

    def test_focus_changes_do_not_block_direct_paste(self):
        app=self.app()
        app.focus_watcher=Mock(token=None)
        app.paste_queue=deque()
        app.session_text=['Bonjour.']
        app._deliver_to_current_field()
        self.assertFalse(app.paste_blocked)
        self.assertEqual(list(app.paste_queue),['Bonjour.'])
        app.desktop.active_window.assert_not_called()

    def test_restart_after_finished_message_requires_no_click(self):
        app=self.app()
        app.closed=False
        app.state='idle'
        app.session_id=3
        app.toggle=Mock()
        app._restart_listening(3)
        app.toggle.assert_called_once_with(True)

    def test_cancel_prevents_scheduled_restart(self):
        app=self.app()
        app.closed=False
        app.state='idle'
        app.session_id=3
        app.cancelled=True
        app.toggle=Mock()
        app._restart_listening(3)
        app.toggle.assert_not_called()

    def test_auto_listening_does_not_require_a_focused_field(self):
        app=self.app()
        app.closed=False
        app.state='idle'
        app.focus_var=Mock(get=Mock(return_value=True))
        app.focus_watcher=Mock(token=None)
        app.toggle=Mock()
        app._auto_listen()
        app.toggle.assert_called_once_with(True)
