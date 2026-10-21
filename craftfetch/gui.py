"""Native desktop UI. Workers never read or modify Tk variables."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .catalog import Catalog, KINDS
from .core import Cancelled, Downloader, DownloadError
from .instances import create_instance, list_instances, read_instance
from .packs import download_pack, install_pack
from .runtime import install_java


def mib(value):
    return f"{value / 1024 / 1024:,.1f} MiB"


class App:
    def __init__(self, window):
        self.window = window
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.busy = self.closing = False
        self.manifest = None
        self.entries, self.projects, self.resource_versions = {}, {}, []
        self.controls = []
        self.combos = []
        self.search = tk.StringVar()
        self.snapshots = tk.BooleanVar(value=False)
        self.source = tk.StringVar(value="官方源")
        default = str(Path(os.environ["APPDATA"]) / ".minecraft") if "APPDATA" in os.environ else ""
        self.directory = tk.StringVar(value=default)
        self.auto_java = tk.BooleanVar(value=True)
        self.loader = tk.StringVar(value="vanilla")
        self.loader_version = tk.StringVar()
        self.instance_name = tk.StringVar()
        self.kind = tk.StringVar(value="Mod")
        self.query = tk.StringVar()
        self.target = tk.StringVar()
        self.game_filter = tk.StringVar(value="1.20.1")
        self.loader_filter = tk.StringVar(value="fabric")
        self.world = tk.StringVar()
        self.pack_name = tk.StringVar(value="My-Modpack")
        self.optional = tk.BooleanVar(value=False)
        self.resource_version = tk.StringVar()
        self.java_major = tk.StringVar(value="21")
        self.java_path = tk.StringVar()
        self.status = tk.StringVar(value="选择游戏目录，再创建实例或为已有实例下载资源。")
        self.detail = tk.StringVar(value="目标平台：Windows x64 · 所有游戏文件和 Java 均保存在所选目录")
        self.target_info = tk.StringVar(value="请选择目标实例；PCL 中须启用该版本的游戏目录隔离。")
        self.resource_info = tk.StringVar(value="资源来自 Modrinth；先搜索项目，再选择兼容的下载版本。")
        window.title(f"CraftFetch · Minecraft 下载器 {__version__}")
        window.geometry("1050x800")
        window.minsize(930, 740)
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f9")
        style.configure("TLabel", background="#f3f5f9", foreground="#243047", font=("", 11))
        style.configure("TCheckbutton", background="#f3f5f9", font=("", 10))
        style.configure("TButton", padding=(9, 6), font=("", 11))
        style.configure("Primary.TButton", foreground="white", background="#2563eb")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        style.configure("Treeview", rowheight=28, font=("", 11))
        style.configure("Treeview.Heading", font=("", 11, "bold"), padding=5)
        style.configure("TNotebook.Tab", padding=(18, 9), font=("", 11))
        header = tk.Frame(window, bg="#17243d", padx=24, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="CraftFetch", fg="white", bg="#17243d", font=("", 23, "bold")).pack(anchor="w")
        tk.Label(header, text="游戏 · Mod · 整合包 · 数据包 · 资源包 · 光影 · Java", fg="#bdcce5", bg="#17243d", font=("", 11)).pack(anchor="w", pady=(4, 0))
        body = ttk.Frame(window, padding=(20, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        folder = ttk.Frame(body)
        folder.grid(row=0, column=0, sticky="ew")
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text="游戏根目录").grid(row=0, column=0, padx=(0, 10))
        self.path_entry = ttk.Entry(folder, textvariable=self.directory)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.controls.append(self.path_entry)
        self.button(folder, "浏览…", self.choose_directory).grid(row=0, column=2, padx=(10, 0))
        options = ttk.Frame(body)
        options.grid(row=1, column=0, sticky="ew", pady=8)
        self.combo(options, self.source, ("官方源", "BMCLAPI 镜像"), 16).pack(side="left")
        ttk.Checkbutton(options, text="安装游戏时自动部署对应 Java", variable=self.auto_java).pack(side="left", padx=14)
        ttk.Label(body, text="选择 PCL 使用的 .minecraft；新实例的资源放在 versions/实例名 内，便于整目录迁移。", font=("", 10)).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.tabs = ttk.Notebook(body)
        self.tabs.grid(row=3, column=0, sticky="nsew")
        self.build_game_tab()
        self.build_resource_tab()
        self.build_java_tab()
        ttk.Label(body, textvariable=self.status, wraplength=960).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.progress = ttk.Progressbar(body, mode="determinate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=7)
        ttk.Label(body, textvariable=self.detail, wraplength=960, font=("", 10)).grid(row=6, column=0, sticky="w")
        self.log = tk.Text(body, height=3, bg="#e9edf5", fg="#4c5970", relief="flat", wrap="word", padx=8, pady=6, state="disabled", font=("", 10))
        self.log.grid(row=7, column=0, sticky="ew", pady=8)
        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, sticky="ew")
        ttk.Button(actions, text="打开游戏目录", command=self.open_directory).pack(side="left")
        ttk.Button(actions, text="刷新本地实例", command=self.scan_instances).pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="取消当前任务", command=self.stop, state="disabled")
        self.cancel_button.pack(side="right")
        ttk.Label(body, text="Modrinth · Eclipse Adoptium · Fabric / Quilt / Forge / NeoForge · BMCLAPI（bangbang93.com）", font=("", 9)).grid(row=9, column=0, sticky="w", pady=(10, 0))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.poll)
        window.after(150, self.load_versions)
        self.path_entry.bind("<FocusOut>", lambda _: self.scan_instances())
        self.scan_instances()

    def button(self, parent, text, command, primary=False):
        result = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        self.controls.append(result)
        return result

    def combo(self, parent, variable, values, width=15, editable=False):
        result = ttk.Combobox(parent, textvariable=variable, values=values, width=width, state="normal" if editable else "readonly")
        self.combos.append((result, "normal" if editable else "readonly"))
        return result

    def entry(self, parent, variable, width=20):
        result = ttk.Entry(parent, textvariable=variable, width=width)
        self.controls.append(result)
        return result

    def table(self, parent, columns, height=6):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        view = ttk.Treeview(frame, columns=tuple(c[0] for c in columns), show="headings", selectmode="browse", height=height)
        for key, title, width in columns:
            view.heading(key, text=title)
            view.column(key, width=width, anchor="w")
        view.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        view.configure(yscrollcommand=scroll.set)
        return frame, view

    def tab(self, title):
        tab = ttk.Frame(self.tabs, padding=12)
        tab.columnconfigure(0, weight=1)
        self.tabs.add(tab, text=title)
        return tab

    def build_game_tab(self):
        tab = self.tab("游戏与加载器")
        tab.rowconfigure(1, weight=1)
        filters = ttk.Frame(tab)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="Minecraft 版本").pack(side="left")
        self.entry(filters, self.search, 20).pack(side="left", padx=8)
        ttk.Checkbutton(filters, text="包含快照", variable=self.snapshots, command=self.populate).pack(side="left")
        self.button(filters, "刷新列表", self.load_versions).pack(side="right")
        self.search.trace_add("write", lambda *_: self.populate())
        frame, self.versions = self.table(tab, (("version", "版本", 260), ("kind", "类型", 160), ("date", "发布时间", 160)))
        frame.grid(row=1, column=0, sticky="nsew")
        self.versions.bind("<<TreeviewSelect>>", lambda _: self.suggest_name())
        row = ttk.Frame(tab)
        row.grid(row=2, column=0, sticky="ew", pady=10)
        ttk.Label(row, text="加载器").pack(side="left")
        self.combo(row, self.loader, ("vanilla", "fabric", "quilt", "forge", "neoforge"), 12).pack(side="left", padx=8)
        self.loader.trace_add("write", lambda *_: self.suggest_name())
        ttk.Label(row, text="加载器版本（留空自动选择）").pack(side="left", padx=(10, 0))
        self.entry(row, self.loader_version, 22).pack(side="left", padx=8)
        row = ttk.Frame(tab)
        row.grid(row=3, column=0, sticky="ew")
        ttk.Label(row, text="实例名称").pack(side="left")
        self.entry(row, self.instance_name, 36).pack(side="left", padx=8)
        self.button(row, "创建并下载实例", self.install, True).pack(side="right")
        ttk.Label(tab, text="vanilla = 原版。Mod 需要匹配的加载器；光影通常还需 Iris / OptiFine。Forge 自动安装支持 1.13+，需 Windows。", wraplength=940, font=("", 10)).grid(row=4, column=0, sticky="w", pady=(9, 0))

    def build_resource_tab(self):
        tab = self.tab("资源与整合包")
        tab.rowconfigure(3, weight=1)
        target_row = ttk.Frame(tab)
        target_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(target_row, text="目标实例").pack(side="left")
        self.target_box = self.combo(target_row, self.target, (), 30)
        self.target_box.pack(side="left", padx=8)
        self.target_box.bind("<<ComboboxSelected>>", lambda _: self.target_changed())
        ttk.Label(target_row, text="存档（数据包）").pack(side="left", padx=(8, 0))
        self.world_box = self.combo(target_row, self.world, (), 23)
        self.world_box.pack(side="left", padx=8)
        ttk.Label(tab, textvariable=self.target_info, wraplength=940, font=("", 10)).grid(row=1, column=0, sticky="w", pady=7)
        search = ttk.Frame(tab)
        search.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.combo(search, self.kind, tuple(KINDS), 8).pack(side="left")
        self.kind.trace_add("write", lambda *_: self.clear_results())
        self.entry(search, self.query, 24).pack(side="left", padx=7)
        ttk.Label(search, text="MC").pack(side="left")
        self.entry(search, self.game_filter, 10).pack(side="left", padx=6)
        self.combo(search, self.loader_filter, ("", "fabric", "quilt", "forge", "neoforge"), 10).pack(side="left")
        self.button(search, "搜索", self.search_resources).pack(side="right")
        frame, self.resources = self.table(tab, (("title", "项目", 260), ("author", "作者", 130), ("downloads", "下载量", 100), ("description", "介绍", 390)), 4)
        frame.grid(row=3, column=0, sticky="nsew")
        self.resources.bind("<Double-1>", lambda _: self.load_resource_versions())
        self.resources.bind("<<TreeviewSelect>>", lambda _: self.clear_resource_versions())
        row = ttk.Frame(tab)
        row.grid(row=4, column=0, sticky="ew", pady=8)
        self.button(row, "读取所选项目版本", self.load_resource_versions).pack(side="left")
        self.resource_box = self.combo(row, self.resource_version, (), 61)
        self.resource_box.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Label(tab, textvariable=self.resource_info, wraplength=940, font=("", 10)).grid(row=5, column=0, sticky="w")
        row = ttk.Frame(tab)
        row.grid(row=6, column=0, sticky="ew", pady=8)
        ttk.Label(row, text="整合包新实例名").pack(side="left")
        self.entry(row, self.pack_name, 22).pack(side="left", padx=7)
        ttk.Checkbutton(row, text="包含整合包可选文件", variable=self.optional).pack(side="left")
        self.button(row, "安装所选资源", self.install_resource, True).pack(side="right")
        row = ttk.Frame(tab)
        row.grid(row=7, column=0, sticky="ew")
        self.button(row, "导入本地 .mrpack", self.import_pack).pack(side="left")
        ttk.Label(row, text="整合包会新建实例；Mod / 资源包 / 光影 / 数据包安装到目标实例，必需依赖自动补齐。", wraplength=700, font=("", 10)).pack(side="left", padx=10)

    def build_java_tab(self):
        tab = self.tab("Java 管理")
        ttk.Label(tab, text="部署独立 Java，无需管理员权限", font=("", 15, "bold")).grid(row=0, column=0, sticky="w", pady=(12, 14))
        ttk.Label(tab, text="创建游戏或整合包时，会按 Mojang 元数据自动选择 Java。也可以在这里单独下载指定主版本。\nJava 保存到游戏根目录的 runtime 文件夹，整体复制游戏目录后仍可使用。", wraplength=900).grid(row=1, column=0, sticky="w")
        row = ttk.Frame(tab)
        row.grid(row=2, column=0, sticky="ew", pady=20)
        ttk.Label(row, text="Java 主版本").pack(side="left")
        self.combo(row, self.java_major, ("8", "11", "16", "17", "21", "25"), 10, True).pack(side="left", padx=10)
        self.button(row, "下载并部署 Windows x64 Java", self.install_runtime, True).pack(side="left")
        ttk.Label(tab, text="Java 可执行文件路径").grid(row=3, column=0, sticky="w")
        self.java_entry = ttk.Entry(tab, textvariable=self.java_path, state="readonly")
        self.java_entry.grid(row=4, column=0, sticky="ew", pady=8)
        ttk.Button(tab, text="复制 Java 路径", command=self.copy_java).grid(row=5, column=0, sticky="w")
        ttk.Label(tab, text="若 PCL 未自动发现，请在该版本的 Java 设置里选择上面的 javaw.exe。\n下载来源：Eclipse Adoptium / Temurin；下载包使用 SHA-256 校验，复用前检查已部署文件。", wraplength=900).grid(row=6, column=0, sticky="w", pady=20)

    def choose_directory(self):
        folder = filedialog.askdirectory(title="选择 .minecraft 游戏根目录")
        if folder:
            self.directory.set(folder)
            self.scan_instances()

    def root_path(self):
        if not self.directory.get().strip():
            self.choose_directory()
        if not self.directory.get().strip():
            return None
        return Path(self.directory.get()).expanduser().resolve()

    def scan_instances(self):
        if not self.directory.get().strip():
            return
        try:
            names = list_instances(Path(self.directory.get()).expanduser().resolve())
            self.target_box.configure(values=names)
            if self.target.get() not in names:
                self.target.set(names[0] if names else "")
            self.target_changed()
        except (OSError, ValueError, KeyError, DownloadError) as exc:
            self.target_info.set(f"读取实例失败：{exc}")

    def target_changed(self):
        self.world.set("")
        self.world_box.configure(values=())
        self.clear_results()
        if not self.target.get() or not self.directory.get():
            return
        root = Path(self.directory.get()).expanduser().resolve()
        try:
            record = read_instance(root, self.target.get())
            self.game_filter.set(record["minecraft"])
            self.loader_filter.set("" if record["loader"] == "vanilla" else record["loader"])
            self.target_info.set(f"{record['minecraft']} · {record['loader']} · PCL 版本设置中启用游戏目录隔离（版本文件夹）")
            saves = root / "versions" / self.target.get() / "saves"
            worlds = sorted(p.name for p in saves.iterdir() if p.is_dir() and (p / "level.dat").is_file()) if saves.is_dir() else []
            self.world_box.configure(values=worlds)
            self.world.set(worlds[0] if worlds else "")
        except (OSError, ValueError, KeyError, DownloadError) as exc:
            self.target_info.set(str(exc))

    def clear_results(self):
        self.projects.clear()
        self.clear_resource_versions()
        if hasattr(self, "resources"):
            self.resources.delete(*self.resources.get_children())

    def clear_resource_versions(self):
        self.resource_versions = []
        self.resource_version.set("")
        if hasattr(self, "resource_box"):
            self.resource_box.configure(values=())

    def suggest_name(self):
        selected = self.versions.selection()
        if selected and selected[0] in self.entries:
            self.instance_name.set(f"CraftFetch-{self.entries[selected[0]]['id']}-{self.loader.get()}")

    def set_busy(self, busy):
        self.busy = busy
        for widget in self.controls:
            widget.configure(state="disabled" if busy else "normal")
        for widget, state in self.combos:
            widget.configure(state="disabled" if busy else state)
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def run(self, action):
        if self.busy:
            return
        self.cancel.clear()
        self.progress["value"] = 0
        self.set_busy(True)
        def worker():
            try:
                action()
            except Cancelled as exc:
                self.messages.put(("phase", str(exc)))
            except Exception as exc:
                self.messages.put(("error", str(exc)))
            finally:
                self.messages.put(("idle", None))
        threading.Thread(target=worker, daemon=True).start()

    def client(self, source):
        return Downloader(source=source, cancel=self.cancel, event=lambda k, v: self.messages.put((k, v)))

    def selected_source(self):
        return "bmclapi" if self.source.get().startswith("BMCLAPI") else "official"

    def load_versions(self):
        source = self.selected_source()
        self.run(lambda: self.messages.put(("manifest", self.client(source).manifest())))

    def populate(self):
        selected = self.versions.selection()
        previous = self.versions.item(selected[0], "values")[0] if selected else None
        self.versions.delete(*self.versions.get_children())
        self.entries.clear()
        if not self.manifest:
            return
        term = self.search.get().strip().lower()
        for item in self.manifest["versions"]:
            if item["type"] not in (("release", "snapshot") if self.snapshots.get() else ("release",)):
                continue
            if term not in item["id"].lower():
                continue
            key = self.versions.insert("", "end", values=(item["id"], "正式版" if item["type"] == "release" else "快照 / 预览版", item.get("releaseTime", "")[:10]))
            self.entries[key] = item
            if item["id"] == previous:
                self.versions.selection_set(key)
        children = self.versions.get_children()
        if children and not self.versions.selection():
            self.versions.selection_set(children[0])

    def install(self):
        selected = self.versions.selection()
        if not selected or selected[0] not in self.entries:
            messagebox.showinfo("选择版本", "请先从列表中选择一个 Minecraft 版本。")
            return
        root = self.root_path()
        if root is None:
            return
        game, instance = self.entries[selected[0]]["id"], self.instance_name.get().strip()
        loader, version, auto, source = self.loader.get(), self.loader_version.get().strip(), self.auto_java.get(), self.selected_source()
        def task():
            result = create_instance(self.client(source), root, game, instance, loader, version, auto)
            self.messages.put(("installed", result))
        self.run(task)

    def search_resources(self):
        query, kind, game, loader, source = self.query.get().strip(), KINDS[self.kind.get()], self.game_filter.get().strip(), self.loader_filter.get(), self.selected_source()
        self.clear_results()
        def task():
            result = Catalog(self.client(source)).search(query, kind, game, loader)
            self.messages.put(("projects", (kind, result)))
        self.run(task)

    def load_resource_versions(self):
        selected = self.resources.selection()
        if not selected or selected[0] not in self.projects:
            messagebox.showinfo("选择项目", "先搜索并选择一个项目。")
            return
        project = self.projects[selected[0]]
        game, loader, kind, source = self.game_filter.get().strip(), self.loader_filter.get(), KINDS[self.kind.get()], self.selected_source()
        def task():
            versions = Catalog(self.client(source)).versions(project["project_id"], game, loader, kind)
            self.messages.put(("resource_versions", (kind, project, versions)))
        self.run(task)

    def install_resource(self):
        index = self.resource_box.current()
        if index < 0 or index >= len(self.resource_versions):
            messagebox.showinfo("选择下载版本", "请读取项目版本，并选择要安装的资源版本。")
            return
        root = self.root_path()
        if root is None:
            return
        selected, kind = self.resource_versions[index], KINDS[self.kind.get()]
        instance, pack_name, world = self.target.get(), self.pack_name.get().strip(), self.world.get()
        auto, optional, source = self.auto_java.get(), self.optional.get(), self.selected_source()
        if kind != "modpack" and not instance:
            messagebox.showinfo("选择目标实例", "请先创建游戏实例，或选择已有目标实例。")
            return
        def task():
            client = self.client(source)
            if kind == "modpack":
                self.messages.put(("installed", download_pack(client, root, selected, pack_name, optional=optional, auto_java=auto)))
            else:
                result = Catalog(client).install(root, instance, selected, kind, world)
                self.messages.put(("content_installed", result))
        self.run(task)

    def import_pack(self):
        root = self.root_path()
        if root is None:
            return
        path = filedialog.askopenfilename(title="选择 Modrinth 整合包", filetypes=[("Modrinth 整合包", "*.mrpack"), ("ZIP", "*.zip")])
        if not path:
            return
        instance, auto, optional, source = self.pack_name.get().strip(), self.auto_java.get(), self.optional.get(), self.selected_source()
        self.run(lambda: self.messages.put(("installed", install_pack(self.client(source), root, path, instance, optional=optional, auto_java=auto))))

    def install_runtime(self):
        root = self.root_path()
        if root is None:
            return
        try:
            major = int(self.java_major.get())
        except ValueError:
            messagebox.showerror("Java 版本", "请输入整数主版本，例如 17 或 21。")
            return
        source = self.selected_source()
        self.run(lambda: self.messages.put(("runtime_installed", install_java(self.client(source), root, major))))

    def copy_java(self):
        if self.java_path.get():
            self.window.clipboard_clear()
            self.window.clipboard_append(self.java_path.get())
            self.status.set("Java 路径已复制，可粘贴到 PCL 的 Java 选择框。")

    def stop(self):
        self.cancel.set()
        self.status.set("正在取消，请等待当前连接结束…")
        self.cancel_button.configure(state="disabled")

    def open_directory(self):
        if not self.directory.get().strip():
            return
        root = Path(self.directory.get()).expanduser().resolve()
        if not root.is_dir():
            messagebox.showinfo("目录尚未创建", "先下载游戏，或选择已有的游戏目录。")
            return
        if sys.platform == "win32":
            os.startfile(root)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(root)])

    def close(self):
        if self.busy:
            if not messagebox.askyesno("退出下载器", "取消当前任务并退出？已校验的缓存会保留。"):
                return
            self.closing = True
            self.stop()
        else:
            self.window.destroy()

    def poll(self):
        for _ in range(100):
            try:
                kind, data = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "manifest":
                self.manifest = data
                self.populate()
                self.status.set(f"版本列表已更新 · 最新正式版 {data.get('latest', {}).get('release', '未知')}")
            elif kind == "phase":
                self.status.set(data)
            elif kind == "plan":
                self.detail.set(f"Windows x64 · {len(data.files):,} 个原版文件 · {mib(data.total_bytes)} · Java {data.java or 8}")
            elif kind == "progress":
                self.progress["value"] = data["done"] / max(1, data["total"]) * 100
                self.status.set(f"当前阶段已校验 {data['done']:,} / {data['total']:,} 个文件 · {mib(data['bytes'])}")
                self.detail.set(f"复用 {data['cached']:,} 个已有文件 · {data['file']}")
            elif kind == "projects":
                resource_kind, result = data
                if resource_kind != KINDS[self.kind.get()]:
                    continue
                for project in result["hits"]:
                    key = self.resources.insert("", "end", values=(project["title"], project["author"], f"{project['downloads']:,}", project["description"]))
                    self.projects[key] = project
                children = self.resources.get_children()
                if children:
                    self.resources.selection_set(children[0])
                self.status.set(f"找到 {result['total_hits']:,} 个项目，显示前 30 个；可增加关键词缩小范围。")
            elif kind == "resource_versions":
                resource_kind, project, versions = data
                if resource_kind != KINDS[self.kind.get()]:
                    continue
                self.resource_versions = versions
                names = [f"{v['version_number']} · {v['version_type']} · {v['date_published'][:10]} · {', '.join(v['loaders'])}" for v in versions]
                self.resource_box.configure(values=names)
                if names:
                    self.resource_box.current(0)
                else:
                    self.resource_version.set("")
                self.resource_info.set(f"{project['title']} · {len(versions)} 个兼容版本。{project['description']}")
                self.status.set("请选择下载版本。" if versions else "没有匹配版本；请检查 MC 版本和加载器筛选。")
            elif kind in ("log", "error"):
                self.log.configure(state="normal")
                self.log.insert("end", str(data) + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 150:
                    self.log.delete("1.0", "50.0")
                self.log.see("end")
                self.log.configure(state="disabled")
                if kind == "error":
                    self.status.set("任务未完成，请查看原因并重试。")
                    if not self.closing:
                        messagebox.showerror("任务未完成", data)
            elif kind == "installed":
                self.progress["value"] = 100
                self.java_path.set(data["java"])
                self.scan_instances()
                self.target.set(data["instance"])
                self.target_changed()
                self.status.set(f"实例 {data['instance']} 已安装完成。")
                if not self.closing:
                    messagebox.showinfo("实例安装完成", f"PCL 游戏目录：\n{data['directory']}\n\n选择版本：{data['instance']}\n在版本设置中启用游戏目录隔离（版本文件夹）。\nJava {data['java_major']}：{data['java'] or '未自动部署'}\n\n若 PCL 未发现 Java，在 Java 设置中手动选择该路径。\n实例文件夹中附有 CraftFetch-导入说明.txt。")
            elif kind == "content_installed":
                self.progress["value"] = 100
                self.status.set(f"资源及依赖已安装，共 {data['resources']} 个文件。")
                if not self.closing:
                    messagebox.showinfo("资源安装完成", f"已安装到：\n{data['directory']}\n\nPCL 请启用该版本的游戏目录隔离。\n资源包和光影包需要在游戏设置中启用；光影需 Iris / OptiFine 等支持。\n数据包可重新进入存档或执行 /reload 后使用。")
            elif kind == "runtime_installed":
                self.progress["value"] = 100
                self.java_path.set(str(data))
                self.status.set("Java 已部署，可复制路径给 PCL 使用。")
            elif kind == "idle":
                self.set_busy(False)
                if self.closing:
                    self.window.destroy()
                    return
        self.window.after(100, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
