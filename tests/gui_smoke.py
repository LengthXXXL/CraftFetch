"""Run with a display; validates filtering and main-thread event handling."""
from unittest.mock import patch
import tkinter as tk

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
        root.destroy()

    # Tk callback errors must fail the process instead of being swallowed.
    errors = []
    root.report_callback_exception = lambda *error: (errors.append(error), root.destroy())
    root.after(1800, verify)
    root.mainloop()
    if errors:
        raise errors[0][1]
    print("GUI smoke passed")
