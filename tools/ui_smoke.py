"""Live, read-only catalogue UI regression; no game/resource installation.

--stay-open leaves the verified native window available for visual inspection.
"""
import argparse
import json
from pathlib import Path
import time
import tkinter as tk

from craftfetch.gui import App
from craftfetch.previews import project_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stay-open', action='store_true')
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('--geometry', help='native viewport for visual QA, e.g. 980x690')
    parser.add_argument('--bottom', action='store_true', help='show the detail download controls for visual QA')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root)
    if args.geometry:
        root.geometry(args.geometry)
    start = time.monotonic()
    stage = 0
    errors = []
    report = {}
    def error(*details):
        errors.append(str(details[1]))
        app.shutdown()
    root.report_callback_exception = error
    def advance():
        nonlocal stage
        if time.monotonic() - start > 150:
            error(None, RuntimeError('UI live test timed out'), None)
            return
        if app.busy:
            root.after(150, advance)
            return
        try:
            if stage == 0:
                assert app.manifest
                app.navigate('mod')
                stage = 1
            elif stage == 1:
                assert app.projects
                report['projects'] = len(app.projects)
                report['categories'] = len(app.nav_buttons)
                hit = next((p for p in app.projects if p['slug'] == 'sodium'), app.projects[0])
                app.open_project(hit)
                stage = 2
            elif stage == 2:
                assert app.project and app.all_resource_versions
                report['title'] = app.project['title']
                report['source'] = project_url(app.project, 'mod')
                report['author_versions'] = len(app.all_resource_versions)
                choices = app.detail_loader_box['values']
                assert 'fabric' in choices and 'neoforge' in choices, choices
                report['loaders'] = list(choices)
                for loader in ['fabric', 'neoforge']:
                    app.detail_loader.set(loader)
                    app.filter_versions()
                    assert app.resource_versions
                    assert all(loader in v['loaders'] for v in app.resource_versions)
                    report[loader+'_versions'] = len(app.resource_versions)
                app.detail_loader.set('全部')
                app.filter_versions()
                stage = 3
                root.after(4000, advance)
                return
            elif stage == 3:
                report['rendered_detail_images'] = len(app.preview_photos)
                assert app.preview_photos, 'Author icon/gallery did not render'
                if not args.detail:
                    app.back_to_results()
                    stage = 4
                    root.after(4000, advance)
                    return
                finish()
                return
            elif stage == 4:
                report['rendered_list_images'] = len(app.preview_photos)
                assert app.preview_photos, 'Project icons did not render'
                finish()
                return
        except Exception as exc:
            error(None, exc, None)
            return
        root.after(150, advance)
    def finish():
        if args.bottom and args.detail:
            root.after(100, lambda: app.detail_scroll.canvas.yview_moveto(1))
        report['seconds'] = round(time.monotonic()-start, 1)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
        if not args.stay_open:
            app.shutdown()
    root.after(300, advance)
    root.mainloop()
    if errors:
        raise RuntimeError('\n'.join(errors))


if __name__ == '__main__':
    main()
