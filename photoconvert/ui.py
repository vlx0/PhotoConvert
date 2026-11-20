"""Dark Russian interface for file batches."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .converter import Cancelled, EXTENSIONS, QUALITY_FORMATS, choices, convert
from .instance import TITLE


BG, CARD, BTN, FG, MUTED = '#111111', '#1a1a1a', '#333333', '#ffffff', '#999999'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.geometry('680x590')
        self.minsize(580, 490)
        self.configure(bg=BG)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.files = {}
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = self.closing = False
        self.folder = tk.StringVar()
        self.format = tk.StringVar(value='PNG')
        self.quality = tk.IntVar(value=90)
        self.status = tk.StringVar(value='Добавьте файлы и выберите папку для результата.')
        self.controls = []

        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=FG,
                        rowheight=29, borderwidth=0, font=('Segoe UI', 9))
        style.configure('Treeview.Heading', background=BTN, foreground=FG, relief='flat')
        style.map('Treeview', background=[('selected', '#29445b')])
        style.configure('Vertical.TScrollbar', background=BTN, troughcolor=CARD,
                        arrowcolor=MUTED, bordercolor=CARD, lightcolor=BTN, darkcolor=BTN)
        style.configure('Horizontal.TProgressbar', background='#79aee3', troughcolor=CARD,
                        bordercolor=BG, lightcolor='#79aee3', darkcolor='#79aee3')

        outer = tk.Frame(self, bg=BG, padx=22, pady=16)
        outer.pack(fill='both', expand=True)
        self.label(outer, 'PhotoConvert', size=22).pack(anchor='w')
        self.label(outer, 'Фото, аудио, видео, таблицы, документы и архивы', MUTED).pack(anchor='w', pady=(3, 12))
        settings = tk.Frame(outer, bg=CARD, padx=12, pady=8)
        settings.pack(fill='x')
        self.label(settings, 'Формат', MUTED, bg=CARD).pack(side='left', padx=(0, 8))
        style.configure('Dark.TCombobox', fieldbackground=BTN, background=BTN, foreground=FG)
        style.map('Dark.TCombobox', fieldbackground=[('readonly', BTN)],
                  foreground=[('readonly', FG)], selectbackground=[('readonly', BTN)])
        self.option_add('*TCombobox*Listbox.background', CARD)
        self.option_add('*TCombobox*Listbox.foreground', FG)
        self.format_box = ttk.Combobox(settings, textvariable=self.format, width=10,
                                       style='Dark.TCombobox', state='readonly')
        self.format_box.pack(side='left')
        self.format_box.bind('<<ComboboxSelected>>', lambda _: self.sync())
        self.quality_box = tk.Spinbox(settings, from_=1, to=100, width=4, textvariable=self.quality,
                                     bg=BTN, fg=FG, buttonbackground=BTN, insertbackground=FG)
        self.quality_box.pack(side='right')
        self.label(settings, 'Качество фото', MUTED, bg=CARD).pack(side='right', padx=8)

        folder = tk.Frame(outer, bg=CARD, padx=12, pady=7)
        folder.pack(fill='x', pady=(6, 12))
        self.button(folder, 'Папка…', self.choose_folder).pack(side='right')
        self.label(folder, 'Куда', MUTED, bg=CARD).pack(side='left', padx=(0, 10))
        tk.Entry(folder, textvariable=self.folder, state='readonly', readonlybackground=CARD,
                 fg=FG, relief='flat', font=('Segoe UI', 9)).pack(side='left', fill='x', expand=True)

        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill='x', pady=(0, 7))
        self.button(actions, '+ Добавить файлы', self.add).pack(side='left')
        self.button(actions, 'Убрать выбранные', self.remove).pack(side='left', padx=6)
        self.button(actions, 'Очистить', self.clear).pack(side='right')
        results = tk.Frame(outer, bg=BG)
        results.pack(fill='both', expand=True)
        self.table = ttk.Treeview(results, columns=('name', 'status'), show='headings', height=5)
        self.table.heading('name', text='Исходный файл', anchor='w')
        self.table.heading('status', text='Результат', anchor='w')
        self.table.column('name', width=280, minwidth=125)
        self.table.column('status', width=220, minwidth=140)
        self.table.tag_configure('ok', foreground='#86efac')
        self.table.tag_configure('error', foreground='#fca5a5')
        self.table.tag_configure('cancel', foreground='#fde68a')
        self.table.bind('<Double-1>', self.details)
        scroll = ttk.Scrollbar(results, command=self.table.yview)
        scroll.pack(side='right', fill='y')
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='both', expand=True)
        self.details_by_id = {}

        self.progress = ttk.Progressbar(outer, mode='determinate')
        self.progress.pack(fill='x', pady=(11, 5))
        self.label(outer, '', MUTED, variable=self.status).pack(fill='x')
        run = tk.Frame(outer, bg=BG)
        run.pack(fill='x', pady=(9, 0))
        self.start_button = self.button(run, 'Конвертировать', self.start, bg='#29445b')
        self.start_button.pack(side='left')
        self.cancel_button = self.button(run, 'Стоп', self.cancel, managed=False)
        self.cancel_button.pack(side='left', padx=6)
        self.button(run, 'Открыть папку', self.open_folder).pack(side='right')
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill='x', pady=(10, 0))
        self.label(footer, 'неделя 13 · день 89', MUTED, size=8).pack(side='left')
        self.label(footer, 'darkshade · MIT', MUTED, size=8).pack(side='right')
        self.sync()
        self.after(100, self.poll)

    def label(self, parent, text, color=FG, size=9, bg=BG, variable=None):
        return tk.Label(parent, text=text, textvariable=variable, bg=bg, fg=color, anchor='w',
                        font=('Segoe UI', size))

    def button(self, parent, text, command, managed=True, bg=BTN):
        button = tk.Button(parent, text=text, command=command, bg=bg, fg=FG,
                           activebackground='#444444', activeforeground=FG, relief='flat',
                           bd=0, padx=10, pady=6, font=('Segoe UI', 9))
        if managed:
            self.controls.append(button)
        return button

    def sync(self):
        output_formats = {name for path in {p.suffix.lower() for p in self.files.values()}
                          for name in choices('file' + path)}
        if not self.files:
            output_formats = (EXTENSIONS.keys() - {'WebP', 'AVIF'}) | set(choices('photo.png'))
        ordered = [name for name in EXTENSIONS if name in output_formats]
        selected = self.format.get()
        if selected not in ordered:
            self.format.set(ordered[0] if ordered else '')
        self.format_box.configure(values=ordered, state='readonly' if ordered and not self.busy else 'disabled')
        for widget in self.controls:
            widget.configure(state='disabled' if self.busy else 'normal')
        self.start_button.configure(state='normal' if self.files and ordered and not self.busy else 'disabled')
        self.cancel_button.configure(state='normal' if self.busy and not self.stop.is_set() else 'disabled')
        self.quality_box.configure(state='normal' if self.format.get() in QUALITY_FORMATS and not self.busy else 'disabled')

    def add(self):
        paths = filedialog.askopenfilenames(parent=self, title='Добавить файлы',
                                          filetypes=[('Все файлы', '*.*')])
        existing = {os.path.normcase(str(path)) for path in self.files.values()}
        for name in paths:
            path = Path(name).absolute()
            key = os.path.normcase(str(path))
            if key not in existing:
                row = self.table.insert('', 'end', values=(path.name, 'В очереди'))
                self.files[row] = path
                existing.add(key)
        if paths:
            previous_format = self.format.get()
            self.sync()
            new_format = self.format.get()
            change = f' · {previous_format} недоступен, выбран {new_format}' if previous_format != new_format else ''
            self.status.set(f'Файлов в списке: {len(self.files)}{change}')

    def remove(self):
        for row in self.table.selection():
            self.table.delete(row)
            self.files.pop(row)
            self.details_by_id.pop(row, None)
        self.status.set(f'Файлов в списке: {len(self.files)}')
        self.sync()

    def clear(self):
        for row in self.table.get_children():
            self.table.delete(row)
        self.files.clear()
        self.details_by_id.clear()
        self.progress.configure(value=0)
        self.status.set('Добавьте файлы.')
        self.sync()

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None, mustexist=True)
        if path:
            self.folder.set(path)

    def open_folder(self):
        if not self.folder.get():
            self.choose_folder()
        if self.folder.get():
            try:
                os.startfile(self.folder.get())
            except OSError as error:
                messagebox.showerror('Не удалось открыть папку', str(error), parent=self)

    def details(self, event):
        row = self.table.identify_row(event.y)
        if row:
            messagebox.showinfo('Сведения', str(self.files[row]) + '\n\n' +
                                self.details_by_id.get(row, 'Ожидает обработки.'), parent=self)

    def start(self):
        if self.busy or not self.files:
            return
        fmt = self.format.get()
        try:
            quality = self.quality.get() if fmt in QUALITY_FORMATS else 90
            if not 1 <= quality <= 100:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror('Качество фото', 'Введите целое число от 1 до 100.', parent=self)
            return
        if not self.folder.get():
            self.choose_folder()
        folder = self.folder.get()
        if not folder:
            return
        batch = list(self.files.items())
        for row, source in batch:
            self.table.item(row, values=(source.name, 'В очереди'), tags=())
        self.details_by_id.clear()
        self.progress.configure(maximum=len(batch), value=0)
        self.status.set(f'Обработка 0 из {len(batch)}…')
        self.stop.clear()
        self.busy = True
        self.sync()
        threading.Thread(target=self.work, args=(batch, folder, fmt, quality)).start()

    def work(self, batch, folder, fmt, quality):
        counts = dict.fromkeys(('ok', 'error', 'cancel', 'skip'), 0)

        def handle(item):
            row, source = item
            try:
                if fmt not in choices(source):
                    return row, 'skip', 'Пропущено', f'Для этого файла формат {fmt} недоступен.'
                destination = convert(source, folder, fmt, quality, self.stop)
                return row, 'ok', destination.name, str(destination)
            except Cancelled:
                return row, 'cancel', 'Остановлено', 'Файл не был сохранён.'
            except Exception as error:
                return row, 'error', 'Ошибка · двойной клик', str(error)

        try:
            with ThreadPoolExecutor(max_workers=min(2, len(batch))) as pool:
                for future in as_completed(pool.submit(handle, item) for item in batch):
                    entry = future.result()
                    counts[entry[1]] += 1
                    self.events.put(('row', entry))
        finally:
            self.events.put(('done', counts))

    def cancel(self):
        self.stop.set()
        self.status.set('Остановка…')
        self.sync()

    def poll(self):
        for _ in range(150):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'row':
                row, tag, caption, detail = payload
                self.table.item(row, values=(self.files[row].name, caption), tags=(tag,))
                self.details_by_id[row] = detail
                self.progress.configure(value=float(self.progress['value']) + 1)
                if not self.stop.is_set():
                    self.status.set(f'Обработано {int(self.progress["value"])} из {len(self.files)}')
            else:
                self.busy = False
                if self.closing:
                    self.destroy()
                    return
                self.status.set(f'Сохранено: {payload["ok"]} · Ошибки: {payload["error"]} · '
                                f'Пропущено: {payload["skip"]} · Остановлено: {payload["cancel"]}')
                self.sync()
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel()
        else:
            self.destroy()
