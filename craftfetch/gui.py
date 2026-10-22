"""Blue-and-white desktop catalogue with asynchronous author images and detail pages."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
import webbrowser

from PIL import Image, ImageTk

from . import __version__
from .catalog import Catalog, KINDS, primary_file
from .core import Cancelled, Downloader, DownloadError
from .instances import create_instance, list_instances, read_instance
from .packs import download_pack, install_pack
from .previews import PreviewService, https_url, project_url
from .runtime import install_java

BLUE = '#1479dc'
HEADER = '#116fca'
BG = '#dfeafa'
WHITE = '#ffffff'
TEXT = '#304052'
MUTED = '#7a899b'
BORDER = '#d9e5f1'
LIGHT = '#edf6ff'
CATEGORIES = [('原版', 'vanilla', 'box'), ('Mod', 'mod', 'puzzle'), ('整合包', 'modpack', 'package'),
              ('数据包', 'datapack', 'circles'), ('资源包', 'resourcepack', 'photo'), ('光影包', 'shader', 'sun')]
LOADERS = ('fabric', 'neoforge', 'forge', 'quilt')


def mib(value):
    return f'{value / 1024 / 1024:,.1f} MiB'


def compact_number(value):
    if value >= 100_000_000:
        return f'{value / 100_000_000:.2f} 亿'
    if value >= 10000:
        return f'{value / 10000:.1f} 万'
    return f'{value:,}'


def shortened(text, limit=100):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


class ScrollFrame(tk.Frame):
    def __init__(self, parent, color=BG):
        super().__init__(parent, bg=color)
        self.canvas = tk.Canvas(self, bg=color, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = tk.Frame(self.canvas, bg=color)
        self.item = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self.content.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.item, width=e.width))

    def top(self):
        self.canvas.yview_moveto(0)


class FlatButton(tk.Frame):
    """A keyboard-operable flat button with consistent colours on macOS/Windows."""
    def __init__(self, parent, text='', command=None, image=None, compound='left', font=None,
                 bg=WHITE, fg=BLUE, activebackground=LIGHT, activeforeground=BLUE, padx=13,
                 pady=8, highlightthickness=0, highlightbackground=BORDER, anchor='center', **_):
        super().__init__(parent, bg=bg, highlightthickness=highlightthickness,
                         highlightbackground=highlightbackground, takefocus=True, cursor='hand2')
        self.command = command
        self.state = 'normal'
        self.normal_bg, self.normal_fg = bg, fg
        self.hover_bg, self.hover_fg = activebackground, activeforeground
        self.caption = tk.Label(self, text=text, image=image, compound=compound, font=font,
                                bg=bg, fg=fg, padx=padx, pady=pady, bd=0, anchor=anchor, cursor='hand2')
        self.caption.pack(fill='both', expand=True)
        for widget in (self, self.caption):
            widget.bind('<Button-1>', lambda _: self.invoke())
            widget.bind('<Enter>', lambda _: self.paint(True))
            widget.bind('<Leave>', lambda _: self.paint(False))
        self.bind('<Return>', lambda _: self.invoke())
        self.bind('<space>', lambda _: self.invoke())
        self.bind('<FocusIn>', lambda _: super(FlatButton, self).configure(highlightbackground=BLUE, highlightthickness=1))
        self.bind('<FocusOut>', lambda _: super(FlatButton, self).configure(highlightbackground=highlightbackground, highlightthickness=highlightthickness))

    def invoke(self):
        if self.state != 'disabled' and self.command:
            self.command()

    def paint(self, hover=False):
        bg = self.hover_bg if hover and self.state != 'disabled' else self.normal_bg
        fg = '#a3b0be' if self.state == 'disabled' else self.hover_fg if hover else self.normal_fg
        super().configure(bg=bg)
        self.caption.configure(bg=bg, fg=fg)

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if 'state' in kwargs:
            self.state = kwargs.pop('state')
        if 'bg' in kwargs:
            self.normal_bg = kwargs.pop('bg')
        if 'fg' in kwargs:
            self.normal_fg = kwargs.pop('fg')
        for name in ('text', 'image'):
            if name in kwargs:
                self.caption.configure(**{name: kwargs.pop(name)})
        super().configure(**kwargs)
        self.paint()

    config = configure


class App:
    def __init__(self, window):
        self.window = window
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.busy = self.closing = False
        self.operation = ''
        self.pending_action = None
        self.generation = 0
        self.category = 'vanilla'
        self.current_page = 'vanilla'
        self.manifest = None
        self.entries = {}
        self.projects = []
        self.project = None
        self.project_hit = None
        self.project_kind = ''
        self.all_resource_versions = []
        self.resource_versions = []
        self.version_entries = {}
        self.icon_cache = {}
        self.image_targets = {}
        self.preview_photos = {}
        self.image_sequence = 0
        self.page_offset = self.total_hits = 0
        self.controls = []
        self.combos = []
        self.dialogs = {}
        self.log_lines = []
        self.previews = PreviewService(self.messages)
        self.search = tk.StringVar()
        self.snapshots = tk.BooleanVar(value=False)
        self.source = tk.StringVar(value='官方源')
        self.catalogue_source = tk.StringVar(value='Modrinth')
        default = str(Path(os.environ['APPDATA']) / '.minecraft') if 'APPDATA' in os.environ else ''
        self.directory = tk.StringVar(value=default)
        self.auto_java = tk.BooleanVar(value=True)
        self.loader = tk.StringVar(value='vanilla')
        self.loader_version = tk.StringVar()
        self.instance_name = tk.StringVar()
        self.query = tk.StringVar()
        self.target = tk.StringVar()
        self.game_filter = tk.StringVar(value='全部')
        self.loader_filter = tk.StringVar(value='全部')
        self.world = tk.StringVar()
        self.pack_name = tk.StringVar(value='My-Modpack')
        self.optional = tk.BooleanVar(value=False)
        self.detail_game = tk.StringVar(value='全部')
        self.detail_loader = tk.StringVar(value='全部')
        self.only_release = tk.BooleanVar(value=False)
        self.java_major = tk.StringVar(value='21')
        self.java_path = tk.StringVar()
        self.status = tk.StringVar(value='选择左侧分类，发现适合你的游戏内容。')
        self.detail = tk.StringVar(value='游戏与资源保存在文件夹中，下载后交给 PCL 启动。')
        self.target_info = tk.StringVar(value='尚未选择目标实例')
        self.directory_caption = tk.StringVar(value=default or '尚未选择游戏目录，安装前可设置')
        self.selection_info = tk.StringVar(value='请选择作者发布的版本')
        self.search_caption = tk.StringVar(value='搜索 Mod')
        self.results_caption = tk.StringVar(value='热门项目')
        self.font_family = 'Microsoft YaHei UI' if sys.platform == 'win32' else ('PingFang SC' if sys.platform == 'darwin' else 'DejaVu Sans')
        window.title(f'CraftFetch · Minecraft 下载器 {__version__}')
        window.geometry('1100x780')
        window.minsize(980, 690)
        window.configure(bg=BG)
        self.configure_style()
        self.build_shell()
        self.build_vanilla()
        self.build_catalogue()
        self.build_project_detail()
        self.show_page('vanilla')
        self.directory.trace_add('write', lambda *_: self.directory_caption.set(self.directory.get() or '尚未选择游戏目录，安装前可设置'))
        self.search.trace_add('write', lambda *_: self.populate())
        self.loader.trace_add('write', lambda *_: self.suggest_name())
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind_all('<MouseWheel>', self.on_wheel, add='+')
        window.bind_all('<Button-4>', self.on_wheel, add='+')
        window.bind_all('<Button-5>', self.on_wheel, add='+')
        window.after(80, self.poll)
        window.after(120, self.load_versions)
        self.scan_instances()

    def configure_style(self):
        style = ttk.Style(self.window)
        style.theme_use('clam')
        font = (self.font_family, 10)
        style.configure('TEntry', fieldbackground=WHITE, bordercolor='#9ebfe4', lightcolor=WHITE,
                        darkcolor=WHITE, padding=7, foreground=TEXT, font=font)
        style.configure('TCombobox', fieldbackground=WHITE, background=WHITE, bordercolor='#9ebfe4',
                        lightcolor=WHITE, darkcolor=WHITE, arrowsize=13, padding=6, foreground=TEXT, font=font)
        style.map('TCombobox', fieldbackground=[('readonly', WHITE)], selectbackground=[('readonly', WHITE)],
                  selectforeground=[('readonly', TEXT)])
        style.configure('Treeview', background=WHITE, fieldbackground=WHITE, foreground=TEXT,
                        borderwidth=0, rowheight=39, font=font)
        style.configure('Treeview.Heading', background='#f6f9fd', foreground=MUTED, borderwidth=0,
                        font=(self.font_family, 10), padding=(10, 8))
        style.map('Treeview', background=[('selected', '#e7f2ff')], foreground=[('selected', BLUE)])
        style.configure('Vertical.TScrollbar', background='#ccddeb', troughcolor=BG, borderwidth=0, arrowsize=11)
        style.configure('Thin.Horizontal.TProgressbar', background=BLUE, troughcolor='#dce9f7',
                        bordercolor='#dce9f7', lightcolor=BLUE, darkcolor=BLUE, borderwidth=0, thickness=4)
        self.window.option_add('*TCombobox*Listbox.font', font)
        self.window.option_add('*TCombobox*Listbox.background', WHITE)
        self.window.option_add('*TCombobox*Listbox.foreground', TEXT)

    def icon(self, name, color='muted', size=22):
        key = name, color, size
        if key not in self.icon_cache:
            path = Path(__file__).parent / 'assets/icons' / f'{name}-{color}.png'
            with Image.open(path) as picture:
                self.icon_cache[key] = ImageTk.PhotoImage(picture.resize((size, size), Image.Resampling.LANCZOS), master=self.window)
        return self.icon_cache[key]

    def label(self, parent, text='', *, variable=None, size=11, bold=False, color=TEXT, bg=None, **kwargs):
        return tk.Label(parent, text=text, textvariable=variable, font=(self.font_family, size, 'bold' if bold else 'normal'),
                        bg=bg or parent.cget('bg'), fg=color, anchor=kwargs.pop('anchor', 'w'), bd=0, **kwargs)

    def button(self, parent, text, command, *, primary=False, icon=None, quiet=False, track=True):
        bg = BLUE if primary else (parent.cget('bg') if quiet else WHITE)
        fg = WHITE if primary else BLUE
        widget = FlatButton(parent, text=text, command=command, font=(self.font_family, 10),
                           bg=bg, fg=fg, activebackground='#0e66bd' if primary else LIGHT,
                           activeforeground=WHITE if primary else BLUE, relief='flat', bd=0,
                           highlightthickness=0 if quiet or primary else 1, highlightbackground='#acc9e9',
                           padx=13, pady=8, cursor='hand2', compound='left')
        if icon:
            widget.configure(image=self.icon(icon, 'white' if primary else 'blue', 18))
        if track:
            self.controls.append(widget)
        return widget

    def entry(self, parent, variable, width=20):
        widget = ttk.Entry(parent, textvariable=variable, width=width, font=(self.font_family, 10))
        self.controls.append(widget)
        return widget

    def combo(self, parent, variable, values, width=18, editable=False):
        state = 'normal' if editable else 'readonly'
        widget = ttk.Combobox(parent, textvariable=variable, values=values, width=width, state=state, font=(self.font_family, 10))
        self.combos.append((widget, state))
        return widget

    def check(self, parent, text, variable, command=None):
        return tk.Checkbutton(parent, text=text, variable=variable, command=command, bg=parent.cget('bg'),
                              fg=TEXT, activebackground=parent.cget('bg'), selectcolor=WHITE,
                              font=(self.font_family, 10), bd=0, highlightthickness=0)

    def card(self, parent, **kwargs):
        return tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1, bd=0, **kwargs)

    def tree(self, parent, columns, height=5):
        frame = tk.Frame(parent, bg=WHITE)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=height)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor='w', minwidth=60)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        tree.configure(yscrollcommand=scroll.set)
        return frame, tree

    def build_shell(self):
        header = tk.Frame(self.window, bg=HEADER, height=60)
        header.pack(fill='x')
        header.pack_propagate(False)
        self.label(header, 'CraftFetch', size=20, bold=True, color=WHITE).pack(side='left', padx=24)
        self.label(header, '下载', size=12, color=WHITE).pack(side='left', padx=(24, 0))
        for text, callback, icon in [('目录设置', self.open_settings, 'settings'), ('Java 管理', self.open_java, 'coffee'), ('下载任务', self.open_tasks, 'download')]:
            b = FlatButton(header, text='  ' + text, command=callback, image=self.icon(icon, 'white', 19), compound='left',
                          bg=HEADER, fg=WHITE, activebackground='#0d61b4', activeforeground=WHITE,
                          bd=0, highlightthickness=0, relief='flat', padx=15, pady=9,
                          font=(self.font_family, 10), cursor='hand2')
            b.pack(side='right', padx=(0, 6))
        main = tk.Frame(self.window, bg=BG)
        main.pack(fill='both', expand=True)
        self.sidebar = tk.Frame(main, bg=WHITE, width=168)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)
        self.nav_buttons = {}
        for index, (title, kind, icon) in enumerate(CATEGORIES):
            if index == 1:
                self.label(self.sidebar, '社区资源', size=9, color=MUTED).pack(fill='x', padx=20, pady=(24, 8))
            frame = tk.Frame(self.sidebar, bg=WHITE, height=48)
            frame.pack(fill='x', pady=(16 if index == 0 else 0, 1))
            frame.pack_propagate(False)
            marker = tk.Frame(frame, bg=BLUE if index == 0 else WHITE, width=3)
            marker.pack(side='left', fill='y', pady=8)
            b = FlatButton(frame, text='  ' + title, image=self.icon(icon, 'blue' if index == 0 else 'muted', 23),
                          compound='left', anchor='w', command=lambda k=kind: self.navigate(k),
                          bg=LIGHT if index == 0 else WHITE, fg=BLUE if index == 0 else TEXT,
                          activebackground=LIGHT, activeforeground=BLUE, font=(self.font_family, 12),
                          bd=0, highlightthickness=0, relief='flat', padx=15, cursor='hand2')
            b.pack(fill='both', expand=True)
            self.nav_buttons[kind] = b, marker, icon
        bottom = tk.Frame(self.sidebar, bg=WHITE)
        bottom.pack(side='bottom', fill='x', padx=20, pady=17)
        self.label(bottom, '独立下载 · 自由启动', size=9, color=MUTED).pack(anchor='w')
        self.label(bottom, f'v{__version__}  /  Windows x64', size=9, color=MUTED).pack(anchor='w', pady=(5, 0))
        right = tk.Frame(main, bg=BG)
        right.pack(side='left', fill='both', expand=True)
        self.workspace = tk.Frame(right, bg=BG)
        self.workspace.pack(fill='both', expand=True, padx=(22, 15), pady=(20, 12))
        self.workspace.rowconfigure(0, weight=1)
        self.workspace.columnconfigure(0, weight=1)
        self.pages = {}
        footer = tk.Frame(right, bg='#f6faff', height=90)
        footer.pack(fill='x')
        line = tk.Frame(footer, bg=BORDER, height=1)
        line.pack(fill='x')
        progress_slot = tk.Frame(footer, height=4, bg='#dce9f7')
        progress_slot.pack(fill='x', padx=20, pady=(9, 5))
        progress_slot.pack_propagate(False)
        self.progress = ttk.Progressbar(progress_slot, mode='determinate', maximum=100, style='Thin.Horizontal.TProgressbar')
        self.progress.pack(fill='both', expand=True)
        row = tk.Frame(footer, bg='#f6faff')
        row.pack(fill='x', padx=20)
        self.label(row, variable=self.status, size=10, wraplength=700).pack(side='left', fill='x', expand=True)
        self.cancel_button = self.button(row, '取消', self.stop, quiet=True, track=False)
        self.cancel_button.pack(side='right')
        self.cancel_button.configure(state='disabled')
        path_row = tk.Frame(footer, bg='#f6faff')
        path_row.pack(fill='x', padx=20, pady=(0, 8))
        self.path_label = self.label(path_row, variable=self.directory_caption, size=9, color=MUTED, width=80)
        self.path_label.pack(side='left', fill='x', expand=True)
        self.button(path_row, '打开目录', self.open_directory, icon='folder', quiet=True, track=False).pack(side='right')

    def page(self, name):
        page = tk.Frame(self.workspace, bg=BG)
        page.grid(row=0, column=0, sticky='nsew')
        self.pages[name] = page
        return page

    def show_page(self, name):
        self.current_page = name
        self.pages[name].tkraise()

    def build_vanilla(self):
        page = self.page('vanilla')
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)
        filters = self.card(page)
        filters.grid(row=0, column=0, sticky='ew', pady=(0, 14))
        self.label(filters, '下载原版游戏', size=12, bold=True).pack(anchor='w', padx=20, pady=(16, 12))
        row = tk.Frame(filters, bg=WHITE)
        row.pack(fill='x', padx=20, pady=(0, 16))
        self.label(row, '版本').pack(side='left', padx=(0, 12))
        self.entry(row, self.search, 24).pack(side='left')
        self.check(row, '包含快照 / 预览版', self.snapshots, self.populate).pack(side='left', padx=12)
        self.button(row, '刷新', self.load_versions, quiet=True, icon='refresh').pack(side='right')
        content = self.card(page)
        content.grid(row=1, column=0, sticky='nsew')
        content.rowconfigure(1, weight=1)
        content.columnconfigure(0, weight=1)
        title = tk.Frame(content, bg=WHITE)
        title.grid(row=0, column=0, sticky='ew', padx=18, pady=(12, 6))
        self.label(title, 'Minecraft Java Edition', size=12, bold=True).pack(side='left')
        self.button(title, '官方网站', lambda: self.open_url('https://www.minecraft.net/zh-hans'), quiet=True, icon='external-link', track=False).pack(side='right')
        frame, self.versions = self.tree(content, [('version', '游戏版本', 260), ('kind', '类型', 130), ('date', '发布时间', 150)])
        frame.grid(row=1, column=0, sticky='nsew', padx=18)
        self.versions.bind('<<TreeviewSelect>>', lambda _: self.suggest_name())
        install = tk.Frame(content, bg=WHITE)
        install.grid(row=2, column=0, sticky='ew', padx=18, pady=14)
        install.columnconfigure(1, weight=1)
        self.label(install, '核心 / 加载器').grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.combo(install, self.loader, ('vanilla', *LOADERS), 17).grid(row=0, column=1, sticky='ew')
        self.label(install, '核心版本').grid(row=0, column=2, padx=(18, 10))
        self.entry(install, self.loader_version, 24).grid(row=0, column=3, sticky='ew')
        self.label(install, '实例名称').grid(row=1, column=0, sticky='w', pady=(12, 0))
        self.entry(install, self.instance_name, 28).grid(row=1, column=1, sticky='ew', pady=(12, 0))
        self.button(install, '下载并创建实例', self.install, primary=True, icon='download').grid(row=1, column=3, sticky='e', pady=(12, 0))
        self.label(content, 'vanilla 为原版；核心版本留空自动选择。按游戏要求自动部署 Java，下载后在 PCL 中选择此实例。', size=9, color=MUTED, wraplength=760).grid(row=3, column=0, sticky='w', padx=18, pady=(0, 15))

    def build_catalogue(self):
        page = self.page('catalogue')
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        filters = self.card(page)
        filters.grid(row=0, column=0, sticky='ew')
        self.label(filters, variable=self.search_caption, size=12, bold=True).grid(row=0, column=0, columnspan=4, sticky='w', padx=20, pady=(16, 12))
        filters.columnconfigure(1, weight=1)
        self.label(filters, '名称').grid(row=1, column=0, padx=(24, 14), sticky='w')
        query_entry = self.entry(filters, self.query, 32)
        query_entry.grid(row=1, column=1, sticky='ew')
        query_entry.bind('<Return>', lambda _: self.search_resources())
        self.label(filters, '来源').grid(row=1, column=2, padx=(24, 12))
        self.combo(filters, self.catalogue_source, ('Modrinth',), 16).grid(row=1, column=3, padx=(0, 24))
        self.label(filters, '游戏版本').grid(row=2, column=0, padx=(24, 14), pady=11, sticky='w')
        self.game_box = self.combo(filters, self.game_filter, ('全部',), 30, editable=True)
        self.game_box.grid(row=2, column=1, sticky='ew', pady=11)
        self.label(filters, '加载器').grid(row=2, column=2, padx=(24, 12))
        self.search_loader_box = self.combo(filters, self.loader_filter, ('全部', *LOADERS), 16)
        self.search_loader_box.grid(row=2, column=3, padx=(0, 24))
        actions = tk.Frame(filters, bg=WHITE)
        actions.grid(row=3, column=0, columnspan=4, sticky='ew', padx=24, pady=(1, 18))
        self.button(actions, '搜索', self.search_resources, primary=True, icon='search').pack(side='left', ipadx=20)
        self.button(actions, '重置条件', self.reset_search).pack(side='left', padx=14, ipadx=10)
        self.import_button = self.button(actions, '导入本地 .mrpack', self.import_pack, quiet=True, icon='folder')
        heading = tk.Frame(page, bg=BG)
        heading.grid(row=1, column=0, sticky='ew', pady=(13, 8))
        self.label(heading, variable=self.results_caption, size=10, color=MUTED).pack(side='left')
        self.previous_button = self.button(heading, '上一页', lambda: self.search_resources(-30), quiet=True)
        self.previous_button.pack(side='right')
        self.next_button = self.button(heading, '下一页', lambda: self.search_resources(30), quiet=True)
        self.next_button.pack(side='right')
        self.results_scroll = ScrollFrame(page)
        self.results_scroll.grid(row=2, column=0, sticky='nsew')
        self.results_body = self.results_scroll.content
        self.empty_results('选择一个资源分类', '搜索项目后，这里会显示作者图标、介绍和发布信息。')

    def build_project_detail(self):
        page = self.page('detail')
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        top = tk.Frame(page, bg=BG)
        top.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        self.button(top, '返回资源列表', self.back_to_results, quiet=True, icon='arrow-left', track=False).pack(side='left')
        self.detail_category = self.label(top, '', size=10, color=MUTED)
        self.detail_category.pack(side='left', padx=14)
        self.detail_scroll = ScrollFrame(page)
        self.detail_scroll.grid(row=1, column=0, sticky='nsew')
        self.detail_body = self.detail_scroll.content

    def clear_images(self):
        self.previews.cancel_pending()
        self.image_targets.clear()
        self.preview_photos.clear()

    def request_image(self, widget, url, size, cover=False):
        self.image_sequence += 1
        key = self.image_sequence
        self.image_targets[key] = widget
        self.previews.request(key, url, size, cover)
        return key

    def empty_results(self, title, subtitle):
        for widget in self.results_body.winfo_children():
            widget.destroy()
        card = self.card(self.results_body)
        card.pack(fill='x', padx=(0, 5), pady=(0, 10))
        self.label(card, title, size=13, bold=True).pack(anchor='w', padx=24, pady=(30, 9))
        self.label(card, subtitle, size=10, color=MUTED, wraplength=730).pack(anchor='w', padx=24, pady=(0, 30))

    def navigate(self, kind):
        if kind not in {k for _, k, _ in CATEGORIES}:
            return
        if kind == self.category and self.current_page != 'detail':
            return
        self.category = kind
        self.generation += 1
        self.project = None
        self.clear_images()
        for key, (button, marker, icon) in self.nav_buttons.items():
            selected = key == kind
            button.configure(bg=LIGHT if selected else WHITE, fg=BLUE if selected else TEXT,
                             image=self.icon(icon, 'blue' if selected else 'muted', 23))
            marker.configure(bg=BLUE if selected else WHITE)
        if kind == 'vanilla':
            self.show_page('vanilla')
            return
        title = next(t for t, k, _ in CATEGORIES if k == kind)
        self.search_caption.set('搜索 ' + title)
        self.query.set('')
        self.loader_filter.set('全部')
        self.game_filter.set('全部')
        self.show_page('catalogue')
        if kind == 'modpack':
            self.import_button.pack(side='right')
        else:
            self.import_button.pack_forget()
        self.search_loader_box.configure(state='readonly' if kind in ('mod', 'modpack') else 'disabled')
        self.search_resources()

    def reset_search(self):
        self.query.set('')
        self.game_filter.set('全部')
        self.loader_filter.set('全部')
        self.search_resources()

    def normal_filter(self, value):
        return '' if value.strip() in ('', '全部') else value.strip()

    def search_resources(self, step=0):
        if self.category == 'vanilla':
            return
        if self.busy:
            if self.operation == 'browse':
                self.cancel.set()
                self.pending_action = lambda: self.search_resources(step)
            else:
                self.status.set('当前正在下载，完成后可继续搜索。')
            return
        offset = max(0, self.page_offset + step) if step else 0
        self.page_offset = offset
        kind, query = self.category, self.query.get().strip()
        game, loader, source = self.normal_filter(self.game_filter.get()), self.normal_filter(self.loader_filter.get()), self.selected_source()
        self.generation += 1
        generation = self.generation
        self.clear_images()
        self.empty_results('正在搜索…', '正在获取项目列表与作者发布的信息。')
        self.results_caption.set('搜索中')
        def task():
            data = Catalog(self.client(source)).search(query, kind, game, loader, offset)
            self.messages.put(('projects', (generation, kind, data)))
        self.run(task, 'browse')

    def render_projects(self, result):
        self.clear_images()
        for widget in self.results_body.winfo_children():
            widget.destroy()
        self.projects = result['hits']
        self.total_hits = result.get('total_hits', len(self.projects))
        self.results_caption.set(f"找到 {compact_number(self.total_hits)} 个项目  ·  {self.page_offset + 1 if self.projects else 0}–{self.page_offset + len(self.projects)}")
        self.previous_button.configure(state='normal' if self.page_offset else 'disabled')
        self.next_button.configure(state='normal' if self.page_offset + len(self.projects) < self.total_hits else 'disabled')
        if not self.projects:
            self.empty_results('没有找到匹配的项目', '试试其他关键词，或将游戏版本、加载器设为“全部”。')
            return
        for project in self.projects:
            row = self.card(self.results_body, takefocus=True)
            row.pack(fill='x', padx=(0, 5), pady=(0, 8))
            row.columnconfigure(1, weight=1)
            image = self.label(row, '', image=self.icon('photo', size=48))
            image.grid(row=0, column=0, rowspan=3, padx=(18, 16), pady=18)
            self.request_image(image, project.get('icon_url'), (62, 62))
            title = self.label(row, shortened(project['title'], 62), size=13, bold=True)
            title.grid(row=0, column=1, sticky='w', pady=(14, 2))
            author = self.label(row, '作者 ' + project.get('author', '未知'), size=9, color=MUTED)
            author.grid(row=0, column=2, sticky='e', padx=(8, 18), pady=(14, 2))
            description = self.label(row, shortened(project.get('description', ''), 115), size=10, color='#7c8898', wraplength=690)
            description.grid(row=1, column=1, columnspan=2, sticky='ew', padx=(0, 18))
            row.bind('<Configure>', lambda e, label=description: label.configure(wraplength=max(240, e.width - 125)))
            loaders = [k for k in LOADERS if k in project.get('categories', [])]
            meta = ' / '.join(k.title() if k != 'neoforge' else 'NeoForge' for k in loaders) or next(t for t, k, _ in CATEGORIES if k == self.category)
            date = project.get('date_modified', '')[:10]
            details = f"{meta}   ·   {compact_number(project.get('downloads', 0))} 次下载   ·   更新 {date}   ·   Modrinth"
            self.label(row, details, size=9, color=MUTED).grid(row=2, column=1, columnspan=2, sticky='w', pady=(4, 14))
            def activate(_, p=project):
                self.open_project(p)
            def bind_widgets(widget):
                widget.configure(cursor='hand2')
                widget.bind('<Button-1>', activate)
                for child in widget.winfo_children():
                    bind_widgets(child)
            bind_widgets(row)
            row.bind('<Return>', activate)
            row.bind('<space>', activate)
            row.bind('<FocusIn>', lambda _, r=row: r.configure(highlightbackground=BLUE))
            row.bind('<FocusOut>', lambda _, r=row: r.configure(highlightbackground=BORDER))
        self.results_scroll.top()

    def open_project(self, hit):
        if self.busy:
            return
        kind = self.category
        self.generation += 1
        generation = self.generation
        self.project_hit = hit
        self.project_kind = kind
        self.show_page('detail')
        self.detail_category.configure(text=next(t for t, k, _ in CATEGORIES if k == kind) + ' / 项目详情')
        self.clear_images()
        for child in self.detail_body.winfo_children():
            child.destroy()
        self.label(self.detail_body, '正在读取项目详情与作者发布的版本…', size=12).pack(pady=30)
        source = self.selected_source()
        def task():
            catalogue = Catalog(self.client(source))
            project = catalogue.project(hit['project_id'])
            versions = catalogue.versions(hit['project_id'], kind=kind)
            self.messages.put(('project_detail', (generation, kind, hit, project, versions)))
        self.run(task, 'browse')

    def render_detail(self, hit, project, versions):
        self.project, self.project_hit = project, hit
        self.all_resource_versions = versions
        self.clear_images()
        for child in self.detail_body.winfo_children():
            child.destroy()
        body = self.detail_body
        hero = self.card(body)
        hero.pack(fill='x', padx=(0, 5), pady=(0, 12))
        hero.columnconfigure(1, weight=1)
        icon = self.label(hero, '', image=self.icon('photo', size=64))
        icon.grid(row=0, column=0, rowspan=3, padx=20, pady=20)
        self.request_image(icon, project.get('icon_url') or hit.get('icon_url'), (76, 76))
        self.label(hero, project['title'], size=20, bold=True, wraplength=580).grid(row=0, column=1, sticky='w', pady=(17, 3))
        self.label(hero, f"作者 {hit.get('author', '未知')}   ·   {compact_number(project.get('downloads', hit.get('downloads', 0)))} 次下载", size=10, color=MUTED).grid(row=1, column=1, sticky='w')
        self.label(hero, project.get('description', ''), size=10, color=MUTED, wraplength=650).grid(row=2, column=1, sticky='ew', padx=(0, 18), pady=(7, 17))
        links = tk.Frame(hero, bg=WHITE)
        links.grid(row=3, column=0, columnspan=2, sticky='ew', padx=20, pady=(0, 14))
        self.button(links, '来源网页 · Modrinth', lambda p=project: self.open_url(project_url(p, self.project_kind)), icon='external-link', quiet=True, track=False).pack(side='left')
        if project.get('source_url'):
            self.button(links, '作者源码', lambda u=project['source_url']: self.open_url(u), icon='external-link', quiet=True, track=False).pack(side='left', padx=12)
        gallery = sorted(project.get('gallery', []), key=lambda x: not x.get('featured', False))[:3]
        if gallery:
            art = self.card(body)
            art.pack(fill='x', padx=(0, 5), pady=(0, 12))
            self.label(art, '作者预览图', size=11, bold=True).pack(anchor='w', padx=18, pady=(12, 9))
            pictures = tk.Frame(art, bg=WHITE)
            pictures.pack(fill='x', padx=18, pady=(0, 14))
            for index, item in enumerate(gallery):
                cell = tk.Frame(pictures, bg=WHITE)
                cell.pack(side='left', fill='x', expand=True, padx=(0, 10))
                label = self.label(cell, '图片加载中', size=9, color=MUTED, width=25, height=6, anchor='center')
                label.pack(anchor='w')
                self.request_image(label, item.get('url'), (230, 122), cover=False)
                self.label(cell, shortened(item.get('title') or '项目预览', 28), size=9, color=MUTED).pack(anchor='w', pady=(4, 0))
        card = self.card(body)
        card.pack(fill='x', padx=(0, 5), pady=(0, 12))
        self.label(card, '选择作者发布的版本', size=12, bold=True).pack(anchor='w', padx=18, pady=(15, 11))
        filters = tk.Frame(card, bg=WHITE)
        filters.pack(fill='x', padx=18, pady=(0, 12))
        games = list(dict.fromkeys(g for v in versions for g in v.get('game_versions', [])))
        if self.manifest:
            positions = {v['id']: i for i, v in enumerate(self.manifest['versions'])}
            games.sort(key=lambda g: positions.get(g, 999999))
        loaders = list(dict.fromkeys(l for v in versions for l in v.get('loaders', [])))
        loaders.sort(key=lambda l: LOADERS.index(l) if l in LOADERS else len(LOADERS))
        self.detail_game.set(self.game_filter.get() if self.game_filter.get() in games else '全部')
        self.detail_loader.set(self.loader_filter.get() if self.loader_filter.get() in loaders else '全部')
        self.label(filters, '游戏版本').pack(side='left', padx=(0, 9))
        self.detail_game_box = self.combo(filters, self.detail_game, ['全部', *games], 14)
        self.detail_game_box.pack(side='left')
        self.detail_game_box.bind('<<ComboboxSelected>>', lambda _: self.filter_versions())
        self.label(filters, '核心 / 加载器').pack(side='left', padx=(18, 8))
        self.detail_loader_box = self.combo(filters, self.detail_loader, ['全部', *loaders], 14)
        self.detail_loader_box.pack(side='left')
        self.detail_loader_box.bind('<<ComboboxSelected>>', lambda _: self.filter_versions())
        self.check(filters, '仅正式版', self.only_release, self.filter_versions).pack(side='right')
        frame, self.resource_table = self.tree(card, [('version', '作者版本', 220), ('game', 'Minecraft', 150),
                                                   ('loader', '加载器', 120), ('date', '发布时间', 100), ('type', '发布类型', 80)], 5)
        frame.pack(fill='x', padx=18)
        self.resource_table.bind('<<TreeviewSelect>>', lambda _: self.selected_release_changed())
        self.version_count = self.label(card, '', size=9, color=MUTED)
        self.version_count.pack(anchor='w', padx=18, pady=9)
        destination = self.card(body)
        destination.pack(fill='x', padx=(0, 5), pady=(0, 12))
        self.label(destination, '下载到文件夹', size=12, bold=True).pack(anchor='w', padx=18, pady=(15, 10))
        row = tk.Frame(destination, bg=WHITE)
        row.pack(fill='x', padx=18)
        if self.project_kind == 'modpack':
            self.label(row, '新实例名称').pack(side='left', padx=(0, 10))
            self.entry(row, self.pack_name, 32).pack(side='left', fill='x', expand=True)
            self.check(destination, '包含整合包可选文件', self.optional).pack(anchor='w', padx=18, pady=(8, 0))
        else:
            self.label(row, '目标实例').pack(side='left', padx=(0, 10))
            self.target_box = self.combo(row, self.target, self.local_instances, 27)
            self.target_box.pack(side='left', fill='x', expand=True)
            self.target_box.bind('<<ComboboxSelected>>', lambda _: self.target_changed())
            self.button(row, '刷新', self.scan_instances, quiet=True, icon='refresh', track=False).pack(side='left', padx=5)
            if self.project_kind == 'datapack':
                self.label(row, '存档').pack(side='left', padx=(8, 8))
                self.world_box = self.combo(row, self.world, self.local_worlds, 18)
                self.world_box.pack(side='left')
        self.label(destination, variable=self.target_info, size=9, color=MUTED, wraplength=760).pack(anchor='w', padx=18, pady=(9, 0))
        final = tk.Frame(destination, bg=WHITE)
        final.pack(fill='x', padx=18, pady=14)
        self.selection_label = self.label(final, variable=self.selection_info, size=10, wraplength=540)
        self.selection_label.pack(side='left', fill='x', expand=True)
        self.resource_install_button = self.button(final, '下载所选版本', self.install_resource, primary=True, icon='download')
        self.resource_install_button.pack(side='right', padx=(12, 0))
        self.filter_versions()
        self.detail_scroll.top()

    def filter_versions(self):
        if not hasattr(self, 'resource_table') or not self.resource_table.winfo_exists():
            return
        selected = self.selected_release()
        previous = selected.get('id') if selected else None
        self.resource_table.delete(*self.resource_table.get_children())
        self.version_entries.clear()
        game, loader = self.normal_filter(self.detail_game.get()), self.normal_filter(self.detail_loader.get())
        versions = [v for v in self.all_resource_versions if (not game or game in v.get('game_versions', []))
                    and (not loader or loader in v.get('loaders', []))
                    and (not self.only_release.get() or v.get('version_type') == 'release')]
        self.resource_versions = versions
        for version in versions:
            values = (version.get('name') or version['version_number'], shortened(', '.join(version.get('game_versions', [])), 27),
                      ' / '.join(version.get('loaders', [])), version.get('date_published', '')[:10],
                      {'release': '正式版', 'beta': '测试版', 'alpha': '预览版'}.get(version.get('version_type'), '其他'))
            key = self.resource_table.insert('', 'end', values=values)
            self.version_entries[key] = version
            if version['id'] == previous:
                self.resource_table.selection_set(key)
        children = self.resource_table.get_children()
        if children and not self.resource_table.selection():
            self.resource_table.selection_set(children[0])
        self.version_count.configure(text=f'显示 {len(versions)} / {len(self.all_resource_versions)} 个作者版本' if versions else '没有匹配版本，请调整游戏版本或加载器。')
        self.selected_release_changed()

    def selected_release(self):
        if not hasattr(self, 'resource_table') or not self.resource_table.winfo_exists():
            return None
        selection = self.resource_table.selection()
        return self.version_entries.get(selection[0]) if selection else None

    def selected_release_changed(self):
        version = self.selected_release()
        if not version:
            self.selection_info.set('没有匹配版本，请调整筛选条件')
        else:
            info = primary_file(version)
            self.selection_info.set(f"{version['version_number']}  ·  {' / '.join(version.get('loaders', []))}  ·  {mib(info['size'])}")
        if hasattr(self, 'resource_install_button') and self.resource_install_button.winfo_exists():
            self.resource_install_button.configure(state='normal' if version and not self.busy else 'disabled')

    def back_to_results(self):
        self.generation += 1
        if self.busy and self.operation == 'browse':
            self.cancel.set()
        self.show_page('catalogue')
        self.project = None
        self.clear_images()
        self.render_projects({'hits': self.projects, 'total_hits': self.total_hits})
        self.status.set('点击项目查看预览图、作者版本与来源网页。')

    def on_wheel(self, event):
        widget = self.window.winfo_containing(event.x_root, event.y_root)
        scroll = self.results_scroll if self.current_page == 'catalogue' else self.detail_scroll if self.current_page == 'detail' else None
        if scroll is None or widget is None or isinstance(widget, (ttk.Treeview, ttk.Combobox)):
            return
        ancestor = widget
        while ancestor is not None:
            if ancestor is scroll:
                delta = -1 if getattr(event, 'num', 0) == 4 else 1 if getattr(event, 'num', 0) == 5 else -int(event.delta / (1 if sys.platform == 'darwin' else 120))
                scroll.canvas.yview_scroll(delta, 'units')
                return 'break'
            ancestor = getattr(ancestor, 'master', None)

    def choose_directory(self):
        folder = filedialog.askdirectory(title='选择 .minecraft 游戏根目录')
        if folder:
            self.directory.set(folder)
            self.scan_instances()

    def root_path(self):
        if not self.directory.get().strip():
            self.choose_directory()
        return Path(self.directory.get()).expanduser().resolve() if self.directory.get().strip() else None

    def scan_instances(self):
        self.local_instances, self.local_worlds = [], []
        if self.directory.get().strip():
            try:
                self.local_instances = list_instances(Path(self.directory.get()).expanduser().resolve())
            except OSError as exc:
                self.target_info.set(str(exc))
        if self.target.get() not in self.local_instances:
            self.target.set(self.local_instances[0] if self.local_instances else '')
        if hasattr(self, 'target_box') and self.target_box.winfo_exists():
            self.target_box.configure(values=self.local_instances)
        self.target_changed()

    def target_changed(self):
        self.local_worlds = []
        self.world.set('')
        if not self.target.get() or not self.directory.get():
            self.target_info.set('先在“原版”中创建对应加载器的实例，再选择目标。PCL 中需启用版本隔离。')
            return
        try:
            root = Path(self.directory.get()).expanduser().resolve()
            profile = read_instance(root, self.target.get())
            self.target_info.set(f"目标：Minecraft {profile['minecraft']} / {profile['loader']}   ·   PCL 中启用版本文件夹隔离")
            saves = root / 'versions' / self.target.get() / 'saves'
            self.local_worlds = sorted(p.name for p in saves.iterdir() if p.is_dir() and (p / 'level.dat').is_file()) if saves.is_dir() else []
            self.world.set(self.local_worlds[0] if self.local_worlds else '')
            if profile.get('java'):
                self.java_path.set(profile['java'])
        except (OSError, ValueError, KeyError, DownloadError) as exc:
            self.target_info.set(str(exc))
        if hasattr(self, 'world_box') and self.world_box.winfo_exists():
            self.world_box.configure(values=self.local_worlds)

    def suggest_name(self):
        selected = self.versions.selection()
        if selected and selected[0] in self.entries:
            self.instance_name.set(f"CraftFetch-{self.entries[selected[0]]['id']}-{self.loader.get()}")

    def set_busy(self, busy):
        self.busy = busy
        self.controls = [w for w in self.controls if w.winfo_exists()]
        self.combos = [(w, s) for w, s in self.combos if w.winfo_exists()]
        for widget in self.controls:
            widget.configure(state='disabled' if busy else 'normal')
        for widget, state in self.combos:
            widget.configure(state='disabled' if busy else state)
        if self.category not in ('mod', 'modpack'):
            self.search_loader_box.configure(state='disabled')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        if not busy:
            self.previous_button.configure(state='normal' if self.page_offset else 'disabled')
            self.next_button.configure(state='normal' if self.page_offset + len(self.projects) < self.total_hits else 'disabled')
        self.selected_release_changed()

    def run(self, action, operation='install'):
        if self.busy:
            return
        self.cancel.clear()
        self.progress.stop()
        self.operation = operation
        self.progress.configure(mode='indeterminate' if operation == 'browse' else 'determinate')
        self.progress['value'] = 0
        if operation == 'browse':
            self.progress.start(15)
        self.set_busy(True)
        def worker():
            try:
                action()
            except Cancelled as exc:
                self.messages.put(('phase', str(exc)))
            except Exception as exc:
                self.messages.put(('error', str(exc)))
            finally:
                self.messages.put(('idle', None))
        threading.Thread(target=worker, daemon=True).start()

    def client(self, source):
        return Downloader(source=source, cancel=self.cancel, event=lambda k, v: self.messages.put((k, v)))

    def selected_source(self):
        return 'bmclapi' if self.source.get().startswith('BMCLAPI') else 'official'

    def load_versions(self):
        source = self.selected_source()
        self.run(lambda: self.messages.put(('manifest', self.client(source).manifest())), 'browse')

    def populate(self):
        selected = self.versions.selection()
        previous = self.versions.item(selected[0], 'values')[0] if selected else None
        self.versions.delete(*self.versions.get_children())
        self.entries.clear()
        if not self.manifest:
            return
        term = self.search.get().strip().lower()
        for item in self.manifest['versions']:
            if item['type'] not in (('release', 'snapshot') if self.snapshots.get() else ('release',)):
                continue
            if term not in item['id'].lower():
                continue
            key = self.versions.insert('', 'end', values=(item['id'], '正式版' if item['type'] == 'release' else '快照 / 预览版', item.get('releaseTime', '')[:10]))
            self.entries[key] = item
            if item['id'] == previous:
                self.versions.selection_set(key)
        children = self.versions.get_children()
        if children and not self.versions.selection():
            self.versions.selection_set(children[0])
        self.suggest_name()

    def install(self):
        selected = self.versions.selection()
        if not selected or selected[0] not in self.entries:
            messagebox.showinfo('选择版本', '请先选择 Minecraft 版本。')
            return
        root = self.root_path()
        if root is None:
            return
        game, instance = self.entries[selected[0]]['id'], self.instance_name.get().strip()
        loader, version, auto, source = self.loader.get(), self.loader_version.get().strip(), self.auto_java.get(), self.selected_source()
        self.run(lambda: self.messages.put(('installed', create_instance(self.client(source), root, game, instance, loader, version, auto))))

    def install_resource(self):
        selected = self.selected_release()
        if not selected:
            messagebox.showinfo('选择发布版本', '请选择一个作者发布的版本。')
            return
        root = self.root_path()
        if root is None:
            return
        kind, instance, pack_name, world = self.project_kind, self.target.get(), self.pack_name.get().strip(), self.world.get()
        auto, optional, source = self.auto_java.get(), self.optional.get(), self.selected_source()
        if kind != 'modpack' and not instance:
            messagebox.showinfo('选择目标实例', '请先在“原版”中创建实例，再选择下载目标。')
            return
        def task():
            client = self.client(source)
            if kind == 'modpack':
                result = download_pack(client, root, selected, pack_name, optional=optional, auto_java=auto)
                self.messages.put(('installed', result))
            else:
                result = Catalog(client).install(root, instance, selected, kind, world)
                self.messages.put(('content_installed', result))
        self.run(task)

    def import_pack(self):
        if self.busy:
            return
        root = self.root_path()
        if root is None:
            return
        path = filedialog.askopenfilename(title='选择 Modrinth 整合包', filetypes=[('Modrinth 整合包', '*.mrpack'), ('ZIP', '*.zip')])
        if not path:
            return
        from tkinter.simpledialog import askstring
        instance = askstring('整合包实例名称', '新实例名称（英文、数字、点或横线）', initialvalue=self.pack_name.get(), parent=self.window)
        if not instance:
            return
        self.pack_name.set(instance)
        source, auto, optional = self.selected_source(), self.auto_java.get(), self.optional.get()
        self.run(lambda: self.messages.put(('installed', install_pack(self.client(source), root, path, instance, optional=optional, auto_java=auto))))

    def open_url(self, url):
        try:
            webbrowser.open(https_url(url), new=2)
        except (DownloadError, OSError, ValueError) as exc:
            messagebox.showerror('无法打开来源网页', str(exc))

    def dialog(self, name, title, geometry):
        existing = self.dialogs.get(name)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return None
        dialog = tk.Toplevel(self.window)
        dialog.title(title)
        dialog.geometry(geometry)
        dialog.configure(bg=BG)
        dialog.transient(self.window)
        self.dialogs[name] = dialog
        return dialog

    def open_settings(self):
        dialog = self.dialog('settings', '游戏目录与下载设置', '690x320')
        if dialog is None:
            return
        card = self.card(dialog)
        card.pack(fill='both', expand=True, padx=20, pady=20)
        self.label(card, '游戏文件保存位置', size=14, bold=True).pack(anchor='w', padx=20, pady=(18, 12))
        row = tk.Frame(card, bg=WHITE)
        row.pack(fill='x', padx=20)
        self.entry(row, self.directory, 45).pack(side='left', fill='x', expand=True)
        self.button(row, '浏览', self.choose_directory, quiet=True, icon='folder').pack(side='right', padx=(8, 0))
        self.label(card, '选择 PCL 使用的 .minecraft 根目录；迁移时复制整个文件夹。', size=10, color=MUTED, wraplength=600).pack(anchor='w', padx=20, pady=12)
        row = tk.Frame(card, bg=WHITE)
        row.pack(fill='x', padx=20)
        self.label(row, '原版下载来源').pack(side='left', padx=(0, 12))
        self.combo(row, self.source, ('官方源', 'BMCLAPI 镜像'), 19).pack(side='left')
        self.check(card, '创建实例时自动部署匹配的 Java', self.auto_java).pack(anchor='w', padx=20, pady=14)

    def open_java(self):
        dialog = self.dialog('java', 'Java 自动部署', '700x330')
        if dialog is None:
            return
        card = self.card(dialog)
        card.pack(fill='both', expand=True, padx=20, pady=20)
        self.label(card, 'Java 管理', size=15, bold=True).pack(anchor='w', padx=20, pady=(18, 10))
        self.label(card, '创建游戏时自动选择版本，也可以单独下载。文件保存在游戏目录的 runtime 中。', size=10, color=MUTED, wraplength=600).pack(anchor='w', padx=20)
        row = tk.Frame(card, bg=WHITE)
        row.pack(fill='x', padx=20, pady=15)
        self.label(row, 'Java 主版本').pack(side='left', padx=(0, 10))
        self.combo(row, self.java_major, ('8', '11', '16', '17', '21', '25'), 10, editable=True).pack(side='left')
        self.button(row, '下载并部署', self.install_runtime, primary=True, icon='download').pack(side='left', padx=16)
        path = ttk.Entry(card, textvariable=self.java_path, state='readonly', font=(self.font_family, 10))
        path.pack(fill='x', padx=20)
        self.button(card, '复制 Java 路径', self.copy_java, quiet=True).pack(anchor='w', padx=10, pady=8)
        self.label(card, 'PCL 未自动发现时，在该版本的 Java 设置中选择此 javaw.exe。', size=9, color=MUTED).pack(anchor='w', padx=20)

    def install_runtime(self):
        root = self.root_path()
        if root is None:
            return
        try:
            major = int(self.java_major.get())
        except ValueError:
            messagebox.showerror('Java 版本', '请输入整数主版本，例如 17 或 21。')
            return
        source = self.selected_source()
        self.run(lambda: self.messages.put(('runtime_installed', install_java(self.client(source), root, major))))

    def copy_java(self):
        if self.java_path.get():
            self.window.clipboard_clear()
            self.window.clipboard_append(self.java_path.get())
            self.status.set('Java 路径已复制，可粘贴到 PCL 中使用。')

    def open_tasks(self):
        dialog = self.dialog('tasks', '下载任务与日志', '800x390')
        if dialog is None:
            return
        self.label(dialog, variable=self.detail, size=10, wraplength=750).pack(anchor='w', padx=20, pady=15)
        self.log_widget = tk.Text(dialog, bg=WHITE, fg=TEXT, font=(self.font_family, 10), wrap='word', relief='flat', padx=12, pady=12)
        self.log_widget.pack(fill='both', expand=True, padx=20, pady=(0, 20))
        self.log_widget.insert('end', '\n'.join(self.log_lines))
        self.log_widget.configure(state='disabled')

    def stop(self):
        self.pending_action = None
        self.cancel.set()
        self.status.set('正在取消，请等待当前连接结束…')
        self.cancel_button.configure(state='disabled')

    def open_directory(self):
        if not self.directory.get().strip():
            self.open_settings()
            return
        root = Path(self.directory.get()).expanduser().resolve()
        if not root.is_dir():
            messagebox.showinfo('目录尚未创建', '先下载游戏，或选择已有的游戏目录。')
            return
        if sys.platform == 'win32':
            os.startfile(root)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(root)])

    def close(self):
        if self.busy:
            if self.operation != 'browse' and not messagebox.askyesno('退出下载器', '取消当前任务并退出？已校验的缓存会保留。'):
                return
            self.closing = True
            self.stop()
        else:
            self.shutdown()

    def shutdown(self):
        self.previews.close()
        self.window.destroy()

    def poll(self):
        for _ in range(100):
            try:
                kind, data = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'image':
                token, picture = data
                widget = self.image_targets.pop(token, None)
                if widget is not None and widget.winfo_exists():
                    photo = ImageTk.PhotoImage(picture, master=self.window)
                    widget.configure(image=photo, text='', width=picture.width, height=picture.height)
                    self.preview_photos[token] = photo
            elif kind == 'image_missing':
                widget = self.image_targets.pop(data, None)
                if widget is not None and widget.winfo_exists() and not widget.cget('image'):
                    widget.configure(text='暂无可用预览图')
            elif kind == 'manifest':
                self.manifest = data
                self.populate()
                games = ['全部', *[v['id'] for v in data['versions'] if v['type'] == 'release']]
                self.game_box.configure(values=games)
                self.status.set(f"版本列表已更新 · 最新正式版 {data.get('latest', {}).get('release', '未知')}")
            elif kind == 'phase':
                self.status.set(data)
            elif kind == 'plan':
                self.detail.set(f'Windows x64 · {len(data.files):,} 个原版文件 · {mib(data.total_bytes)} · Java {data.java or 8}')
            elif kind == 'progress':
                self.progress['value'] = data['done'] / max(1, data['total']) * 100
                self.status.set(f"当前阶段 {data['done']:,} / {data['total']:,} 个文件 · {mib(data['bytes'])}")
                self.detail.set(f"复用 {data['cached']:,} 个已有文件 · {data['file']}")
            elif kind == 'projects':
                generation, resource_kind, result = data
                if generation == self.generation and resource_kind == self.category:
                    self.render_projects(result)
                    self.status.set('点击项目查看预览图、作者版本与来源网页。')
            elif kind == 'project_detail':
                generation, resource_kind, hit, project, versions = data
                if generation == self.generation and resource_kind == self.category and self.current_page == 'detail':
                    self.render_detail(hit, project, versions)
                    self.status.set('选择游戏版本与加载器，查看匹配的作者发布版本。')
            elif kind in ('log', 'error'):
                self.log_lines.append(str(data))
                self.log_lines = self.log_lines[-150:]
                if hasattr(self, 'log_widget') and self.log_widget.winfo_exists():
                    self.log_widget.configure(state='normal')
                    self.log_widget.insert('end', str(data) + '\n')
                    self.log_widget.see('end')
                    self.log_widget.configure(state='disabled')
                if kind == 'error':
                    self.status.set('任务未完成，可在“下载任务”中查看原因。')
                    if self.operation == 'browse' and self.current_page == 'catalogue':
                        self.empty_results('暂时无法获取资源', shortened(data, 180))
                    if self.operation != 'browse' and not self.closing and self.pending_action is None:
                        messagebox.showerror('任务未完成', data)
            elif kind == 'installed':
                self.progress['value'] = 100
                self.java_path.set(data['java'])
                self.scan_instances()
                self.target.set(data['instance'])
                self.target_changed()
                self.status.set(f"实例 {data['instance']} 已安装完成。")
                if not self.closing:
                    messagebox.showinfo('实例安装完成', f"PCL 游戏目录：\n{data['directory']}\n\n选择版本：{data['instance']}\n在版本设置中启用游戏目录隔离（版本文件夹）。\nJava {data['java_major']}：{data['java'] or '未自动部署'}\n\n实例文件夹中附有 CraftFetch-导入说明.txt。")
            elif kind == 'content_installed':
                self.progress['value'] = 100
                self.status.set(f"资源及依赖已安装，共 {data['resources']} 个文件。")
                if not self.closing:
                    messagebox.showinfo('资源安装完成', f"已安装到：\n{data['directory']}\n\nPCL 请启用该版本的游戏目录隔离。\n资源包和光影包需要在游戏设置中启用；光影需 Iris / OptiFine 等支持。\n数据包可重新进入存档或执行 /reload 后使用。")
            elif kind == 'runtime_installed':
                self.progress['value'] = 100
                self.java_path.set(str(data))
                self.status.set('Java 已部署，可复制路径给 PCL 使用。')
            elif kind == 'idle':
                self.progress.stop()
                self.progress.configure(mode='determinate')
                self.set_busy(False)
                if self.closing:
                    self.shutdown()
                    return
                pending, self.pending_action = self.pending_action, None
                if pending is not None:
                    self.window.after_idle(pending)
        self.window.after(80, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
