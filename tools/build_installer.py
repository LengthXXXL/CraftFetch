"""Build the Windows installer after PyInstaller has produced dist/CraftFetch.exe."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from craftfetch import __version__


def main():
    if os.name != "nt":
        raise SystemExit("Windows 安装包需要在 Windows 上构建；GitHub Actions 会自动完成此步骤。")
    root = Path(__file__).resolve().parents[1]
    if not (root / "dist" / "CraftFetch.exe").is_file():
        raise SystemExit("请先使用 PyInstaller 生成 dist/CraftFetch.exe。")
    compiler = shutil.which("ISCC.exe") or shutil.which("iscc")
    if compiler is None:
        for variable in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(variable)
            if not base:
                continue
            for version in ("7", "6"):
                candidate = Path(base) / f"Inno Setup {version}" / "ISCC.exe"
                if candidate.is_file():
                    compiler = str(candidate)
                    break
            if compiler:
                break
    if compiler is None:
        raise SystemExit("未找到 Inno Setup 6.3 或更新版本。请安装后重新运行。")
    output = root / "artifacts"
    output.mkdir(exist_ok=True)
    subprocess.run([compiler, f"/DMyAppVersion={__version__}", f"/DSourceRoot={root}",
                    f"/O{output}", str(root / "installer" / "windows.iss")], check=True)
    setup = output / f"CraftFetch-Setup-{__version__}-Windows-x64.exe"
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    setup.with_suffix(".exe.sha256").write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
    print(f"Windows installer: {setup}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
