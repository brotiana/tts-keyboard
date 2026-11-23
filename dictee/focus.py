"""Pont vers AT-SPI installé sur Linux Mint."""
import json
from pathlib import Path
import subprocess
import threading


class FocusWatcher:
    def __init__(self, callback, invalidate):
        self.token = None
        self.callback = callback
        self.invalidate = invalidate
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            ['/usr/bin/python3', str(Path(__file__).with_name('focus_watch.py'))],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        def read():
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'focus' in message:
                    if self.token != message['focus']:
                        self.invalidate()
                    self.token = message['focus']
                self.callback(message)
            self.token = None
            self.invalidate()
            self.callback({'error': 'La surveillance des champs est arrêtée.'})
        threading.Thread(target=read, daemon=True).start()

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
