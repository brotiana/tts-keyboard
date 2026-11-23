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

    def test_changed_field_never_queues_message(self):
        app=self.app()
        app.closed=False
        app.session_id=3
        app.mic_indicator=Mock()
        app._controls=Mock()
        app.focus_watcher=Mock(token=99)
        app.paste_abort=threading.Event()
        app.paste_queue=deque()
        app._receive_text('Bonjour. Terminé !')
        app._stream_event(3, 'done', None)
        self.assertEqual(list(app.paste_queue), [])
        self.assertTrue(app.paste_blocked)
