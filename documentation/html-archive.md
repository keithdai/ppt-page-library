# HTML 归档与组合

该能力已进入现役代码，但当前 Beta 打包版默认开关不变；开发态和体验入口可显式设置
`PPTLIB_ENABLE_HTML=1`。历史实施过程和分支不是运行时事实，不作为后续维护入口。

## 当前能力

| 入口 | 入库方式 | 后续操作 |
| --- | --- | --- |
| 单文件 HTML | 原位读取、指纹校验；不复制本地源文件 | 搜索、分类、备注、缩略图、高清及动态预览、选片 |
| HTML + 资源目录 | 选择 HTML 入口，递归收集其图片、CSS、字体、音视频、字幕、SVG 依赖 | 同上 |
| HTML ZIP | 唯一 index.html；直接读取 ZIP，不解压来源包 | 同上 |
| 公开在线链接 | 下载静态 HTML 和媒体依赖，保存不可变本地快照与来源记录 | 同上，可断网使用已归档资源 |
| DeckAtlas HTML / ZIP 输出 | 重新读取独立页面，保留页间样式隔离与讲稿 | 可再次入库、组合 |

静态 DOM 适配器支持 render-deck、Reveal 的叶子 section、`.slide`、显式
`section[data-slide]`。普通网页和仅靠脚本生成内容的空壳页面会明确拒绝。
不会执行外站脚本、登录站点、抓取浏览器凭证或加载第三方 iframe。
保留原生视频/GIF、CSS 动效、radio/checkbox + label 的页内切换、details 折叠。
自定义 JS 播放器、Canvas/WebGL 和在线业务交互不属于此轮支持范围。

## 使用

桌面端：导入页选择本地素材，或粘贴在线链接并“添加链接”→导入并渲染。
在选片页搜索、预览、选页、拖拽排序；进入组合后选择：

- **PPTX**：原有可编辑 OOXML 导出；选中 HTML 页会提示改用 HTML 输出。
- **离线 HTML**：自包含单文件，保留 HTML 文字、布局及原生媒体。
- **HTML ZIP 资源包**：解压后打开 index.html；请连同资源目录一起保存或传递。

混合 PPTX/HTML 输出时，PPTX 页使用已有高清预览图，预检会明确提示其文字、动画、
媒体不可原生编辑。缺少高清预览时阻止输出，避免用缩略图充数。
没有直接发布公网链接；导出的文件可以交给已有托管流程发布。

```sh
PPTLIB_ENABLE_HTML=1 pptlib import ./deck.html
PPTLIB_ENABLE_HTML=1 pptlib import ./bundle.zip
PPTLIB_ENABLE_HTML=1 pptlib import-url 'https://example.com/deck#32'
PPTLIB_ENABLE_HTML=1 pptlib compose-preflight selection.json composed.html
PPTLIB_ENABLE_HTML=1 pptlib compose selection.json composed.html --preflight-token TOKEN
```

`deckatlas capabilities` 在 HTML 开关开启时报告 html/zip；版本化 Agent
`compose preflight/run` 同样支持这两种输出。继续要求有效预检 token、
`allow_write` 和 `PPTLIB_OUTPUT_ROOT` 内的输出路径。

## 归档与兼容规则

- URL fragment 只代表打开页，归档整份文稿；相同地址、相同内容复用快照。
- 内容变化生成另一个不可变快照，保留旧页面和已存选片，不覆盖旧归档。
- 本地 HTML/PPTX 原文件继续原位索引。在线资源必须下载，保存在独立库
  `assets/html-archives/<url hash>/<content hash>/` 下。
- archive.json 保存来源、时间、资源清单、内容指纹和下载缺失情况。
  新增 `0012_html_origins.sql` 记录可显示的在线来源；旧 PPTX 表和路径保持兼容。
- 资源 404/下载截断会列为缺失，仍保存可用内容；入口下载失败、非法网络地址、
  大小超限或无法识别页面结构会拒绝导入。用户可据缺失提示重试。
- 下载采用逐跳公有地址校验、连接地址固定、TLS 校验、总时间和资源大小上限；
  不访问本机/局域网地址。停止任务时释放未完成的暂存目录。
- 预览和输出复用同一 HTML 清理规则。来源脚本不执行，原生控件保留；
  各页 iframe 隔离样式，外层只运行应用自己的导航代码。
- 导出预检及写入再次校验源、依赖、选片、目的文件；不得覆盖源文件、源资源、
  数据库、选片输入或库内资产。输出及来源清单一并写入，失败回滚。
- HTML 入口上限 128 MiB，导出总内容上限 256 MiB，最多 2000 页。

## 实际验证

提供的 magic.solutionsuite.cn 链接完整识别 32 页及 32 页讲稿，下载 102 项资源，
82,006,526 字节；生成 32 张缩略图和 32 张高清预览。
原站 `assets/lark-logo.png` 返回 404，已在归档记录和导入结果中明示；
该项不在最终演示页面的媒体依赖中。

按原页 1、6、8、32 组合生成 HTML / ZIP；断网下图片、CSS 标签切换和
22.45 秒视频播放通过。逐页总览检查无空白页，选片界面保留原三栏布局。
自动化覆盖去重、备注、尺寸、SVG/字幕、截断和慢速响应、路径保护、
指纹变化、混合导出、回滚、重新入库以及原有 PPTX 流程。
