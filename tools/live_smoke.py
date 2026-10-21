"""Explicit opt-in real CDN check; downloads five representative files to temp.

Run from the repository: python -m tools.live_smoke --version 1.20.1
Does not create a launcher-visible installation or retain game files.
"""
import argparse
import json
from pathlib import Path
import tempfile

from craftfetch.core import Downloader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.20.1")
    parser.add_argument("--source", choices=("official", "bmclapi"), default="official")
    args = parser.parse_args()
    client = Downloader(source=args.source, event=lambda kind, data: print(data, flush=True) if kind in ("phase", "log") else None)
    manifest = client.manifest()
    entry = next(v for v in manifest["versions"] if v["id"] == args.version)
    with tempfile.TemporaryDirectory(prefix="craftfetch-live-") as folder:
        root = Path(folder)
        plan = client.plan(root, entry)
        picks = [
            next(f for f in plan.files if f.path.endswith(f"/{args.version}.jar")),
            next(f for f in plan.files if f.path.startswith("libraries/") and "natives-windows" not in f.path),
            next(f for f in plan.files if "natives-windows" in f.path),
            next(f for f in plan.files if f.path.startswith("assets/indexes/")),
            next(f for f in plan.files if f.path.startswith("assets/objects/")),
        ]
        checked = []
        for item in picks:
            print(f"Checking {item.path}", flush=True)
            assert client.download(root, item) == "downloaded"
            assert client.valid(root / item.path, item)
            assert client.download(root, item) == "cached"
            checked.append({"path": item.path, "bytes": item.size, "sha1": item.sha1})
        print(json.dumps({"version": plan.version, "source": args.source, "planned_files": len(plan.files),
                          "planned_bytes": plan.total_bytes, "verified_files": checked,
                          "verified_bytes": sum(f.size for f in picks)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
