# CraftFetch

独立的 Minecraft Java 版原版下载器。将游戏文件安装到标准 `.minecraft` 目录，再由 PCL、PCL-CE 或其他支持此目录结构的启动器识别和启动。

这是独立编写的第三方工具，与 PCL、Mojang、Microsoft 无隶属关系。项目不包含 PCL 源码、游戏二进制或账号凭据。

## 首版范围

- 原生桌面界面：选择版本、筛选快照、搜索、选择游戏目录、查看进度、取消和重试。
- 标准目录：`versions/<版本>/<版本>.json` / `.jar`、`libraries`、`assets/indexes`、`assets/objects`。
- 按 **Windows x64** 规则下载原版游戏、本地库和依赖库，包括旧资源的 virtual / resources 映射。
- 官方源与 [BMCLAPI 镜像](https://bmclapidoc.bangbang93.com/)；选择镜像时，镜像失败或校验不通过会回退到官方源。选择官方源时只访问官方源。
- 每个游戏文件核对 Mojang 元数据提供的 SHA-1 和长度；已校验的完整文件会复用，损坏的文件会重新下载。
- 最多 6 个默认并行任务，失败自动重试 3 次。重试可复用完整文件，**不支持未完成单个文件的 HTTP Range 续传**。
- 新安装只有全部文件成功后才写入启动器可识别的版本 JSON；已有不同配置不会覆盖。同目录只允许一个安装任务。

首版暂不包含 Forge / NeoForge / Fabric / Quilt、整合包、模组管理、Java 自动安装和游戏启动。远古版本若没有标准资源索引会明确提示不支持。下载器可在 Windows、macOS 和 Linux 上运行，但**首版下载的游戏文件均面向 Windows x64**，不代表支持在 macOS / Linux 上启动这些游戏。

## 使用桌面界面

开发环境需要 Python 3.10 或更新版本，且包含 Tkinter。Windows 的 Python 官方安装包通常已包含 Tkinter；Linux 可能需要安装 `python3-tk`。

```sh
python -m pip install -r requirements.txt
python -m craftfetch
```

1. 选择正式版或勾选“包含快照 / 预览版”，可搜索版本号。
2. 选择 PCL 实际使用的 `.minecraft` 文件夹，也可以创建一个新的专用目录。
3. 点击“下载所选版本”，等待校验完成。
4. 在 PCL 的版本选择页面添加 / 选择同一个游戏目录，并刷新版本列表。
5. 按提示自行安装对应 Java，并使用启动器登录自己的游戏账号后启动。

PCL 如果使用了自定义游戏目录，请以 PCL 中显示的路径为准，不要只凭文件夹名称猜测。首次测试建议使用新目录。下载器不会读取账号、修改存档或修改启动器设置。不同启动器的具体版本仍需要实机验证；标准目录兼容不等于保证每个版本都兼容。

网络超时或镜像不可用时，可取消后换源重试。取消会等待正在进行的网络读取结束，最长可能需要一个连接超时时间；完整文件保留，临时碎片清理。若任务被操作系统强制结束，可能留下以 `.craftfetch-` 开头的临时文件；关闭下载器后可以删除这些临时文件。

## 命令行

```sh
# 列出正式版本
python -m craftfetch --list

# 列出正式版和快照 / 预览版
python -m craftfetch --list --snapshots

# 查看计划，不写入游戏目录
python -m craftfetch --version 1.20.1 --directory "D:/Games/.minecraft" --plan

# 使用 BMCLAPI 镜像安装（镜像失败时尝试官方源）
python -m craftfetch --version 1.20.1 --directory "D:/Games/.minecraft" --source bmclapi
```

`--workers` 可设置为 1–16。`--plan` 会读取在线元数据和资源索引，不下载游戏文件。

## 测试和打包

```sh
python -m unittest discover -s tests -v

# 需要桌面显示环境；Linux 可使用 xvfb-run
python -m tests.gui_smoke

# 真实 CDN 抽样验证（下载本体、依赖、本地库、索引、资源各一份，临时目录自动清理）
python -m tools.live_smoke --version 1.20.1

# 必须在目标系统上构建对应系统的程序
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm --onefile --windowed --name CraftFetch --collect-all certifi run.py
```

Windows 构建结果为 `dist/CraftFetch.exe`。`.exe` 自带 Python 和 Tkinter，用户不需要安装 Python。macOS / Linux 的产物分别对应其构建系统，不能当成 Windows 程序使用。桌面打包默认不带控制台，命令行使用建议直接运行源码。

[构建工作流](.github/workflows/build.yml)会在 Windows、macOS、Linux 上运行测试、检查界面并构建程序，生成含程序、说明、许可的 ZIP 和 SHA-256 文件，放在 Actions 的 Artifacts 中。工作流**不会自动发布 GitHub Release**。未签名的初版程序可能触发系统的来源确认。

## 数据来源与许可

- 版本列表：[Mojang 官方 version_manifest_v2.json](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json)。版本元数据和资源索引 URL 取自这份清单。
- 游戏文件与依赖：Mojang / Minecraft 官方 CDN，或用户主动选择的 [BMCLAPI](https://bmclapidoc.bangbang93.com/) 镜像（维护者 bangbang93）。界面保留镜像来源说明。
- 下载器代码使用 [MIT 许可](LICENSE)。游戏、第三方依赖库和镜像服务分别遵循其原有条款；本项目的 MIT 许可不覆盖它们。
- 工具负责文件安装，不验证或授予 Minecraft 使用权，也不替代购买和账号登录。
