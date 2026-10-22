"""Offline desktop regression for navigation, project details and author-version choices."""
from unittest.mock import patch
import tkinter as tk
import tempfile
from pathlib import Path
import json
import io
from PIL import Image
from craftfetch.gui import App

fixture = {'latest': {'release': '1.20.1'}, 'versions': [
    {'id': '1.20.1', 'type': 'release', 'releaseTime': '2023-06-12'},
    {'id': '23w31a', 'type': 'snapshot', 'releaseTime': '2023-08-02'}]}
hit = {'project_id': 'test', 'slug': 'sodium', 'title': 'Sodium', 'author': 'CaffeineMC',
       'description': 'A high-performance rendering engine replacement for Minecraft.',
       'downloads': 1200000, 'categories': ['fabric', 'neoforge'], 'date_modified': '2026-10-22',
       'icon_url': 'https://cdn.modrinth.com/icon.png'}
project = {**hit, 'id': 'test', 'project_type': 'mod', 'source_url': 'https://github.com/CaffeineMC/sodium',
           'gallery': [{'url': 'https://cdn.modrinth.com/gallery.png', 'title': 'Preview'}]}
def version(name, loader, game, kind='release'):
    return {'id': name, 'name': name, 'version_number': name, 'project_id': 'test',
            'loaders': [loader], 'game_versions': [game], 'version_type': kind, 'date_published': '2026-10-22',
            'files': [{'filename': name+'.jar', 'size': 1234}]}
versions = [version('Fabric build', 'fabric', '1.20.1'), version('NeoForge build', 'neoforge', '1.21.1'),
            version('Beta build', 'fabric', '1.20.1', 'beta')]

with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp) / 'versions/Test'
    folder.mkdir(parents=True)
    (folder / 'Test.json').write_text('{}')
    (folder / 'craftfetch-instance.json').write_text(json.dumps({'minecraft': '1.20.1', 'loader': 'fabric', 'java_major': 17, 'java': ''}))
    world = folder / 'saves/Test world'
    world.mkdir(parents=True)
    (world / 'level.dat').write_bytes(b'world')
    def preview(service, token, url, size, cover=False):
        service.messages.put(('image', (token, Image.new('RGBA', size, '#6dc356'))))
    with patch('craftfetch.gui.Downloader.manifest', return_value=fixture), \
         patch('craftfetch.gui.Catalog.search', return_value={'hits': [hit], 'total_hits': 1}), \
         patch('craftfetch.gui.Catalog.project', return_value=project), \
         patch('craftfetch.gui.Catalog.versions', return_value=versions), \
         patch('craftfetch.previews.PreviewService.request', preview), \
         patch('craftfetch.gui.webbrowser.open') as browser, \
         patch('craftfetch.gui.messagebox.showerror') as errors:
        root = tk.Tk()
        app = App(root)
        failures = []
        def fail(*error):
            failures.append(error[1])
            app.shutdown()
        root.report_callback_exception = fail
        def first():
            assert not app.busy
            assert len(app.versions.get_children()) == 1
            app.snapshots.set(True)
            app.populate()
            assert len(app.versions.get_children()) == 2
            app.search.set('23w')
            assert len(app.versions.get_children()) == 1
            assert len(app.nav_buttons) == 6
            app.directory.set(temp)
            app.scan_instances()
            assert app.target.get() == 'Test'
            assert app.world.get() == 'Test world'
            app.nav_buttons['mod'][0].invoke()
            root.after(350, second)
        def second():
            assert app.category == 'mod' and app.current_page == 'catalogue'
            assert len(app.projects) == 1
            assert app.preview_photos
            app.open_project(app.projects[0])
            root.after(350, third)
        def third():
            assert app.current_page == 'detail' and app.project['title'] == 'Sodium'
            assert len(app.resource_versions) == 3
            assert 'fabric' in app.detail_loader_box['values']
            assert 'neoforge' in app.detail_loader_box['values']
            app.detail_loader.set('neoforge')
            app.filter_versions()
            assert len(app.resource_versions) == 1
            assert app.selected_release()['id'] == 'NeoForge build'
            app.detail_loader.set('fabric')
            app.only_release.set(True)
            app.filter_versions()
            assert len(app.resource_versions) == 1
            assert app.selected_release()['id'] == 'Fabric build'
            app.open_url('https://modrinth.com/mod/sodium')
            browser.assert_called_with('https://modrinth.com/mod/sodium', new=2)
            app.open_java()
            app.open_settings()
            assert app.dialogs['java'].winfo_exists()
            assert app.dialogs['settings'].winfo_exists()
            assert app.auto_java.get()
            app.back_to_results()
            assert app.current_page == 'catalogue'
            for kind in ['modpack', 'datapack', 'resourcepack', 'shader', 'vanilla']:
                app.navigate(kind)
            # An old asynchronous page result must not replace the selected page.
            app.messages.put(('projects', (-1, 'mod', {'hits': [], 'total_hits': 0})))
            root.after(500, finish)
        def finish():
            assert app.category == 'vanilla' and app.current_page == 'vanilla'
            assert not errors.called, errors.call_args
            app.shutdown()
        root.after(800, first)
        root.after(12000, lambda: fail(None, AssertionError('GUI test timed out'), None))
        root.mainloop()
        if failures:
            raise failures[0]
print('GUI smoke passed: six categories, images, detail filters, release selection, source link, dialogs, stale results')
