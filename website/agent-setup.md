# 给 Agent：接入 DeckAtlas 本地页库

用户把本说明交给你，是希望你完成一次安装或连接，之后用自然语言调用他们已有的演示页库。你需要具备本机命令执行、文件读取和用户授予的访问权限。本说明不会在网页中执行，也不会替你的宿主申请权限。

## 一次接入

1. 确认你的执行环境能访问用户的 Mac 和本地文件；仅有远程聊天或云端沙箱时，不声称已接入本机页库。
2. 优先使用用户已有的 `deckatlas` 或源码虚拟环境。新安装可以读取本仓库的 `v0.3.0-beta.2` Release，获取完整 `deckatlas-0.3.0-beta.2-macos-arm64.zip` 和校验值。确认资源实际可下载后再安装，不使用猜测的下载链接。包仅适用于 Apple Silicon Mac，未完成 Developer ID 与公证。
3. 使用 Release 包时保留完整 `deckatlas/` 与 `_internal` 目录，按用户选择的位置解压并核对 SHA-256；无需安装 Python / Node。若没有适用包或用户选择源码，在允许的位置获取 `https://github.com/keithdai/ppt-page-library.git`，检查 Git、Python 3.11，再执行 `make install`。缺少依赖、macOS 拦截或权限时说明具体情况，不关闭系统安全保护或改动用户已有仓库。
4. 确定现有资料库的 `PPTLIB_HOME`。优先使用用户提供的位置或已经授权的桌面配置，不把新建的空库当成原资料库。无法确定时只询问资料库位置。未使用过 DeckAtlas 的用户，可在明确选择目录后，通过桌面端导入初始稿件。
5. 确定用户允许导出的 `PPTLIB_OUTPUT_ROOT`，与资料库配置分别记录。在后续命令中保持环境一致；不要依赖临时终端的工作目录。
6. 调用 `deckatlas capabilities`，或使用已安装源码环境中该可执行文件的绝对路径。检查响应成功、`library_available`、`commands`、`export_formats` 和输出目录。没有资料库时，不为了让能力检查成功擅自初始化或迁移数据。
7. 接入完成后说明使用的资料库、可用任务和输出位置。若宿主支持经用户允许的持久配置，将可执行路径与目录配置存入宿主指定的位置；否则告知新会话需要再次提供这份配置。安装 CLI 本身不等于已给所有 Agent 注册工具。

## 用户要求“安装并打开”时

按用户已经授权的安装请求完成桌面端和 CLI 两个入口：

- Apple Silicon Mac 可以从同一 Release 获取 `DeckAtlas-0.3.0-beta.2-mac-arm64.zip`，解压后安装应用，并连接独立 CLI。包已经包含 Electron 和 Python；LibreOffice / Chrome 仍为外部工具。只有实际下载、启动成功后才报告安装完成；不能绕过 macOS 拦截。
- 使用包时按发布说明连接桌面实际资料库，并说明 HTML 默认关闭。下面的 Python、Node 和 npm 步骤只适用于源码安装。
- 除 Python 3.11、Git 外，检查 Node.js 20 和 PPTX 预览所需的 LibreOffice。HTML 预览还需要本地 Chrome。缺少系统依赖或宿主权限时说明具体阻碍；不要把“源码已下载”当成“应用已可用”。
- 在已检查的源码仓库中完成 `make install`，再执行 `npm --prefix desktop install` 安装桌面依赖。复用既有安装，不覆盖用户尚未提交的改动。
- 使用前面确定的 `PPTLIB_HOME`、`PPTLIB_OUTPUT_ROOT`。用户第一次使用且已选择位置时，可以采用仓库的 `var/dev` 作为体验库；已有用户不能未经确认切到空库。
- 通过 `npm --prefix desktop start -- --password-store=basic --use-mock-keychain` 启动桌面端。任何自动化验证都要另外使用隔离的 `PPTLIB_ELECTRON_USER_DATA`，按仓库要求完成后关闭；用户实际打开的桌面会话使用其选定配置。
- 检查启动是否成功、依赖是否可用，再用同一库运行 Agent `capabilities`。首次打开尚无已索引内容时，解释需要导入，不能声称已能搜索已有材料。
- 告诉用户下一步：在桌面导入一份熟悉的 PPTX，或在已启用 HTML 的源码环境里导入可识别的 HTML 汇报。用户授权本地文件入库且当前 CLI 提供 `import` 时，也可以由你代为导入。完成后可以自然语言交代检索与组稿任务。

## 日常任务由你编排

用户可以直接说：“从旧稿里找供应链案例，按背景、方案和成果推荐页面”，或“按我确认的顺序导出新 PPT”。不用要求用户手写命令、页面标识、JSON 清单或 token。

- 将用户意图转成具体关键词与分类过滤；`deckatlas search` 提供确定性文本检索，不是内置语言模型，也不保证能识别图片语义。必要时换关键词并读取候选页面。
- 使用 `deckatlas slide get` 读取搜索结果中的真实 `slide_id`。正文、备注、已有预览路径、`deck_name` 和页码可辅助核对内容。不要编造命中页面、案例结果或不可用预览。
- 把用户需要的页面按顺序写为选片清单：`schema_version` 使用 `deckatlas.selection/1.0`，`slide_ids` 只使用真实返回值。在用户允许的工作位置保存清单。
- 推荐素材时给出来源、页码和排序理由。用户只是要求推荐，就停在推荐；用户已明确要求导出，则按既有授权完成可执行的步骤，不重复询问已经确定的选择。
- 导出先调用 `compose preflight`，检查 `ready`、阻断项、兼容性提示和输出路径。只有检查成功且用户授权覆盖该次写入时，才用返回的原始 `preflight_token` 和 `--allow-write` 调用 `compose run`。
- 清单、源文件或输出变化时重新预检。默认不覆盖已有文件，只有用户明确授权覆盖时才加 `--overwrite`。所有导出及伴随文件必须在 `PPTLIB_OUTPUT_ROOT` 内。
- 返回实际输出位置、页数和重要兼容性提示；PPTX 组稿后建议用户在 PowerPoint 核对并继续编辑。

## 新稿沉淀与格式

- 先查看 `capabilities.commands`，确认 `import` 可用。`v0.3.0-beta.2` 提供此命令，旧版可能没有。
- 只有用户明确授权加入页库时，才调用 `deckatlas import <明确的本地文件路径> --allow-write`。接受 PPTX、HTML、HTM 和 ZIP；不接受目录、在线 URL 或符号链接。
- 导入沿用原文件位置，不能删除或移动源稿；HTML 的本地依赖也应保留。索引与预览可能分别成功或失败，检查 `searchable` 和 `preview_ready`，不把部分成功描述成完整导入。
- PPTX 预览需要 LibreOffice；HTML 预览需要本地 Chrome。首次索引和渲染会花时间并生成缓存。
- 使用已公布的 `export_formats`。HTML / ZIP 输出需要 `PPTLIB_ENABLE_HTML=1`；混合选片中的 PPTX 页在 HTML / ZIP 中以高清图片呈现。没有支持时不承诺输出。
- URL 归档、删除、维护和同步不属于 Agent 公共 CLI 范围，不通过私有 `pptlib` 命令绕开接口。

## 边界与排错

搜索与读页默认只读，不初始化或迁移资料库。源 PPTX / HTML 不得修改、移动或删除。任何错误先读取结构化响应中的错误码、可重试标记和详情；忙碌时合理重试，拒绝项不绕过。

本地页库不自动授权将材料上传外部服务。外部 Agent 或模型是否接收读取的内容，取决于用户选择的工具及权限；遵守用户关于资料、截图和远程处理的限制。

完整协议与退出码：
https://github.com/keithdai/ppt-page-library/blob/main/documentation/agent-cli.md
