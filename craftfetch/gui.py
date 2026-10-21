"""Small native desktop interface; all network and disk work runs off Tk."""
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
from .core import Cancelled, Downloader


def mib(value):
    return f"{value / 1024 / 1024:,.1f} MiB"


class App:
    def __init__(self, window):
        self.window = window
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.manifest = None
        self.entries = {}
        self.last_directory = None
        self.search = tk.StringVar()
        self.snapshots = tk.BooleanVar(value=False)
        self.source = tk.StringVar(value="官方源")
        default = str(Path(os.environ["APPDATA"]) / ".minecraft") if "APPDATA" in os.environ else ""
        self.directory = tk.StringVar(value=default)
        self.status = tk.StringVar(value="选择版本和游戏目录，下载后交给你熟悉的启动器。")
        self.detail = tk.StringVar(value="目标平台：Windows 64 位 · 原版 Minecraft Java 版")
        window.title(f"CraftFetch · Minecraft 下载器 {__version__}")
        window.geometry("900x760")
        window.minsize(760, 680)
        window.configure(bg="#f3f5f9")
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f9")
        style.configure("TLabel", background="#f3f5f9", foreground="#243047", font=("", 11))
        style.configure("TCheckbutton", background="#f3f5f9", font=("", 11))
        style.configure("TButton", padding=(12, 8), font=("", 11))
        style.configure("Primary.TButton", foreground="white", background="#2563eb")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        style.configure("Treeview", rowheight=30, font=("", 11))
        style.configure("Treeview.Heading", font=("", 11, "bold"), padding=7)
        style.configure("TEntry", padding=7)
        header = tk.Frame(window, bg="#17243d", padx=26, pady=19)
        header.pack(fill="x")
        tk.Label(header, text="CraftFetch", fg="white", bg="#17243d",
                 font=("", 24, "bold")).pack(anchor="w")
        tk.Label(header, text="下载游戏，自由选择启动器", fg="#bdcce5", bg="#17243d",
                 font=("", 12)).pack(anchor="w", pady=(4, 0))
        body = ttk.Frame(window, padding=(24, 16))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        controls = ttk.Frame(body)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="1  选择版本", font=("", 13, "bold")).pack(side="left")
        self.refresh = ttk.Button(controls, text="刷新列表", command=self.load_versions)
        self.refresh.pack(side="right")
        self.source_box = ttk.Combobox(controls, textvariable=self.source, values=("官方源", "BMCLAPI 镜像"),
                                       state="readonly", width=17)
        self.source_box.pack(side="right", padx=10)
        filters = ttk.Frame(body)
        filters.grid(row=1, column=0, sticky="ew", pady=10)
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="搜索版本").grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(filters, textvariable=self.search).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(filters, text="包含快照 / 预览版", variable=self.snapshots,
                        command=self.populate).grid(row=0, column=2, padx=(16, 0))
        self.search.trace_add("write", lambda *_: self.populate())
        table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.versions = ttk.Treeview(table, columns=("version", "kind", "date"), show="headings",
                                     selectmode="browse", height=8)
        for key, title, width in (("version", "版本", 280), ("kind", "类型", 140), ("date", "发布时间", 160)):
            self.versions.heading(key, text=title)
            self.versions.column(key, width=width, anchor="w")
        self.versions.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.versions.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.versions.configure(yscrollcommand=scroll.set)
        ttk.Label(body, text="2  选择游戏目录", font=("", 13, "bold")).grid(
            row=3, column=0, sticky="w", pady=(17, 8))
        folder = ttk.Frame(body)
        folder.grid(row=4, column=0, sticky="ew")
        folder.columnconfigure(0, weight=1)
        self.path_entry = ttk.Entry(folder, textvariable=self.directory)
        self.path_entry.grid(row=0, column=0, sticky="ew")
        self.browse = ttk.Button(folder, text="浏览…", command=self.choose_directory)
        self.browse.grid(row=0, column=1, padx=(10, 0))
        ttk.Label(body, text="选择启动器使用的 .minecraft 文件夹；程序会在其中创建 versions、libraries 和 assets。",
                  wraplength=800, font=("", 10)).grid(row=5, column=0, sticky="w", pady=(7, 14))
        ttk.Label(body, textvariable=self.status, wraplength=800).grid(row=6, column=0, sticky="w")
        self.progress = ttk.Progressbar(body, mode="determinate")
        self.progress.grid(row=7, column=0, sticky="ew", pady=8)
        ttk.Label(body, textvariable=self.detail, wraplength=800, font=("", 10)).grid(row=8, column=0, sticky="w")
        self.log = tk.Text(body, height=3, bg="#e9edf5", fg="#4c5970", relief="flat",
                           wrap="word", padx=8, pady=6, state="disabled", font=("", 10))
        self.log.grid(row=9, column=0, sticky="ew", pady=10)
        actions = ttk.Frame(body)
        actions.grid(row=10, column=0, sticky="ew")
        self.open_button = ttk.Button(actions, text="打开游戏目录", command=self.open_directory)
        self.open_button.pack(side="left")
        self.download_button = ttk.Button(actions, text="下载所选版本", style="Primary.TButton", command=self.install)
        self.download_button.pack(side="right")
        self.cancel_button = ttk.Button(actions, text="取消", command=self.stop, state="disabled")
        self.cancel_button.pack(side="right", padx=10)
        ttk.Label(body, text="镜像服务：BMCLAPI · bangbang93.com  |  游戏版权归 Mojang / Microsoft 所有",
                  font=("", 9)).grid(row=11, column=0, sticky="w", pady=(12, 0))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.poll)
        window.after(150, self.load_versions)

    def choose_directory(self):
        folder = filedialog.askdirectory(title="选择 .minecraft 游戏目录")
        if folder:
            self.directory.set(folder)

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.refresh, self.download_button, self.browse, self.path_entry):
            control.configure(state="disabled" if busy else "normal")
        self.source_box.configure(state="disabled" if busy else "readonly")
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def run(self, action):
        if self.busy:
            return
        self.cancel.clear()
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
        return Downloader(source=source, cancel=self.cancel,
                          event=lambda k, v: self.messages.put((k, v)))

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
            messagebox.showinfo("选择版本", "请先从列表中选择一个版本。")
            return
        if not self.directory.get().strip():
            self.choose_directory()
            if not self.directory.get().strip():
                return
        root = Path(self.directory.get()).expanduser().resolve()
        entry = self.entries[selected[0]]
        source = self.selected_source()
        self.progress["value"] = 0
        self.last_directory = root

        def task():
            client = self.client(source)
            plan = client.plan(root, entry)
            self.messages.put(("plan", plan))
            result = client.install(root, plan)
            self.messages.put(("success", (root, plan, result)))
        self.run(task)

    def stop(self):
        self.cancel.set()
        self.status.set("正在取消，请等待当前网络连接结束…")
        self.cancel_button.configure(state="disabled")

    def open_directory(self):
        if not self.directory.get().strip():
            return
        root = Path(self.directory.get()).expanduser().resolve()
        if not root.is_dir():
            messagebox.showinfo("目录尚未创建", "先下载一个版本，或选择已有的游戏目录。")
            return
        if sys.platform == "win32":
            os.startfile(root)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(root)])

    def close(self):
        if self.busy:
            if not messagebox.askyesno("退出下载器", "取消当前任务并退出？已校验的完整文件会保留。"):
                return
            self.closing = True
            self.stop()
        else:
            self.window.destroy()

    def poll(self):
        # Bound each poll so a burst of asset progress cannot starve Tk input.
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
                self.detail.set(f"Windows 64 位 · {len(data.files):,} 个文件 · {mib(data.total_bytes)} · Java {data.java or '请按版本要求安装'}")
            elif kind == "progress":
                self.progress["value"] = data["done"] / data["total"] * 100
                self.status.set(f"已校验 {data['done']:,} / {data['total']:,} 个文件 · {mib(data['bytes'])} / {mib(data['total_bytes'])}")
                self.detail.set(f"复用 {data['cached']:,} 个已有文件 · {data['file']}")
            elif kind in ("log", "error"):
                self.log.configure(state="normal")
                self.log.insert("end", str(data) + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 150:
                    self.log.delete("1.0", "50.0")
                self.log.see("end")
                self.log.configure(state="disabled")
                if kind == "error":
                    self.status.set("任务未完成，可查看下方原因并重试。")
                    if not self.closing:
                        messagebox.showerror("下载未完成", data)
            elif kind == "success":
                root, plan, result = data
                self.progress["value"] = 100
                self.detail.set(f"{plan.version} · 复用 {result['cached']:,} 个文件 · {root}")
                if not self.closing:
                    messagebox.showinfo("下载完成", f"{plan.version} 已安装到：\n{root}\n\n在 PCL 或其他启动器中添加这个游戏目录，并刷新版本列表。\n需要自行安装 Java {plan.java or '对应版本'}。")
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
