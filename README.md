# CraftFetch

独立的 Minecraft Java 版下载器：原版、Mod、整合包、数据包、资源包、光影包、加载器和 Java。下载到标准 `.minecraft` 文件夹，再交给 PCL 或其他启动器启动。

**[一键安装 Windows 64 位版](https://github.com/LengthXXXL/CraftFetch/releases/download/v0.3.0/CraftFetch-Setup-0.3.0-Windows-x64.exe)** · [发布页 / 便携版](https://github.com/LengthXXXL/CraftFetch/releases/tag/v0.3.0) · [验证记录](VALIDATION.md)

## 一键安装

下载 EXE，双击后点击“一键安装”，完成后自动打开。支持 Windows 10 / 11 x64，无需 Python、命令行或管理员权限。程序安装到 `%LOCALAPPDATA%\Programs\CraftFetch`；运行新版安装包可覆盖更新。卸载程序会保留外部游戏目录。

## 创建游戏或 Mod 实例

1. 点击顶部“目录设置”，选择 PCL 使用的 `.minecraft` **根目录**，不要选其内部的 `versions` 文件夹。
2. 在左侧“原版”中选 Minecraft 版本，再选 `vanilla`（原版）、`fabric`、`quilt`、`forge` 或 `neoforge`。
3. 核心版本留空时自动选择；也可填写准确版本号。填写一个新的实例名称，点击“下载并创建实例”。
4. 默认按 Mojang 版本元数据部署对应的 Windows x64 Java，保存在游戏根目录的 `runtime` 内。
5. 在 PCL 中添加 / 选择同一个游戏根目录，刷新版本，选择刚创建的实例。**在该版本设置中启用游戏目录隔离，使用版本文件夹。**
6. 若 PCL 未自动发现 Java，在该版本的 Java 设置中选择程序显示的 `javaw.exe`。也可在“Java 管理”中复制路径。

每个实例附带 `CraftFetch-导入说明.txt`。新实例名需使用英文字母、数字、点、横线或下划线，不能与已有目录重名，也不能直接用原版版本号。原版基础版本会保留，供其他实例复用。

## 下载 Mod、数据包、资源包和光影

1. 在左侧选择 **Mod、数据包、资源包或光影包**，查看热门项目或输入关键词搜索；可按游戏版本和加载器筛选，结果支持翻页。
2. 图文列表显示作者图标、简介、下载量和更新日期。点击项目进入详情，查看作者提供的预览图（如有）。图片异步加载；图片暂不可用不影响资源安装。
3. 在详情页选择游戏版本和 **Fabric / NeoForge / Forge / Quilt 等实际支持的核心**，再选择作者发布的具体版本；也可只显示正式版。筛选只列出该项目实际发布过的版本。
4. 点击“来源网页 · Modrinth”会在系统浏览器打开原始项目页；作者提供源码地址时也可点击“作者源码”。
5. 在详情页下方选择**目标实例**，点击“下载所选版本”。程序检查 MC 版本和加载器并自动解析必需依赖；不会安装推荐 / 可选依赖。目录内没有实例时先在“原版”中创建。
6. **数据包需要先在游戏里创建存档**，然后在详情页刷新目标实例、选择存档。数据包写入该存档的 `datapacks`，重新进入存档或使用 `/reload` 后加载。
7. 资源包和光影包仍需在游戏设置中启用。光影需要匹配的 Iris / OptiFine 等支持，下载光影文件本身不会自动启用光影。

当前资源源为 **Modrinth**，无需 API Key。支持识别同一项目的 Mod / 数据包等不同发布形式。搜索每页显示 30 个结果。Mod 应装入匹配加载器的实例；本工具不会因为选择另一种核心筛选就更改已有实例的加载器。

已有的同名不同内容文件会保留并报出冲突。对本工具记录过的资源，不会自动替换为其他版本，以避免残留两份 Mod；需要升级时请使用新实例，或先备份、移走旧文件并清理实例内 `craftfetch-content.json` 的对应记录。依赖版本冲突或明确的不兼容关系会报错；未记录的手动添加文件、作者未声明的冲突仍需自行判断。整合包内资源由整合包统一管理，追加资源时尤其应检查已有 Mod。

## 下载 / 导入整合包

- 在线：左侧选择“整合包”，搜索并点击项目，在详情页选择作者版本、填写**新实例名称**后安装。
- 本地：在“整合包”分类点击“导入本地 .mrpack”，选择文件后填写新实例名称。
- 自动读取 `.mrpack` 中的原版和加载器版本，安装依赖及 Java，再下载客户端文件，应用 `overrides` 和 `client-overrides`。忽略独立服务器文件。
- 默认跳过客户端可选文件；可勾选“包含整合包可选文件”将它们全部安装。
- 完成后生成 PCL 可识别的独立版本目录，**无需再让 PCL 下载或导入整合包**。按上面的步骤选择目录、实例并启用版本隔离即可。

目前支持 **Modrinth `.mrpack`**；不支持 CurseForge `manifest.json` ZIP、服务端包、任意 ZIP 的自动识别或格式转换。请下载对应的 `.mrpack` 格式。

## 文件位置

```text
.minecraft/
  assets/                         # 原版资源，实例间复用
  libraries/                      # 原版与加载器依赖
  runtime/java-17-windows-x64/     # 自动部署的 Java，主版本按游戏确定
    bin/javaw.exe
  versions/
    1.20.1/                       # 原版基础版本
    My-Fabric/
      My-Fabric.json
      My-Fabric.jar
      mods/
      resourcepacks/
      shaderpacks/
      saves/存档名/datapacks/
      craftfetch-instance.json
      craftfetch-content.json
      CraftFetch-导入说明.txt
  .craftfetch/                    # 可校验缓存、临时目录、安装日志和写入锁
```

迁移时复制**整个 `.minecraft`**，因为 `assets`、`libraries` 和 Java 位于实例目录之外。PCL 中重新选择迁移后的目录及 Java 路径。程序不会修改 PCL 的配置或账号，不提供游戏登录和启动。

## 支持范围与下载行为

- 游戏文件与 Java 的目标平台均为 **Windows x64**。macOS / Linux 版本可准备 Windows 文件，不能用它们在本机启动游戏；Forge / NeoForge 安装器需要在 Windows 中执行。
- Fabric / Quilt 使用官方元数据和 Maven 依赖。Forge 自动安装支持 **Minecraft 1.13+**；NeoForge 使用现代 `net.neoforged:neoforge` 安装器（1.20.2+），1.20.1 的旧 NeoForge 坐标不在当前范围。以加载器官方实际支持的游戏版本为准。
- Java 使用 Eclipse Adoptium / Temurin 的 Windows x64 ZIP，无需管理员权限，不修改系统 PATH / JAVA_HOME。按游戏元数据选择主版本（旧元数据缺省 Java 8）；某个主版本没有可用 ZIP 时会明确报错。
- 原版支持官方源 / [BMCLAPI 镜像](https://bmclapidoc.bangbang93.com/)，选择镜像时失败会回退官方源。该选项只影响支持映射的原版文件；Modrinth、加载器、Java 和 Forge 安装器使用各自下载服务。
- 原版及加载器核对 SHA-1；Modrinth 文件核对提供的 SHA-1 / SHA-512；Java ZIP 核对 SHA-256，复用部署时核对每个文件。ZIP 路径、链接、重复名称及解压大小受检查。
- 默认 6 个下载任务，网络失败重试 3 次；已完成且校验正确的文件可复用。不支持未完成单个文件的 HTTP Range 断点续传。
- 整合包在临时目录组装，完成后才显示新实例；失败可保留已完成的原版、Java 和缓存。失败追加资源时，已校验的新增文件可能保留，可重试完成；程序不会覆盖预先存在的不同内容文件。
- 取消等待当前网络读取结束；Forge / NeoForge 安装器会停止。强制终止程序可能留下临时文件；确认无下载任务后可清理 `.craftfetch/staging` 和 `.craftfetch-` 临时文件。缓存可在关闭程序后清理，之后下载将重新获取文件。
- 没有标准资源索引的远古版本不支持；非必需依赖、加载器以外的特殊安装步骤、任意历史整合包及所有 PCL 版本不能保证通用兼容。

## 从源码运行

需要 Python 3.10+ 和 Tkinter（Linux 通常另装 `python3-tk`）。普通用户直接使用上面的一键安装包。

```sh
python -m pip install -r requirements.txt
python -m craftfetch
```

## 命令行

```sh
# 原版计划（只读）
python -m craftfetch --version 1.20.1 --directory "D:/Games/.minecraft" --plan

# 创建 Fabric 隔离实例并自动部署 Java
python -m craftfetch --version 1.20.1 --loader fabric --instance My-Fabric --directory "D:/Games/.minecraft"

# 搜索 Modrinth 项目
python -m craftfetch --search sodium --type mod --version 1.20.1 --loader fabric

# 将 Mod 及必需依赖安装到目标实例
python -m craftfetch --project sodium --type mod --instance My-Fabric --directory "D:/Games/.minecraft"

# 在线安装整合包 / 导入本地整合包
python -m craftfetch --project fabulously-optimized --type modpack --version 1.20.1 --loader fabric --instance My-Pack --directory "D:/Games/.minecraft"
python -m craftfetch --pack "D:/Downloads/example.mrpack" --instance My-Pack --directory "D:/Games/.minecraft"

# 存档中安装数据包
python -m craftfetch --project terralith --type datapack --instance My-Fabric --world "New World" --directory "D:/Games/.minecraft"

# 单独部署 Java
python -m craftfetch --java 21 --directory "D:/Games/.minecraft"
```

可选参数：`--source bmclapi`、`--workers 1..16`、`--loader-version`、`--resource-version`（Modrinth 版本 ID）、`--include-optional`、`--no-java`。不带 `--instance` 的原版下载命令保留旧版目录行为；Forge / NeoForge 安装仍需 Java。

## 测试和构建

```sh
python -m unittest discover -s tests -v
python -m tests.gui_smoke
python -m tools.content_smoke --report artifacts/content-live.json
# Windows 上额外执行真实 Forge / NeoForge 安装
python -m tools.content_smoke --forge --report artifacts/content-live-windows.json
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm --onefile --windowed --name CraftFetch --collect-all certifi --collect-data craftfetch run.py
# Windows 需 Inno Setup 6.3+
python -m tools.build_installer
```

[构建工作流](.github/workflows/build.yml)运行跨平台测试和打包，并验证 Windows 一键安装、自动启动、再次安装和卸载。网络集成验证单独记录结果。未签名的程序可能出现系统来源提示。

## 来源与许可

- [Mojang 版本列表](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json)、官方 CDN 与用户选择的 BMCLAPI。
- [Modrinth 公共 API](https://docs.modrinth.com/api/) 和 [.mrpack 格式](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)。文件按作者提供的下载地址获取，项目不重新分发它们。
- [Fabric Meta](https://meta.fabricmc.net/)、[Quilt Meta](https://meta.quiltmc.org/)、[Forge 安装器](https://github.com/MinecraftForge/Installer/tree/2.0)、[NeoForge Maven](https://maven.neoforged.net/)。
- [Eclipse Adoptium API](https://api.adoptium.net/)：Java 包内保留原始许可文件。
- 导航图标使用 Tabler Icons（MIT），图片解码使用 Pillow。许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。项目图标和预览图在浏览时从作者地址获取，不打包重新分发。
- 下载器代码使用 [MIT 许可](LICENSE)。游戏、Mod、资源、运行时和依赖各自遵循原有许可；本项目的 MIT 许可不覆盖它们。

CraftFetch 与 PCL、Mojang、Microsoft 无隶属关系。项目不包含 PCL 源码、游戏二进制或账号凭据；不验证或授予 Minecraft 使用权。
