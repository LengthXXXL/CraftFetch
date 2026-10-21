"""Run with a display; validates filtering and main-thread event handling."""
from unittest.mock import patch
import tkinter as tk
import tempfile
from pathlib import Path
import json

from craftfetch.gui import App

fixture = {"latest": {"release": "1.20.1"}, "versions": [
    {"id": "1.20.1", "type": "release", "releaseTime": "2023-06-12"},
    {"id": "23w31a", "type": "snapshot", "releaseTime": "2023-08-02"}]}

with patch("craftfetch.gui.Downloader.manifest", return_value=fixture):
    root = tk.Tk()
    app = App(root)

    def verify():
        assert len(app.versions.get_children()) == 1
        app.snapshots.set(True)
        app.populate()
        assert len(app.versions.get_children()) == 2
        app.search.set("23w")
        assert len(app.versions.get_children()) == 1
        assert app.versions.item(app.versions.get_children()[0], "values")[0] == "23w31a"
        assert len(app.tabs.tabs()) == 3
        app.loader.set("fabric")
        assert "fabric" in app.instance_name.get()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "versions/Test"
            folder.mkdir(parents=True)
            (folder / "Test.json").write_text('{}')
            (folder / "craftfetch-instance.json").write_text(json.dumps({
                "minecraft": "1.20.1", "loader": "fabric", "java_major": 17, "java": ""}))
            world = folder / "saves/Test world"
            world.mkdir(parents=True)
            (world / "level.dat").write_bytes(b'world')
            app.directory.set(temp)
            app.scan_instances()
            assert app.target.get() == "Test"
            assert app.game_filter.get() == "1.20.1"
            assert app.loader_filter.get() == "fabric"
            assert app.world.get() == "Test world"
            app.tabs.select(1)
            app.kind.set("数据包")
            app.set_busy(True)
            assert str(app.resource_box['state']) == 'disabled'
            app.set_busy(False)
            assert str(app.resource_box['state']) == 'readonly'
            app.tabs.select(2)
            app.java_path.set(str(Path(temp) / 'runtime/bin/javaw.exe'))
            assert app.auto_java.get()
        root.destroy()

    # Tk callback errors must fail the process instead of being swallowed.
    errors = []
    root.report_callback_exception = lambda *error: (errors.append(error), root.destroy())
    root.after(1800, verify)
    root.mainloop()
    if errors:
        raise errors[0][1]
    print("GUI smoke passed")
