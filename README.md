# zterm

zterm 项目的应用显示名为 **zTerm**，是基于 Python、GTK 3 和 VTE 的独立终端应用，支持本地终端与按服务器分组的 SSH 会话。

[下载 deb 安装包](https://github.com/zhou411X/zterm/releases/latest) · [更新记录](CHANGELOG.md) · [GPL-3.0 许可证](LICENSE)

- 添加服务器时填写显示别名、IP、用户名、密码和端口（默认 22）；侧栏和窗口标题显示别名。密码存入系统钥匙环，留空使用密钥登录。
- 左侧栏按钮或右键服务器 →“编辑服务器”可修改别名、IP、用户名、密码和端口；密码留空保留原密码，也可清除已保存密码。连接信息用于新会话或重新连接，现有连接继续运行。
- “复制服务器”按同一 IP/用户名/端口和已存密码新建副本（密码自动复制，无需重输），副本别名自动加“ 副本”后缀。
- 每个服务器拥有独立会话标签，切换服务器保留连接；支持重连及关闭确认。
- 左侧服务器支持拖拽排序，插入位置以指示线显示，顺序自动保存并在重启后保留；本地终端固定在顶部。
- 会话标签显示 `[序号] 活动内容`；识别到 Codex 标题时额外显示 `[CODEX]`。每个标签固定宽度 240（原宽度的 1.5 倍），长标题末尾省略、悬停显示完整内容；关闭按钮在右侧，“+”紧随最后一个标签。
- 标签编号始终按从左到右的位置更新，拖拽或关闭后连续排列；`Alt+1 … 9` 对应显示编号。
- 窗口标题随当前服务器切换；统一底栏左侧显示服务器与当前会话状态，离线后可直接重连，中间显示 Codex 订阅额度。
- 设置字体、字号、文字、背景及光标颜色，预览后保存并应用到所有会话。
- 主窗口和外观设置窗口在 X11 下请求初始居中，Wayland 下由桌面决定位置；外观设置为独立窗口，可单独移动和缩放，重复打开保留当前预览。
- 左右侧栏顶部各有一个图标，点击展开或收起；收起后保留窄图标栏。文件栏每次启动默认收起，拖动分隔线调宽，重新展开保留本次窗口宽度。
- 外观设置支持背景不透明度、背景图片、图片强度及图片毛玻璃；文字保持清晰，可选择是否把图片同时应用到终端。
- SFTP 文件面板支持浏览、文件/目录上传下载、拖入上传、新建目录、重命名和删除文件/空目录；后台队列支持进度、取消、重试及同名处理。
- 应用界面主题覆盖标题栏、侧栏、标签、底栏和菜单；可跟随终端配色、独立搭配 30 套明暗配色，或跟随系统外观，支持预览、取消恢复及保存。
- Solarized、Gruvbox、PaperColor、Peaksea 的 Light / Dark 配色及 ANSI 16 色调色板。
- Gruvbox、PaperColor、Peaksea 的普通/亮 ANSI 色采用本地 Vim 主题的主要语法高亮 RGB；具体着色位置仍由命令程序决定。
- 支持 Monokai、Ayu Light / Dark、AyuMirage、Breadog、ClawBeige Light / Dark、DragonBeige、FaultySchematics、NightOwl Light / Dark、RetroTheme、TokyoNight Light / Dark / Storm 和 VintageLight；终端与界面均可选择。
- PaperColor 额外适配截图中默认 Vim 的注释/语句色槽及 256 色索引 81、121；这是调色板近似，不替代 Vim 的语法主题规则。
- 六套 GNOME Terminal / Ubuntu 配色：浅黄背景黑字、白底黑字、黑底灰字、黑底白字、黑底绿字、Ubuntu 默认；连同上述 Vim 配色共 30 套主题。
- 圆角羽毛应用图标和独立桌面启动入口；配置在重启后保留，不自动重连服务器。

## 安装已发布的 deb 包（推荐）

适用于提供 GTK 3 / VTE 的 Linux 桌面。已在 Ubuntu 24.04、Python 3.12、VTE 0.76 上验证。
需要图形桌面环境；其他 Debian / Ubuntu 版本尚未逐一验证。

从 [Releases](https://github.com/zhou411X/zterm/releases) 下载
`zterm_0.2.10_all.deb`，进入下载目录后安装：

```sh
sudo apt install ./zterm_0.2.10_all.deb
/usr/bin/zterm
```

也支持 `dpkg -i`；如果提示缺少依赖，随后用 apt 补齐：

```sh
sudo dpkg -i ./zterm_0.2.10_all.deb
# 仅当上一步报告缺少依赖时执行：
sudo apt --fix-broken install
```

也可在应用菜单搜索“zTerm”。升级使用同样的安装命令，
之后重新打开应用；已有窗口不会自动加载新版。Release 同时提供 `SHA256SUMS`，
与 deb 放在同一目录后可运行 `sha256sum -c SHA256SUMS` 校验下载内容。

## 从源码运行与用户级安装

这是 Python 程序，不需要 C/C++ 编译器、CMake 或 Node.js。先获取源码并安装运行依赖：

```sh
sudo apt update
sudo apt install git python3 python3-gi python3-gi-cairo python3-cairo python3-pil python3-paramiko gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-secret-1 gnome-keyring librsvg2-common openssh-client
git clone https://github.com/zhou411X/zterm.git
cd zterm
/usr/bin/python3 app.py
```

程序要求 Python 3.9+。使用系统 `/usr/bin/python3`，以便找到 apt 安装的 GTK/VTE 绑定。
zTerm 使用 GTK 默认的显示后端，在 Wayland 桌面上使用原生 Wayland，避免强制 XWayland 在分数缩放下使文字发虚。
Wayland 下主窗口和外观窗口的位置由桌面决定；X11 下请求初始居中，之后可自由拖动。不修改系统全局设置。
如需手动使用 XWayland，可用 `GDK_BACKEND=x11 /usr/bin/python3 app.py` 启动，但分数缩放下文字可能变模糊。
VTE 0.76 及以上将完整字符行贴底显示，不足一行的高度余量留在顶部，减少最大化时终端底部的空白；
字号、行数和状态栏保持不变。更早的 VTE 沿用其默认对齐方式。
如需安装到当前用户（这一步无需 sudo）：

```sh
sh install.sh
~/.local/bin/zterm
```

用户级安装文件位于 `${XDG_DATA_HOME:-~/.local/share}/ssh-workbench/`，
设置位于 `${XDG_CONFIG_HOME:-~/.config}/ssh-workbench/`。服务器别名和外观分别保存在
`servers.json` 和 `appearance.json`。左侧服务器栏底部依次排列“添加服务器”“编辑服务器”“复制服务器”“移除服务器”。
点击“添加服务器”可指定 IPv4/IPv6 地址、用户名和端口，
显示别名留空时使用 IP；这些连接字段优先于 `~/.ssh/config`。密钥和跳板等其他设置仍由 OpenSSH 管理。
“添加服务器”使用独立窗口，可单独移动、缩放，填写期间主终端仍可操作；重复点击会唤回已有窗口。
若 `.bashrc` 的静态 SSH 别名中只有一个登录用户名，表单自动预填该用户，可自行修改。
已有版本保存的服务器继续保留：按别名或 IP 匹配 `.bashrc` 的直接 SSH 命令，
优先采用其中明确指定的用户和连接选项；不执行 `.bashrc` 或复杂 shell 命令。

填写的密码通过 libsecret 保存在系统钥匙环，配置文件、SSH 参数和环境变量中均不包含密码。
保存密码需要桌面 Secret Service（如 GNOME Keyring）；钥匙环不可用时会提示保存失败。
SSH 仍先尝试密钥，再通过 askpass 读取保存的密码；首次连接需要确认服务器主机密钥。
侧栏底部或右键服务器 →“移除服务器”可删除对应连接配置及钥匙环条目，不修改 `.bashrc` 或 `.ssh/config`。
右键移除作用于所点击的服务器；有活动会话时先确认，有未完成文件任务时禁止移除，其他服务器会话保持运行。

从 SSH 工作台升级：应用现名 zTerm，启动命令为 `zterm`；旧命令 `ssh-workbench`
仍可使用。沿用原来的数据目录、配置目录和桌面入口标识，服务器列表和外观设置无需迁移。
deb 包名从 `ssh-workbench` 改为 `zterm`，安装新版时替换旧包。

## 构建 deb 安装包

在源码目录执行以下命令。打包使用 `dpkg-deb`，构建过程无需 root：

```sh
sudo apt install dpkg
# Python 语法编译检查；不会生成本机架构专用的可执行程序。
/usr/bin/python3 -m py_compile app.py model.py credentials.py quota.py file_browser.py transfer.py diagnostics.py background.py
sh build_deb.sh
sudo apt install ./dist/zterm_0.2.10_all.deb
```

默认版本取自源码中的 `VERSION` 文件，也可通过 `sh build_deb.sh 0.2.10` 显式指定版本。
“设置 → 关于 zTerm”显示当前运行版本；deb 中的显示版本与打包时指定的版本一致。安装包为 `Architecture: all`，
包含 Python 源码、桌面入口和图标，运行依赖由 apt 安装。
系统版启动命令为 `/usr/bin/zterm`，程序放在 `/usr/share/ssh-workbench/`，
文档放在 `/usr/share/doc/zterm/`，继续使用原有用户配置。

如果此前运行过 `install.sh`，用户级启动器和桌面入口仍优先于系统版；
可先通过 `/usr/bin/zterm` 明确启动 deb 版。安装包不会删除用户级安装。
卸载系统版使用 `sudo apt remove zterm`，用户配置保留。
用户级安装可删除对应的 `~/.local/bin/zterm` 和 `~/.local/bin/ssh-workbench`、上述应用数据目录，
以及用户数据目录下的 `applications/local.ssh.Workbench.desktop` 和
`icons/hicolor/scalable/apps/local.ssh.Workbench.svg`；保留配置目录即可保留设置。

## 会话活动标题

活动内容采用 shell 或程序上报的终端标题；没有标题时显示启动程序名（如 `bash`、`ssh`）。
标签编号继续按位置更新，并对应 `Alt+1 … 9`；窗口标题仍显示服务器别名或 IP。
活动标题识别到独立的 `Codex` 字样时显示 `[CODEX]`（不区分大小写，目录 `/codex` 和文件 `codex.py` 不算）。
此标记依据标题，不扫描远端进程；程序没有上报标题或 shell 没有恢复标题时，活动信息可能缺失或过时。
会话结束时移除标记并显示结束状态，重连清空旧活动标题。

## 背景与毛玻璃

“设置 → 外观设置 → 背景效果”中可选 PNG、JPEG、WebP 背景图片，调整图片强度和毛玻璃模糊程度。
“背景不透明度”范围为 15%–100%，100% 不透明；只影响背景，文字和按钮保持清晰。
图片在工作区居中裁剪填充；勾选“背景图片同时应用到终端”后才用于终端内容区，
标题栏使用主题色透明背景。未勾选时终端保持自己的背景色，仍可调整背景透明度。
毛玻璃只模糊所选图片，不会实时模糊窗口后方的桌面；桌面透视需要窗口合成器支持。

附带浅色 `zterm-mist-light.png` 和深色 `zterm-mist-dark.png` 两张背景图，不会自动替换现有背景。
deb 安装后位于 `/usr/share/ssh-workbench/backgrounds/`，用户级安装位于
`${XDG_DATA_HOME:-~/.local/share}/ssh-workbench/backgrounds/`，源码中位于 `backgrounds/`。
在背景效果页选择相应图片即可使用；目录内的 `generation-prompts.json` 记录 AI 生成提示词。

图片在后台读取和模糊，最大接受 4000 万像素，绘制缓存最长边为 2560 像素；动画图片只读取首帧。
读取期间仍可操作终端，处理完成前不能保存。图片不可读时显示错误，可重新选择或清除；
重启时图片丢失则退回主题色。保存的是图片绝对路径，请保留原文件；清除背景不会删除文件。
设置会即时预览；取消或关闭外观窗口恢复保存值。字体和语法颜色仍在下方终端预览，保存后应用到会话。

## SFTP 文件传输

文件栏固定在右侧，每次启动只显示顶部文件夹图标；选择远程服务器后点击该图标展开，再次点击收起。
拖动分隔线调整宽度，收起再打开保留本次窗口的宽度；调整主窗口大小时优先保持侧栏宽度。
也可右键服务器 →“浏览服务器文件”，只浏览文件而不新建终端。
首次进入远端登录目录，可输入远端绝对路径、双击目录或点击“↑”导航。
每个服务器在当前窗口内保留浏览目录；不自动跟随终端中的 `cd`。

点击“上传”后，双击单个本地文件即可上传；也可多选后点击“上传”、选择整个目录，或把本地文件拖入文件面板。
文件选择和命名窗口采用非模态异步处理，打开期间主终端可继续操作；同一目标重复点击会唤回已有窗口，
不同服务器或任务使用各自的窗口，并显示绑定的服务器和路径。
确认/取消按钮保留在窗口底部，兼容 GNOME 的标题栏按钮设置。
下载先选择远端条目，再选择本地目标目录。
队列位于右侧“文件 · SFTP”面板底部，不占终端高度；串行执行，显示进度和平均速度，支持取消与重试。
收起面板会同时隐藏队列，传输继续进行；切换服务器或关闭终端标签也不取消传输。
同名文件在复制前等待选择“覆盖”“另存”“跳过”；目录覆盖会替换该任务内的同名普通文件。
传输目标固定为创建任务时的服务器和路径，后续切换页面不会改变它。
关闭应用会提示取消未完成任务；任务历史不跨重启保存。有未完成任务时需先处理再移除服务器。

连接复用 OpenSSH 的 SSH 配置、密钥、agent、跳板机和 zTerm 钥匙环认证，通过独立 SFTP 子进程工作。
未知主机需确认，主机密钥不匹配时拒绝连接。服务器必须启用 SFTP 子系统。
每次目录读取/任务建立独立连接；无密钥、agent 或已保存密码时，可能重复提示登录。
文件先写同目录 `.zterm-*.part`，成功后再完成替换；远端覆盖要求支持 OpenSSH `posix-rename` 扩展。
取消或断网可能留下远端临时文件，任务悬停详情显示路径；重试会重新传输。
首版不支持续传/目录同步、不跟随符号链接，仅删除文件或空目录，不递归删除非空目录。

## 诊断日志

启动时自动写入 `${XDG_STATE_HOME:-~/.local/state}/zterm/zterm.log`；
“设置 → 诊断日志”显示当前日志路径和进程 PID。默认 INFO，需详细连接阶段记录时：

```sh
ZTERM_LOG_LEVEL=DEBUG zterm
```

日志记录对话框打开/确认/关闭、文件任务编号/状态/字节数、连接进程和异常栈位置，
不记录密码、完整 SSH 参数/环境、终端正文或传输文件内容。目录权限为 0700、文件为 0600。
GTK/VTE 警告仅记录来源、级别和调用位置，不保存可能包含用户数据的原始警告文本。
主日志轮转，最多 4 份、每份约 2 MiB。主线程超过 10 秒没有心跳时自动记录线程栈，
恢复后记录恢复事件；正常运行的模态事件循环可能仍有心跳，可通过对话框日志判断。
Python 致命错误和手动线程栈写入同目录 `crash.log`；仅对运行新版且已启用日志的
进程执行 `kill -USR1 <设置中显示的PID>` 可采集线程栈。不要对旧版进程发送此信号。
`crash.log` 超过 2 MiB 时在下次启动截断；单次运行中反复手动转储仍可能增大。
报告问题时提供日志、发生时间及操作步骤即可；退出并重开应用后才加载本次新增的日志功能。

## Codex 订阅额度

底栏对应当前选中机器上的 CLI 账号，后台每 5 分钟查询，也可手动刷新。
远端需要 Python 3.9+，可使用密钥、SSH agent 或已存入解锁钥匙环的密码，不会向终端注入命令。
先在终端会话中确认主机密钥；后台不会弹出主机确认、钥匙环解锁或密码输入窗口。
Codex 使用已登录 CLI 的官方 [account/rateLimits/read](https://learn.chatgpt.com/docs/app-server) 接口。
不读取或传输 Codex 登录凭据，不修改远端 CLI 配置。悬停可查看数据时间；
暂无数据或已过重置点时不会显示虚假的 0%。显示范围为 Codex 主/次额度，
模型专属额度及额外付费用量不在此范围。连接要求详见 [HELP.txt](HELP.txt)。

## 验证

测试需要 `xvfb` 和 `xauth`，使用临时配置与本地模拟进程，不连接真实 SSH 服务器。

```sh
sudo apt install xvfb xauth openssh-sftp-server
/usr/bin/python3 -m py_compile app.py model.py credentials.py quota.py file_browser.py transfer.py diagnostics.py background.py test_workbench.py test_quota.py test_credentials.py test_transfer.py test_file_browser.py test_diagnostics.py test_background.py
/usr/bin/python3 test_credentials.py
/usr/bin/python3 test_quota.py
/usr/bin/python3 test_transfer.py
/usr/bin/python3 test_diagnostics.py
GDK_BACKEND=x11 xvfb-run -a /usr/bin/python3 test_workbench.py
GDK_BACKEND=x11 xvfb-run -a /usr/bin/python3 test_file_browser.py
GDK_BACKEND=x11 xvfb-run -a /usr/bin/python3 test_background.py
```

测试涵盖独立服务器表单、右键编辑、侧栏排序持久化、旧配置兼容、密码保留/替换/清除及失败回滚、
异步保存期间的关闭/删除/重名处理、旧账号额度结果丢弃、askpass 目标匹配、SSH 用户/IP/端口覆盖、
配置保存、PTY 输入输出、会话切换和回收、外观保存恢复，
以及 30 套主题的实际 ANSI/前景/背景色块渲染、旧主题配置迁移、界面主题预览/保存/窗口隔离、标签布局/排序/溢出滚动、重排编号与 Alt 快捷键对应和统一底栏状态/重连。
“在线”依据当前会话进程状态；真实 SSH 断网场景尚未验证。
截图是测试生成物，不纳入版本控制。
SFTP 测试使用临时目录和本机 SFTP 子系统；安装 openssh-server 后还会验证仅监听回环地址的
临时 SSH 服务、公钥登录和主机密钥不匹配拒绝。测试不连接用户实际服务器。

详细用法和快捷键见 [HELP.txt](HELP.txt)，主题来源与适配约定见
[THEME_SOURCES.txt](THEME_SOURCES.txt)。终端配色改变默认色及 ANSI 0–15；PaperColor 另适配索引 81、121，
不覆盖程序自行指定的 truecolor 颜色。

## 开源许可

Copyright (c) 2026 zhou411X。项目以 **GPL-3.0-only** 发布，完整条款见 [LICENSE](LICENSE)。
配色适配保留上游作者和许可声明，见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。
安装包包含程序源码和许可文本；每个 Release 的对应源码可通过同名 Git 标签获取。
