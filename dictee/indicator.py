"""Petit panneau déplaçable ; toutes les méthodes sont appelées par Tk."""
import tkinter as tk
from tkinter import ttk


class DictationIndicator(tk.Toplevel):
    def __init__(self, root):
        super().__init__(root)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.attributes('-type', 'tooltip')
        self.configure(takefocus=False, bg='#176455')
        self.geometry(f'+{max(0, root.winfo_screenwidth()-360)}+35')
        self._offset = None
        self._processing = False
        self.label = tk.Label(
            self, text='● Dictée active — dites « terminé »', width=32, font=('Sans', 9),
            bg='#176455', fg='white', padx=9, pady=7,
        )
        self.label.pack()
        ttk.Style(self).configure('Voice.Horizontal.TProgressbar', thickness=3,
                                  borderwidth=0, background='#9fcbbf', troughcolor='#176455')
        self.progress = ttk.Progressbar(self, mode='indeterminate', length=240,
                                        style='Voice.Horizontal.TProgressbar')
        self.progress.pack(fill='x', padx=9, pady=(0, 5))
        for widget in (self, self.label, self.progress):
            widget.configure(cursor='fleur')
            widget.bind('<ButtonPress-1>', self._begin_drag)
            widget.bind('<B1-Motion>', self._drag)
            widget.bind('<ButtonRelease-1>', self._end_drag)

    def _begin_drag(self, event):
        self._offset = (event.x_root-self.winfo_x(), event.y_root-self.winfo_y())
        return 'break'

    def _drag(self, event):
        if self._offset is not None:
            x = max(0, min(event.x_root-self._offset[0], self.winfo_screenwidth()-self.winfo_width()))
            y = max(0, min(event.y_root-self._offset[1], self.winfo_screenheight()-self.winfo_height()))
            self.geometry(f'+{x}+{y}')
        return 'break'

    def _end_drag(self, event):
        self._offset = None
        return 'break'

    def set_processing(self, active):
        active = bool(active)
        if active == self._processing:
            return
        self._processing = active
        if active:
            self.label.configure(text='Audio reçu · transcription…')
            self.progress.start(65)
        else:
            self.progress.stop()
            self.progress['value'] = 0
            self.label.configure(text='● Dictée active — dites « terminé »')

    def withdraw(self):
        if hasattr(self, 'progress'):
            self.set_processing(False)
        super().withdraw()

    def destroy(self):
        if hasattr(self, 'progress'):
            self.progress.stop()
        super().destroy()
