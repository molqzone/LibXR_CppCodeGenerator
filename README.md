<h1 align="center">
<img src="https://github.com/xrobot-org/LibXR_CppCodeGenerator/raw/main/imgs/XRobot.jpeg" width="300">
</h1><br>

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![GitHub Repo](https://img.shields.io/github/stars/xrobot-org/libxr?style=social)](https://github.com/xrobot-org/libxr)
[![Documentation](https://img.shields.io/badge/docs-online-brightgreen)](https://xrobot.work/libxr/)
[![GitHub Issues](https://img.shields.io/github/issues/xrobot-org/LibXR_CppCodeGenerator)](https://github.com/xrobot-org/LibXR_CppCodeGenerator/issues)
[![CI/CD - Python Package](https://github.com/xrobot-org/LibXR_CppCodeGenerator/actions/workflows/python-publish.yml/badge.svg)](https://github.com/xrobot-org/LibXR_CppCodeGenerator/actions/workflows/python-publish.yml)
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2FJiu-xiao%2FLibXR_CppCodeGenerator.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2FJiu-xiao%2FLibXR_CppCodeGenerator?ref=badge_shield)

`libxr` 是一个 Python 包，用于自动化嵌入式系统开发。它通过解析硬件配置文件并生成对应的 C++ 工程代码，显著降低嵌入式开发中的重复性工作。目前默认支持 STM32 平台，后续将扩展至更多硬件体系结构。

`libxr` is a Python package for automating embedded system development. It parses hardware configuration files and generates corresponding C++ project code, significantly reducing repetitive manual work. STM32 is supported by default, with more hardware architectures planned.

## 🌟 Features 功能亮点

- 🧠 由 STM32CubeMX 工程生成 LibXR 外设对象、`app_main` 入口和 CMake 接入。
  Generates the LibXR peripheral objects, the `app_main` entry and the CMake integration
  from an STM32CubeMX project.

- ⚙️ 解析器和生成器按平台选择，目前支持 STM32。
  Parsers and generators are chosen by platform; STM32 is supported so far.

- 📦 可与 XRobot 框架集成：每个生成的设备对象以自身名字和唯一类型静态注册。
  Integrates with XRobot: every generated device object is registered statically under its own name with one type.

## Static XRobot integration 静态 XRobot 集成

`--xrobot` 生成具名的 `XR_REGISTER(object, Type)` 声明，不生成运行期
HardwareContainer/ApplicationManager；`--no-xrobot` 时不生成任何 XRobot 代码。两者都不写时，
`libxr gen` 和 `libxr stm32 setup` 沿用已有 `app_main.cpp` 的选择，新工程不生成 XRobot 代码。
With `--xrobot`, the generator emits named `XR_REGISTER(object, Type)`
declarations instead of a runtime HardwareContainer/ApplicationManager; with
`--no-xrobot`, no XRobot code is generated. With neither, `libxr gen` and
`libxr stm32 setup` keep the choice of the existing `app_main.cpp`, and a new project gets
no XRobot code.

- 每个名字只注册一种类型。每个 FDCAN 对象 `fdcanN` 注册为 `LibXR::FDCAN`，并额外生成
  同作用域的基类引用 `LibXR::CAN& canN = fdcanN;` 注册为 `LibXR::CAN`。若芯片同时有
  CAN 与 FDCAN 导致 `canN` 重名，生成失败。
  One name has one type. Each FDCAN object `fdcanN` is registered as
  `LibXR::FDCAN`, and a reference `LibXR::CAN& canN = fdcanN;` in the same scope
  is registered as `LibXR::CAN`. If a chip has both CAN and FDCAN controllers so
  that `canN` collides, generation fails.
- `XROBOT_MAIN();` 由生成器维护，位于 `app_main` 的 User Code 区域之后。
  `XROBOT_MAIN();` is owned by the generator and follows the User Code regions of
  `app_main`.
- 迁移：旧版本把 `XROBOT_MAIN();` 放在 User Code 3 中。若 User Code 区域中仍有该调用，
  生成器报告其行号并停止、不写任何文件；删除该行后重新生成。
  Migration: older versions placed `XROBOT_MAIN();` in User Code 3. If a User Code
  region still calls it, the generator reports the line and stops without writing
  anything; delete that line and regenerate.

先生成 BSP 源码，再用 XRobot 工具生成 `User/xrobot_main.hpp`，然后按工程原有的
CMake 流程构建。
Generate the BSP source first, then `User/xrobot_main.hpp` with the XRobot tool,
then build with the project's own CMake flow.

## 📥 Installation 安装

### 使用pipx安装 (Install via `pipx`)

windows

```ps
python -m pip install --user pipx
python -m pipx ensurepath
pipx install libxr
pipx ensurepath
# Restart your terminal
```

linux

```bash
sudo apt install pipx
pipx install libxr
pipx ensurepath
# Restart your terminal
```

### 使用 pip 安装 (Install via `pip`)

```bash
pip install libxr
```

### 从源码安装 (Install from source)

```bash
git clone https://github.com/xrobot-org/LibXR_CppCodeGenerator.git
cd LibXR_CppCodeGenerator
python3 ./scripts/gen_libxr_version.py
pip install -e .
```

---

## 🔧 命令 / Commands

所有功能都在一个命令 `libxr` 下。`parse` 和 `gen` 与平台无关：`parse` 按工程所属的平台选择
解析器，并把平台记录在配置 YAML 中；`gen` 按记录的平台选择生成器。只属于某个平台的命令放在
平台名下，例如 `libxr stm32 setup`。目前支持 STM32（含有 STM32CubeMX `.ioc` 文件的目录）。

All functions are subcommands of one command, `libxr`. `parse` and `gen` are platform-neutral:
`parse` chooses the parser by the platform of the project and records the platform in the
configuration YAML, and `gen` chooses the generator by the recorded platform. Commands that
belong to one platform sit under its name, such as `libxr stm32 setup`. STM32 (a directory
holding an STM32CubeMX `.ioc` file) is supported so far.

| 命令 Command | 作用 | Purpose |
| --- | --- | --- |
| `libxr parse` | 解析工程，写出配置 YAML | Parse a project into the configuration YAML |
| `libxr gen` | 由配置 YAML 生成 LibXR 代码 | Generate the LibXR code from the configuration YAML |
| `libxr stm32 setup` | 把 CubeMX 工程配置为使用 LibXR | Set up a CubeMX project for LibXR |
| `libxr stm32 cubemx-gen` | 以脚本模式运行 CubeMX 生成工程 | Run CubeMX in script mode |
| `libxr stm32 cmake` | 把 LibXR 接入 CubeMX 的 CMake 工程 | Integrate LibXR into the CubeMX CMake project |
| `libxr stm32 flash-info` | 打印某个型号的 Flash 布局 | Print the flash layout of a model |
| `libxr stm32 toolchain` | 切换工具链和 clang 标准库 | Switch the toolchain and the clang standard library |

每个子命令都接受 `--verbose`，输出调试日志。

每个命令运行时在后台查询 PyPI，结束时若有更新的 libxr 就提示升级：pipx 安装的提示
`pipx upgrade libxr`，其余提示 `pip install -U libxr`。

Every subcommand takes `--verbose` for debug logging.

While a command runs, PyPI is queried in the background; a newer libxr is reported when the
command ends, with `pipx upgrade libxr` for a pipx installation and `pip install -U libxr`
otherwise.

### 旧命令 / Old commands

6.0.0 之前的 `xr_*` 命令仍可使用：运行时先提示对应的新命令，参数及其含义与原来相同。例如
`xr_parse` 仍必须给出 `-d`，`xr_gen_code` 仍按输入 YAML 所在目录识别工程，`xr_cubemx_cfg`
没有 `--xrobot` 时不使用 XRobot，`xr_cubemx_generate --auto-confirm` 相当于
`libxr stm32 cubemx-gen --firmware migrate --download`。旧命令将在 7.0.0 删除。

The `xr_*` commands of earlier versions still work: they name their new command and keep their
arguments with the same meaning. For example, `xr_parse` still needs `-d`, `xr_gen_code` still
finds the project in the directory of the input YAML, `xr_cubemx_cfg` without `--xrobot` does
not use XRobot, and `xr_cubemx_generate --auto-confirm` means
`libxr stm32 cubemx-gen --firmware migrate --download`. They are removed in 7.0.0.

| 旧命令 Old | 新命令 New |
| --- | --- |
| `xr_parse`、`xr_parse_ioc` | `libxr parse` |
| `xr_gen_code`、`xr_gen_code_stm32` | `libxr gen` |
| `xr_cubemx_cfg` | `libxr stm32 setup` |
| `xr_cubemx_generate` | `libxr stm32 cubemx-gen` |
| `xr_stm32_cmake` | `libxr stm32 cmake` |
| `xr_stm32_flash` | `libxr stm32 flash-info` |
| `xr_stm32_toolchain_switch` | `libxr stm32 toolchain` |

### 重新生成 BSP（CI 所用命令）Regenerating a BSP (what CI runs)

XRobot BSP 的 CI 在工程根目录用以下命令重新生成 BSP 对象，并检查结果与提交内容一致：
XRobot BSP CI regenerates the BSP objects with these commands in the project root and checks
that the result matches the committed files:

```bash
libxr parse -d . -o .ci-tools/cubemx.yaml
libxr gen -i .ci-tools/cubemx.yaml -o User/app_main.cpp --xrobot --libxr-config User/libxr_config.yaml
```

`User/libxr_config.yaml` 顶层可有 `generator:` 键（本工具的发布版本号或 40 位提交 SHA），
BSP CI 用它安装固定版本的生成器。生成器保留该键及文件中的注释；已安装的版本与之不同时，
`libxr gen` 给出警告。
`User/libxr_config.yaml` may contain a top-level `generator:` key (a release
version or 40-hex commit SHA of this tool) that BSP CI uses to install the pinned
generator. The generator keeps this key and the file's comments, and `libxr gen` warns when
the installed version differs from it.

---

## STM32 工程工具 (STM32 Project Tools)

### `libxr stm32 setup`

自动配置 STM32CubeMX 工程
Automatically configures an STM32CubeMX project.

```bash
usage: libxr stm32 setup [-h] [-d DIRECTORY] [-t TERMINAL] [--xrobot | --no-xrobot] [--commit COMMIT]
                         [--git-source GIT_SOURCE] [--git-mirrors GIT_MIRRORS] [--verbose]
```

加入 LibXR 子模块，解析 `.ioc` 文件，生成 YAML 和 C++ 代码，并接入 CMake
Adds the LibXR submodule, parses `.ioc`, generates the YAML and the C++ code, and integrates CMake.

改动工程之前先检查：有 `Core/`、恰好一个 `.ioc` 文件、有 `CMakeLists.txt`（CubeMX 的
Toolchain / IDE 设为 CMake）。不满足时报错，工程保持不变。
The project is checked before anything changes: it needs `Core/`, exactly one `.ioc` file and
a `CMakeLists.txt` (Toolchain / IDE set to CMake in CubeMX). Otherwise the command stops with an
error and leaves the project unchanged.

#### ⚙️ 可选参数 (Optional)

- `-d, --directory <DIRECTORY>`：

  STM32CubeMX 工程路径，默认为当前目录
  Path to the STM32CubeMX project, the current directory by default.

- `-t, --terminal <TERMINAL>`：

  终端设备名称(如 `usart1` `usb_fs_cdc`)，写入 `User/libxr_config.yaml` 的 `terminal_source`，
  之后的重新生成沿用该设置。
  Terminal device name (e.g. `usart1` `usb_fs_cdc`). It is stored as
  `terminal_source` in `User/libxr_config.yaml`, so later regenerations keep it.

- `--xrobot` / `--no-xrobot`：

  生成或不生成 XRobot 注册代码。都不写时沿用工程现在的选择：`User/app_main.cpp` 由 `--xrobot`
  生成时继续生成 XRobot 代码，新工程不生成。
  Generate XRobot registrations, or not. Without either, the project keeps its choice:
  XRobot code is generated again when `User/app_main.cpp` was generated with `--xrobot`, and a
  new project gets none.

- `--commit <COMMIT>`

  显式指定 LibXR 仓库 commit 版本；提供该参数时工具会切到这个 commit。
  Explicitly select the LibXR repository commit; when provided, the tool checks out this commit.

  不提供 `--commit` 时，已有的 LibXR checkout 保持不动：工程的 gitlink 固定 LibXR 版本。checkout 比本工具的
  默认 commit 旧或与之不同时只打印警告和切换命令；只有本次新添加的子模块才检出默认 commit，未初始化的
  已登记子模块检出 gitlink 记录的 commit。需要切换版本时显式使用 `--commit`，然后提交 gitlink。
  When `--commit` is omitted, an existing LibXR checkout is never moved: the project's gitlink pins LibXR. A
  checkout older than or different from this tool's default commit only produces a warning with the command to
  switch. Only a submodule added by this run is checked out at the default commit; a registered but
  uninitialized submodule is checked out at its recorded gitlink. Use `--commit` to switch, then commit the
  gitlink.

  已存在的 `Middlewares/Third_Party/LibXR` 目录不会被删除、移动或重新克隆；若它不是有效的
  Git checkout（且非空），命令报错并保持目录不变。
  An existing `Middlewares/Third_Party/LibXR` directory is never deleted, moved or
  re-cloned; if it is not a valid Git checkout (and not empty), the command stops
  with an error and leaves it untouched.

- `--git-source`

  需要克隆 LibXR 时从哪里克隆：`auto`、`github`、base URL、完整仓库 URL 或本地仓库（默认：
  `auto`）。从本地仓库克隆时，工具只对这次克隆放行 git 的 file 协议（git 2.38 起子模块默认禁止）。
  Where LibXR is cloned from when a clone is needed: `auto`, `github`, a base URL, a full
  repository URL or a local repository (default: `auto`). For a local repository the tool
  allows git's file protocol for that clone only, which submodules forbid by default since
  git 2.38.

  示例 / Examples:
  ```bash
  --git-source https://gitee.com/jiu-xiao/libxr
  --git-source D:/mirrors/libxr
  ```

- `--git-mirrors`

  逗号分隔的镜像 base/完整仓库 URL 列表，`--git-source` 为 `auto` 时作为候选参与测速。
  Comma-separated mirror base/full repo URLs; candidates when `--git-source` is `auto`.

  示例 / Examples:
  ```bash
  --git-mirrors "https://gitee.com/jiu-xiao/libxr"
  ```

  或通过环境变量追加 / Or via environment variable:
  ```bash
  export XR_GIT_MIRRORS="https://gitee.com/jiu-xiao/libxr"
  ```

#### 🌐 网络与镜像说明 (Networking & Mirrors)

只有需要克隆 LibXR（新加入子模块，或已登记但还没有检出）时，工具才在 GitHub 与内置/自定义镜像间
测速并从最快的源克隆；测速时 git 不提示输入账号密码。
Only when LibXR has to be cloned (a new submodule, or a registered one without a checkout) does
the tool probe GitHub and the built-in/custom mirrors and clone from the fastest; probes never
ask for credentials.

镜像只用于这次克隆：工程的 `.gitmodules` 和子模块的 origin 始终是
`https://github.com/xrobot-org/libxr.git`。新加入的子模块检出默认 commit 后，其 gitlink 已经暂存，
直接提交即可。
A mirror is used for that clone only: the project's `.gitmodules` and the submodule's origin are
always `https://github.com/xrobot-org/libxr.git`. After a new submodule is checked out at the
default commit, its gitlink is staged and ready to commit.

#### 🤖 CubeMX 自动生成说明 (CubeMX Automation Notes)

`libxr stm32 cubemx-gen` 可以单独使用，只做 CubeMX 脚本模式生成，不执行后续 LibXR 解析和代码生成。
`libxr stm32 cubemx-gen` can be used on its own when only CubeMX script-mode generation is wanted.

最小脚本模式命令示例 / Minimal example:

```bash
libxr stm32 cubemx-gen
```

Windows 示例：指定 CubeMX，沿用工程原来的固件包，并保存日志 / Windows example: name CubeMX, keep the project's firmware package and save the logs:

```powershell
libxr stm32 cubemx-gen -d . `
  --cubemx-cmd "$env:LOCALAPPDATA\Programs\STM32CubeMX\STM32CubeMX.exe" `
  --firmware keep `
  --log-dir .cubemx-logs
```

STM32CubeMX 依次从 `--cubemx-cmd`、环境变量 `STM32CUBEMX_CMD`（或 `CUBEMX_CMD`、`STM32CUBEMX`）和默认安装位置查找。安装目录中有 `jre` 时，默认的 `--launch-mode auto` 用这份 JRE 以 `java -jar` 启动 CubeMX 并等待它结束；jar 是旁边的 `STM32CubeMX.jar`，CubeMX 6.18 起则是 `STM32CubeMX.exe` 本身。Windows 上直接运行 `STM32CubeMX.exe` 会在启动 Java 后立即返回，所以 `--launch-mode direct` 只适合其他 CubeMX 启动脚本。`--launch-mode java` 要求 `--cubemx-cmd` 是 STM32CubeMX `.jar`，或是这样一个带 `jre` 的安装中的可执行文件；`--java-cmd` 可以替换所用的 Java。
STM32CubeMX is looked up in `--cubemx-cmd`, the environment variable `STM32CUBEMX_CMD` (or `CUBEMX_CMD`, `STM32CUBEMX`) and the default install locations, in that order. When the installation holds `jre`, the default `--launch-mode auto` starts CubeMX through `java -jar` with that JRE and waits for it to finish; the jar is `STM32CubeMX.jar` next to the executable, or from CubeMX 6.18 `STM32CubeMX.exe` itself. On Windows, running `STM32CubeMX.exe` directly returns as soon as Java has started, so `--launch-mode direct` suits other CubeMX start scripts only. `--launch-mode java` needs `--cubemx-cmd` to be an STM32CubeMX `.jar` or the executable of such an installation with `jre`; `--java-cmd` replaces the Java used.

#### 💬 CubeMX 对话框 (CubeMX Dialogs)

脚本模式下 CubeMX 仍会弹出对话框。libxr 认出其中常见的几种，按命令行参数回答：
CubeMX still shows dialogs in script mode. libxr recognizes the common ones and answers them as the command line says:

| 对话框 / Dialog | 回答 / Answer |
| --- | --- |
| 工程由另一版本的 CubeMX 保存（`New STM32Cube firmware version available`）<br>Project saved by another CubeMX version | `--firmware keep`：Continue，沿用工程原来的固件包 / keep the project's firmware package<br>`--firmware migrate`：Migrate，迁移到当前 CubeMX 和固件包 / migrate to the current CubeMX and firmware package<br>未给出时停止 / stop when not given |
| 缺少固件包、下载确认、固件包许可协议<br>Missing firmware package, download confirmation, package license | `--download`：下载并接受许可协议 / download and accept the license<br>未给出时停止 / stop when not given |
| 下载、解压等进度窗口<br>Progress windows (download, unpacking) | 等待它关闭 / wait until it closes |
| ST 账号登录 / ST account login | 停止 / stop |
| 其他对话框 / Any other dialog | 停止 / stop |

停止时 libxr 结束 CubeMX，输出对话框的标题、正文和按钮，并以状态 1 退出。每条脚本命令都返回 OK、期望的路径都存在时，生成才算成功。
On a stop, libxr ends CubeMX, prints the title, text and buttons of the dialog, and exits with status 1. The generation succeeds only when every script command returns OK and the expected paths exist.

Windows 上 libxr 通过 CubeMX 自带 JRE 中的 Java Access Bridge 读取对话框并点击按钮，因此需要以 `java -jar` 启动（`--launch-mode auto` 或 `java`）；以 `direct` 启动时，任何对话框都会停止运行。Linux 上 libxr 在 X11 显示（`DISPLAY`）中发现 CubeMX 的对话框后停止运行，并报出对话框标题；这时在 CubeMX 中打开工程，处理对话框并保存，再重新运行。
On Windows, libxr reads dialogs and clicks their buttons through the Java Access Bridge of CubeMX's bundled JRE, so CubeMX has to be started through `java -jar` (`--launch-mode auto` or `java`); with `direct`, any dialog stops the run. On Linux, libxr stops the run when a CubeMX dialog appears on the X11 display (`DISPLAY`) and reports its title; open the project in CubeMX, handle the dialog, save, and run again.

#### 📦 输出内容 (Outputs)

- `.config.yaml`：

  由 `.ioc` 解析得到的配置
  Configuration parsed from the `.ioc` file

- `User/app_main.cpp`、`User/app_main.h`、`User/libxr_config.yaml`、`User/flash_map.hpp`：

  生成的 C++ 代码与配置（见 `libxr gen`）
  Generated C++ code and configuration (see `libxr gen`)

- `cmake/LibXR.CMake`、`CMakeLists.txt`、`.gitignore`（见 `libxr stm32 cmake`）
  (see `libxr stm32 cmake`)

- 初始化的 Git 仓库及 LibXR 子模块
  Initialized Git repository and LibXR submodule

---

### `libxr stm32 cubemx-gen`

独立运行 STM32CubeMX 脚本模式生成。
Run STM32CubeMX script-mode generation as a standalone step.

```bash
usage: libxr stm32 cubemx-gen [-h] [-d DIRECTORY] [--ioc IOC] [--cubemx-cmd CUBEMX_CMD] [--java-cmd JAVA_CMD]
                              [--launch-mode {auto,direct,java}] [--generate-code-dir GENERATE_CODE_DIR]
                              [--expect-path EXPECT_PATH] [--log-dir LOG_DIR] [--script-path SCRIPT_PATH]
                              [--keep-script] [--silent] [--firmware {keep,migrate}] [--download] [--timeout TIMEOUT]
                              [--verbose]
```

用途 / Purpose:

- 只做 CubeMX 工程生成，不做 LibXR 解析或代码生成
  Run CubeMX generation only, without LibXR parsing or code generation
- 为 CI 产出命令、脚本和日志工件
  Produce command/script/log artifacts for CI
- 在已有 `.ioc` 上做预生成或复现 GUI 迁移流程
  Pre-generate projects or reproduce GUI migration flows from an existing `.ioc`

默认会校验 `Core/Inc` 和 `Drivers` 已生成；如需覆盖默认值，可重复传入 `--expect-path`。
By default, the runner verifies that `Core/Inc` and `Drivers` were generated; pass `--expect-path` one or more times to override the defaults.

#### CubeMX 前置条件 / CubeMX Prerequisites

`cubemx-gen` 使用本机 CubeMX 的用户状态和固件包仓库。需要登录 ST 账号时运行会停止，登录需要先在 CubeMX 中完成；缺少的 `STM32Cube_FW_*` 包可以预先在 CubeMX 中安装，也可以用 `--download` 在生成时下载。工程由另一版本的 CubeMX 保存时，用 `--firmware` 选择沿用或迁移（见上文“CubeMX 对话框”）。
`cubemx-gen` uses the user state and the firmware repository of the local CubeMX. A run that needs an ST account login stops, so the login has to be done in CubeMX first; a missing `STM32Cube_FW_*` package can be installed in CubeMX beforehand or downloaded during generation with `--download`. For a project saved by another CubeMX version, `--firmware` chooses between keeping and migrating (see "CubeMX Dialogs" above).

CI 运行 CubeMX 时，同样需要这样准备好的桌面环境。
CI that runs CubeMX needs a desktop environment prepared the same way.

#### 📦 输出内容 (Outputs)

- `cubemx_generate.txt` 或显式指定的脚本文件
  Generated CubeMX script file

- 可选日志目录中的命令、stdout、stderr
  Optional command/stdout/stderr logs in `--log-dir`

- 默认校验的 `Core/Inc`、`Drivers`，或由 `--expect-path` 覆盖的生成目录/文件
  Default `Core/Inc` and `Drivers` checks, or generated directories/files overridden through `--expect-path`

---

### `libxr parse`

自动解析 STM32CubeMX 工程配置
Parses `.ioc` files from STM32CubeMX projects and exports structured YAML.

```bash
usage: libxr parse [-h] [-d DIRECTORY] [-o OUTPUT] [--verbose]
```

解析 `.ioc` 文件为 `.config.yaml`，并在终端输出解析摘要
Parses `.ioc` files and creates `.config.yaml` with a readable summary.

#### ⚙️ 可选参数 (Optional)

- `-d, --directory <DIRECTORY>`
  `.ioc` 文件所在目录路径，默认为当前目录
  Path to the directory containing the `.ioc` file, the current directory by default.

- `-o, --output <FILE>`
  YAML 输出路径，默认为 DIRECTORY 下的 `.config.yaml`。目录中有多个 `.ioc` 文件时报错。
  Output YAML path; the default is `.config.yaml` in DIRECTORY. A directory with
  several `.ioc` files is an error.

- `--verbose`
  启用调试日志，输出详细解析过程
  Enable verbose logging.

#### 📦 输出内容 (Outputs)

- `.config.yaml`：

  包含平台（`Platform: stm32`，`libxr gen` 据此选择生成器）以及 GPIO、外设、DMA、FreeRTOS、
  MCU 等配置
  YAML file containing the platform (`Platform: stm32`, from which `libxr gen` chooses the
  generator) and the GPIO, peripheral, DMA, FreeRTOS, and MCU configurations.

- 控制台摘要：MCU 信息、GPIO 数量、外设统计等
  Console summary: MCU information, GPIO count, peripheral statistics, etc.

---

### `libxr gen`

根据 YAML 配置生成 STM32 硬件抽象层代码，可选生成 XRobot 集成代码。
Generates STM32 application code from YAML.

```bash
usage: libxr gen [-h] -i INPUT [-d DIRECTORY] -o OUTPUT [--xrobot | --no-xrobot] [--libxr-config LIBXR_CONFIG]
                 [--verbose]
```

#### 🔧 Required

- `-i`：

  `.config.yaml` 配置文件路径
  Path to `.config.yaml`

- `-o`：

  生成的 `app_main.cpp` 路径；其余输出写入同一目录（只给文件名时为当前目录）
  Path of the generated `app_main.cpp`; the other outputs go to the same
  directory (the current directory for a bare file name)

#### ⚙️ Optional

- `-d`：

  工程目录，默认为当前目录。配置 YAML 没有记录平台时（旧版 `parse` 写出的文件）按它的平台选择
  生成器
  Project directory, the current directory by default; its platform chooses the generator
  when the configuration YAML records none (a file written by an older `parse`)

- `--xrobot` / `--no-xrobot`：

  生成或不生成 XRobot 静态注册与 `XROBOT_MAIN();`（见上文 Static XRobot integration）；都不写时
  沿用已有输出文件的选择
  Emit XRobot static registrations and `XROBOT_MAIN();`, or not (see Static XRobot
  integration); with neither, the existing output file keeps its choice

- `--libxr-config`：

  自定义 libxr_config.yaml 路径(可为本地或远程，远程下载时限 30 秒)；无法找到、读取或解析时生成失败
  Path or URL to runtime config YAML (a download times out after 30 s); generation fails if
  it cannot be found, read or parsed

- `--verbose`：

  输出调试日志；生成失败时同时打印调用栈
  Enable debug logging; a failed generation also prints the traceback

#### 📦 Outputs

全部文件先在内存中生成，没有错误时才写出，且只写内容有变化的文件；内容不变的文件保持原来的
修改时间，不会触发重新编译。结束时用一行日志列出写入、未变化和删除的文件。
All files are generated in memory first and written only when nothing failed, and only the
files whose content changed are written; unchanged files keep their modification time and
trigger no rebuild. One log line at the end lists the files written, unchanged and removed.

- `app_main.cpp`：
  主入口文件，包含所有初始化逻辑。`/* User Code Begin N */` 与 `/* User Code End N */`
  之间的内容在重新生成时保留。标记缺失、重复、改名、不成对或位于 `#if` 等条件编译块内时，
  生成器报告并停止、不写任何文件。GPIO 标签作为对象名，若是 C++ 关键字、宏或与生成代码中的
  名字冲突，生成器同样报错。
  Main entry point with all initialization logic. Code between
  `/* User Code Begin N */` and `/* User Code End N */` is kept on regeneration.
  If a marker is missing, duplicated, renamed, unpaired or inside a preprocessor
  conditional, the generator reports it and stops without writing anything. GPIO
  labels become object names; a label that is a C++ keyword, a macro, or a name
  the generated code already uses is reported as an error as well.

- `libxr_config.yaml`：
  运行时配置文件，可自定义缓冲区大小、队列等参数；重新生成时保留注释和未知键（如 `generator:`）。
  文件无法读取或解析时生成失败，不会被默认值覆盖。
  Runtime config YAML, can be customized with buffer size, queue, etc. Comments
  and keys the generator does not use (such as `generator:`) are kept. A file
  that cannot be read or parsed stops generation instead of being reset to
  defaults.
  外设实例的键为小写实例名（如 `spi1`、`can1`）；以前版本写成大写的 CAN/FDCAN 键（如 `CAN1`）
  在生成时改为小写并提示。
  Peripheral instances are keyed by their lower-case name (such as `spi1` or `can1`); the
  upper-case CAN/FDCAN keys of earlier versions (such as `CAN1`) are renamed on generation
  with a notice.
  软件定时器（`software_timer.priority`）、终端线程和看门狗线程（`thread_priority`）的优先级写
  0-4 或等级名 `IDLE`、`LOW`、`MEDIUM`、`HIGH`、`REALTIME`；生成的代码使用对应的
  `LibXR::Thread::Priority` 等级，由 LibXR 按 RTOS 的优先级数换算。其他值使生成失败。
  The priorities of the software timer (`software_timer.priority`) and of the terminal and
  watchdog threads (`thread_priority`) take 0-4 or the level names `IDLE`, `LOW`, `MEDIUM`,
  `HIGH` and `REALTIME`; the generated code uses the matching `LibXR::Thread::Priority`
  level, which LibXR converts by the RTOS priority count. Any other value stops generation.
  缓冲区、队列、FIFO、栈深度、超时和终端尺寸必须是正整数，`dma_enable_min_size` 可以为 0，
  USB 的 `vid`、`pid`、`bcd` 在 0 到 0xFFFF 之间，`vref` 和 `init_voltage` 必须是数字；不合要求时
  生成失败，报错中写出键名。USB 的 `ep0_packet_size` 不是 8、16、32、64 时给出警告并改为 8。
  Buffer, queue, FIFO, stack-depth, timeout and terminal sizes must be positive integers,
  `dma_enable_min_size` may be 0, the USB `vid`, `pid` and `bcd` lie between 0 and 0xFFFF, and
  `vref` and `init_voltage` must be numbers; otherwise generation stops with an error that
  names the key. A USB `ep0_packet_size` other than 8, 16, 32 or 64 is warned about and
  becomes 8.

- `flash_map.hpp`：
  自动生成的 Flash 扇区表，供 Flash 抽象层使用。推算不出 MCU 型号的 Flash 布局时给出警告，
  不生成该文件，`app_main.cpp` 也不 include 它，以前生成的 `flash_map.hpp` 被删除。
  Auto-generated flash sector layout for use with Flash abstraction layer. When no flash
  layout can be derived for the MCU, a warning is logged, the file is not generated,
  `app_main.cpp` does not include it, and a previously generated `flash_map.hpp` is deleted.

---

### `libxr stm32 flash-info`

解析 STM32 型号，生成 Flash 扇区信息表（YAML 格式输出）。
Parses STM32 model name and generates flash layout info (YAML output).

```bash
usage: libxr stm32 flash-info [-h] [--verbose] model
```

型号无效或无法推算布局时记录错误并以状态 1 退出；旧命令 `xr_stm32_flash` 仍以状态 2 退出。
An invalid model or one whose layout cannot be derived logs an error and exits with status 1;
the old `xr_stm32_flash` command still exits with status 2.

### 🧠 功能说明 (Functionality)

- 根据 STM32 型号名称自动推导 Flash 大小
  Automatically infers flash size from the STM32 model string

- 根据芯片系列（如 F1/F4/H7/U5 等）生成对应的扇区布局
  Generates sector layout depending on the chip series (e.g., F1/F4/H7/U5)

- 输出包括每个扇区的地址、大小和索引
  Output includes address, size, and index of each sector

### 📦 输出内容 (Outputs)

- YAML 格式的 Flash 信息
  Flash info in YAML format:

```yaml
model: STM32F103C8
flash_base: '0x08000000'
flash_size_kb: 64
sectors:
- index: 0
  address: '0x08000000'
  size_kb: 1.0
- index: 1
  address: '0x08000400'
  size_kb: 1.0
  ...
```

---

### `libxr stm32 cmake`

为 STM32CubeMX 工程生成 `LibXR.CMake` 配置，并自动集成至 `CMakeLists.txt`。
Generates `LibXR.CMake` file and injects it into the STM32CubeMX CMake project.

```bash
usage: libxr stm32 cmake [-h] [-d DIRECTORY] [--verbose]
```

#### ⚙️ 可选参数 (Optional)

- `-d, --directory <DIRECTORY>`：

  CubeMX 生成的 CMake 工程根目录，默认为当前目录
  Root of the CubeMX-generated CMake project, the current directory by default

#### ⚙️ 功能说明 (Functionality)

- 自动生成 `cmake/LibXR.CMake` 文件，内容包括：
  Generate `cmake/LibXR.CMake` containing:

  - 添加 `LibXR` 子目录
    Add `LibXR` as a subdirectory

  - 链接 `xr` 静态库
    Link the `xr` static library

  - 添加 `Core/Inc`、`User` 目录为包含路径
    Include `Core/Inc` and `User` directories

  - 添加 `User/*.cpp` 为源文件（`CONFIGURE_DEPENDS`：新加入的文件在下次构建时被编译）
    Add `User/*.cpp` to project sources (`CONFIGURE_DEPENDS`: a new file is compiled on
    the next build)

  - 仅当 `User/app_main.cpp` 由 `libxr gen --xrobot` 生成时，设置
    `XROBOT_MODULES_DIR` 指向 `Modules/`；纯 LibXR 工程不设置。
    Set `XROBOT_MODULES_DIR` to `Modules/` only when `User/app_main.cpp` was
    generated by `libxr gen --xrobot`; LibXR-only projects do not set it.

- 已存在的 `cmake/LibXR.CMake` 只调整 C++20、`LIBXR_SYSTEM` 和 `User/*.cpp` 的
  `CONFIGURE_DEPENDS`；`XROBOT_MODULES_DIR` 与工程不一致时给出警告。
  An existing `cmake/LibXR.CMake` only gets C++20, `LIBXR_SYSTEM` and the
  `CONFIGURE_DEPENDS` of `User/*.cpp`; an `XROBOT_MODULES_DIR` that disagrees with the
  project is reported as a warning.

- 自动检测 RTOS：
  Auto-detect the RTOS:

  - 存在 `Core/Inc/FreeRTOSConfig.h` → `LIBXR_SYSTEM=FreeRTOS`
  - 否则存在 `Core/Inc/app_threadx.h` → `LIBXR_SYSTEM=ThreadX`
  - 都没有时设置为 `None`

- 调整 `cmake/starm-clang.cmake`（存在时）：默认运行时配置仍写在 CubeMX 的
  `set(STARM_TOOLCHAIN_CONFIG "...")` 一行，加入的选择块使单个构建目录可用
  `-DSTARM_TOOLCHAIN_CONFIG=<profile>` 选择配置，`libxr stm32 toolchain` 改变的默认值也作用于
  已有构建目录；未知的配置名在 CMake 配置时报错。
  Adjust `cmake/starm-clang.cmake` when it exists: the default runtime profile stays on
  CubeMX's `set(STARM_TOOLCHAIN_CONFIG "...")` line, and the added selection block lets a
  single build directory choose a profile with `-DSTARM_TOOLCHAIN_CONFIG=<profile>` and
  carries a default changed by `libxr stm32 toolchain` into existing build directories; an
  unknown profile name fails at CMake configure time.

- 自动向主 `CMakeLists.txt` 添加以下指令(若尚未包含)：
  Auto-appends the following line to `CMakeLists.txt` if missing:

  ```cmake
  include(${CMAKE_CURRENT_LIST_DIR}/cmake/LibXR.CMake)
  ```

#### 📦 输出内容 (Outputs)

- 生成 `cmake/LibXR.CMake` 文件
  Generates `cmake/LibXR.CMake` file

- 修改主工程的 `CMakeLists.txt`，插入 `include(...)`
  Updates `CMakeLists.txt` to include `LibXR.CMake`

- 已有的构建目录保持不变，CMake 在下次构建时重新配置
  Existing build directories are kept; CMake reconfigures them on the next build

---

### STM32 工程要求  (STM32 Project Requirements)

#### 📁 项目结构要求(Project Structure)

- 必须为 **STM32CubeMX 导出的 CMake 工程**
  Must be a CMake project exported from STM32CubeMX

- 项目应包含以下路径：
  Project should contain the following directories:

  - `xx.ioc`
  - `CMakeLists.txt`
  - `Core/Inc`, `Core/Src`

#### ⚙️ 配置要求(Peripheral & Middleware)

- 所有 **UART / SPI / I2C** 外设必须启用 **DMA**
  All **UART / SPI / I2C** peripherals must have **DMA** enabled

- 如果ADC启用了DMA，请开启连续转换模式
  If ADC has DMA enabled, enable continuous mode

- 推荐启用 **FreeRTOS**，自动生成 `FreeRTOSConfig.h`
  Recommended to enable **FreeRTOS** and generate `FreeRTOSConfig.h`

- 使用 USB 时关闭 `USB_DEVICE` 或 `USBX` 中间件
  With USB, disable the `USB_DEVICE` or `USBX` middleware

#### ⏱️ Timebase 配置建议(Timebase Configuration)

> ✅ 强烈推荐使用 `TIM6`/`TIM7` 等 Timer 作为 Timebase
    Strongly recommended to use `TIM6`/`TIM7` Timers as Timebase
> ✅ 并将该中断优先级设置为 **最高(0)**
    And set the interrupt priority to **highest (0)**

时基仍是 SysTick，或时基定时器中断的抢占优先级不是 0 时，`libxr parse` 会给出警告。
`libxr parse` warns when the timebase is still SysTick, or when the preemption priority of the
timebase timer interrupt is not 0.

---

### `libxr stm32 toolchain`

自动切换 STM32 CMake 工程的工具链及 Clang 标准库配置。
Automatically switches STM32 CMake toolchain and Clang standard library configuration.

```bash
usage: libxr stm32 toolchain [-h] [-d DIRECTORY] [-g | -n | -p] [--verbose] {gcc,clang}
```

#### 🔧 必选参数 (Required)

- `gcc`
  切换为 GCC ARM 工具链
  Switch to GCC ARM toolchain

- `clang`
  切换为 Clang 工具链；不指定标准库时沿用 `cmake/starm-clang.cmake` 中现在的设置
  Switch to Clang toolchain; without a standard library option the current one in
  `cmake/starm-clang.cmake` is kept

#### ⚙️ 可选参数 (Optional)

- `-d, --directory <DIRECTORY>`
  工程根目录，默认为当前目录
  Project root, the current directory by default

标准库（仅用于 `clang`）/ Standard library (`clang` only):

- `-g, --gnu, --hybrid`
  使用 GNU 标准库
  Use GNU standard library

- `-n, --newlib`
  使用 newlib 标准库
  Use newlib standard library

- `-p, --picolibc`
  使用 picolibc 标准库
  Use picolibc standard library

#### 📝 示例 (Examples)

```bash
libxr stm32 toolchain gcc
libxr stm32 toolchain clang -g
libxr stm32 toolchain clang --newlib
libxr stm32 toolchain clang --picolibc
libxr stm32 toolchain clang
```

#### 📦 功能说明 (Functionality)

- 修改任何文件之前先检查 `CMakePresets.json`、其中的 `default` preset 和目标工具链文件
  （以及需要时 `cmake/starm-clang.cmake` 中的 `STARM_TOOLCHAIN_CONFIG` 行）；检查不通过时
  不做任何修改
  Before any file changes, `CMakePresets.json`, its `default` preset and the target
  toolchain file (and, when needed, the `STARM_TOOLCHAIN_CONFIG` line of
  `cmake/starm-clang.cmake`) are checked; a failed check changes nothing

- 自动修改 `CMakePresets.json`，切换默认工具链
  Automatically modify `CMakePresets.json` to switch the default toolchain

- 如使用 Clang，同步修改 `cmake/starm-clang.cmake` 中的默认标准库类型；已有构建目录在下次
  配置/构建时即使用新值。单个构建目录也可用 `-DSTARM_TOOLCHAIN_CONFIG=<profile>` 选择
  （需先运行 `libxr stm32 cmake`）。
  If using Clang, synchronize the default standard library type in
  `cmake/starm-clang.cmake`; existing build directories use the new value on their
  next configure or build. A single build directory can also select a profile with
  `-DSTARM_TOOLCHAIN_CONFIG=<profile>` (after `libxr stm32 cmake` has run).

- 在 gcc 与 clang 之间切换时删除 `build/` 和 `cmake-build*` 目录：CMake 不会更换已有构建
  目录的编译器，只警告 `CMAKE_TOOLCHAIN_FILE` 未被使用。下次构建在新的构建目录中配置。
  Switching between gcc and clang removes the `build/` and `cmake-build*` directories:
  CMake does not change the compiler of an existing build directory and only warns that
  `CMAKE_TOOLCHAIN_FILE` was not used. The next build configures a new build directory.

---

## 🧩 代码生成后操作 (After Code Generation)

生成代码后，CubeMX 生成的代码中需要**手动加入**以下内容，写在 USER CODE 区域中，CubeMX
重新生成时保留：
After generation, the following is **added by hand** to the CubeMX-generated code, inside
USER CODE sections so that CubeMX keeps it on regeneration:

```cpp
#include "app_main.h"
```

并在合适位置调用 `app_main();`：
and `app_main();` is called at the following place:

| 场景 (Scenario)       | 添加位置        |Where to add|
|-----------------------|------------------------------------| -----------|
| 🟢 Bare metal 裸机工程 | `main()` 函数末尾   | End of `main()` |
| 🔵 FreeRTOS / ThreadX 工程 | 线程入口       | Thread entry function |

---

## LibXR / LibXR_CppCodeGenerator / XRobot Relationship

LibXR、LibXR_CppCodeGenerator 与 XRobot 三者形成了一套完整的嵌入式与机器人软件开发体系，分工明确，协同紧密。
LibXR, LibXR_CppCodeGenerator and XRobot together form a complete software ecosystem for embedded and robotics development, with clear separation of concerns and tight integration.

---

### 🧠 LibXR

**LibXR 是跨平台的驱动抽象与工具库**，支持 STM32、Linux 等平台，包含：
LibXR is a cross-platform driver abstraction and utility library supporting STM32, Linux, and more. It provides:

- 通用外设接口封装
  Unified peripheral interface abstraction
- 嵌入式组件（如 Terminal、PowerManager、Database 等）
  Embedded modules like Terminal, PowerManager, Database, etc.
- 裸机、FreeRTOS、ThreadX 与 Linux 支持
  Bare-metal, FreeRTOS, ThreadX and Linux support
- 机器人运动学与导航
  Kinematics and navigation libraries for robotics
- 自动代码生成支持
  Code generation support

#### 🔗 Links

- **Repository**: [libxr](https://github.com/xrobot-org/libxr)
- **API Documentation**: [API](https://xrobot.work/libxr/)
- **Issues**: [Issue Tracker](https://github.com/xrobot-org/libxr/issues)

---

### 🔧 LibXR_CppCodeGenerator

**LibXR_CppCodeGenerator 是用于 LibXR 的代码生成工具链**，当前支持 STM32 + CubeMX，未来将扩展至 Zephyr、ESP-IDF 等平台。
LibXR_CppCodeGenerator is a code generation toolchain for LibXR. It currently supports STM32 with CubeMX, and is planned to support Zephyr, ESP-IDF, and more.

- 由工程文件（目前是 STM32CubeMX 的 `.ioc`）生成 `.yaml` 配置
  Parse project files (STM32CubeMX `.ioc` so far) into a `.yaml` configuration
- 由 `.yaml` 生成 `app_main.cpp`、`flash_map.hpp` 和 CMake 接入
  Generate `app_main.cpp`, `flash_map.hpp` and the CMake integration from the `.yaml`
- 可选生成 XRobot 使用的 `XR_REGISTER` 注册
  Optionally emit the `XR_REGISTER` registrations that XRobot uses
- 重新生成时保留 User Code 区域中的用户代码
  Keep the user code of the User Code regions on regeneration

#### 🔗 Links

- **Repository**: [LibXR_CppCodeGenerator](https://github.com/xrobot-org/LibXR_CppCodeGenerator)
- **Documentation and Releases**: [PyPI](https://pypi.org/project/libxr/)
- **Issues**: [Issue Tracker](https://github.com/xrobot-org/LibXR_CppCodeGenerator/issues)

---

### 🤖 XRobot

XRobot 是配合 LibXR 使用的模块管理工具。它负责拉取模块、把每个模块锁定到具体的提交，再根据 `User/` 下的 YAML 配置生成主函数。
**XRobot is the Module manager for LibXR.**
It fetches Modules, locks each one to a commit, and generates the main function from the YAML configurations under `User/`.

- 拉取模块并把每个模块锁定到具体的提交（`xrobot.lock`）
  Fetch Modules and lock each one to a commit (`xrobot.lock`)
- 检查模块配置，生成 `User/xrobot_main.hpp`
  Check the Module configurations and generate `User/xrobot_main.hpp`
- 不直接访问硬件，使用 BSP 以 `XR_REGISTER` 注册的具名对象
  Does not access hardware directly; uses the named objects the BSP registers with `XR_REGISTER`

---

#### ✅ Recommended For 推荐使用场景

- 工程由多个模块（如传感器、通信、控制器）组成，需要统一管理模块的版本和配置
  For projects made of several Modules (such as sensors, communication, controllers) whose versions and configurations are managed together.

- 应用层与底层驱动解耦：模块配置按生成的对象名选择硬件，便于适配不同的硬件
  For application logic decoupled from the drivers: Module configurations select hardware by the generated object names, which eases moving to different hardware.

#### 🔗 Links

- **Repository**: [XRobot](https://github.com/xrobot-org/XRobot)
- **Documentation**: [GitHub Pages](https://xrobot.work)
- **Releases**: [PyPI](https://pypi.org/project/xrobot)
- **Issues**: [Issue Tracker](https://github.com/xrobot-org/XRobot/issues)

---

## 📄 License

Licensed under **Apache-2.0**. See [LICENSE](LICENSE).


[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2FJiu-xiao%2FLibXR_CppCodeGenerator.svg?type=large)](https://app.fossa.com/projects/git%2Bgithub.com%2FJiu-xiao%2FLibXR_CppCodeGenerator?ref=badge_large)
