# PDF 日期修改工具

批量改写 PDF 里的日期，保持每个日期的原有格式（含字体、字号、颜色），结果另存到新目录，**绝不改动原文件**。

当前版本 **v1.1.0**，内置自动更新（OTA）。

支持三类日期所在位置：

| 位置 | 典型场景 | 处理方式 |
| --- | --- | --- |
| **正文文字** | 数字版 PDF：发票、报表、合同 | 定位日期字符 → 擦除 → 按原字体/字号/颜色原位重绘 |
| **表单域** | 可填写的表单框（AcroForm 文本域） | 直接改写字段值 |
| **注释** | 批注、FreeText 文本框、便签 | 直接改写批注内容 |

三者可以任意组合勾选。表单域和注释是 PDF 里的矢量对象，所以即使是**扫描件**，只要日期填在框里或写在批注里，也能精确改写，**不需要 OCR**。

## 不支持的场景

| 场景 | 说明 |
| --- | --- |
| 日期是扫描图像上的像素 | 纯图片、无可选文字。需要 OCR + 图像擦除方案，本工具不做 |
| PDF 元数据里的创建/修改时间 | 不影响页面显示，本工具不改 |
| 数字签名 / 时间戳 | 改动会导致签名失效 |

> 拿不准日期藏在哪？用 `tools/inspect_pdf.py` 一探便知（见下文「诊断」）。

## 为什么不用「转 Word 再转回 PDF」

把 PDF 转成 Word 再转回去，会重排版式、替换字体、丢失表格线与页眉页脚——对发票、合同这类版式敏感的文件是灾难性的。本工具直接在 PDF 内部做**字符级替换**，其余内容一个字节都不动。

## 下载与安装

**方式一：直接下载 exe（推荐）**

到 [Releases](https://github.com/1048258747/pdf_change_date/releases) 下载 `PDFDateTool.exe`，双击即可运行，无需安装 Python。程序自带自动更新，之后有新版本会自己提示升级。

**方式二：从源码运行**

克隆仓库后双击 **`run.bat`**，首次运行会自动创建虚拟环境并安装依赖 PyMuPDF，之后直接启动界面。

如果双击后窗口没有出现，改用 **`run_console.bat`**，它带控制台，能看到报错信息。

## 自动更新（OTA）

程序启动约 1.5 秒后会**静默检查**一次 GitHub Release；底部也有「检查更新」按钮可手动触发。

- 发现新版本时，会显示版本号、更新包大小并询问是否升级；
- 确认后自动下载 `PDFDateTool.exe`，退出程序，替换自身并重新启动；
- 以**源码方式**运行时无法自动替换，会退化为「打开下载页面」。

更新包来自本仓库的 Release 资产（`PDFDateTool.exe`），版本号取自 `pdfdatetool/__init__.py` 的 `__version__`，并与 Release 的 tag 比对。

## 使用步骤

1. **添加文件** —— 点「添加文件」选单个/多个 PDF，或点「添加文件夹」一次性收集整个目录（含子目录，输出时保留原有目录结构）。
2. **勾选处理范围** —— 默认「正文文字 / 表单域 / 注释」全选。只改某一层时取消其余勾选，可避免误伤。
3. **扫描日期** —— 点「扫描日期」，列表显示每个文件识别到几个日期。**双击某一行**可查看明细：日期在哪个对象、第几页、原内容是什么。
4. **设定目标日期** —— 用年/月/日三个输入框设定，或用「今天」「+1 年」「+1 月」「+1 天」快捷按钮。
5. **选择输出目录** —— 默认是项目下的 `output/`，原文件不受影响。
6. **开始替换** —— 执行完成后可点「打开」直接查看结果。

默认勾选「仅处理扫描到日期的文件」，避免把没有日期的文件无意义地复制一份。

## 支持的日期格式

替换时会**自动沿用原文格式**，不会把「2024年5月1日」改成「2025-06-02」：

| 原文 | 目标日期 2025-06-02 替换后 |
| --- | --- |
| `2024年5月1日` | `2025年6月2日` |
| `2024 年 05 月 01 日` | `2025 年 06 月 02 日` |
| `2024/5/1` | `2025/6/2` |
| `2024-05-01` | `2025-06-02` |
| `2024.05.01` | `2025.06.02` |
| `05/01/2024`（月/日/年） | `06/02/2025` |
| `20240501` | `20250602` |
| `签发日期：2024年5月1日（加盖公章）` | `签发日期：2025年6月2日（加盖公章）` |
| `打印日期 2024-05-01 10:32:00` | `打印日期 2025-06-02 10:32:00` |

同一段文字里有多个日期会全部替换；非日期的数字（如编号 `EQ-2021-0087`、版本号 `v2.4.1`、金额 `1,200.00`）不会被误改。

## 字体还原

重绘正文文字时，按以下优先级还原字体：

1. **复用 PDF 内嵌的字体** —— 保真度最高（仅当内嵌字体含有要绘制的字形时才用）；
2. **调用系统中文字体** —— 按原字体名匹配系统里的同名字体（如 `SimSun` → simsun、`SimHei`/`Heiti` → simhei），这类字体同时含全角汉字与半角数字，重绘出来的日期与原样几乎一致；
3. 找不到时退回内置基础字体（Helvetica / Times / Courier / 中文黑体）。

日期整串一次性重绘，所以复制、搜索时仍是连续的字符串。实际效果：蓝色开票日期仍是蓝色，灰色打印时间仍是灰色，Times 衬线字体仍是衬线。

## 已知限制

- **复制/搜索的文字顺序**：正文文字是「擦除旧字符 + 写入新字符」实现的，新文字在 PDF 内容流中排在页面末尾。**显示完全正常，日期整串连续、可被搜索**；但用阅读器全选复制时，被改写的日期可能排在文本末尾而不是原位置。
- **日期长度变化**：若新日期的字符数明显多于原日期（如 `2024/5/1` → `2025/12/2`），重绘后可能与其右侧内容轻微重叠。保持原格式的规则下这种情况很少见。
- **系统字体依赖**：第 2 级依赖系统里装有对应中文字体（Windows 自带 SimSun/SimHei 等）。找不到时会退回内置字体，日期依然正确，只是字形略有差异。

## 诊断

当主程序提示「未发现日期」时，用它查看 PDF 的真实结构：

```
.venv\Scripts\python.exe tools\inspect_pdf.py "你的文件.pdf"
```

会列出元数据、每一页的表单域、注释、页面文字层中出现的日期，并标明日期到底在哪一层。

## 目录结构

```
PDF修改/
├── run.bat                 双击启动（无控制台）
├── run_console.bat         双击启动（带控制台，便于排错）
├── build.bat               一键打包 exe
├── build_exe.py            PyInstaller 打包脚本
├── main.py                 入口
├── requirements.txt        运行依赖
├── requirements-dev.txt    打包依赖（pyinstaller / pillow）
├── LICENSE
├── assets/
│   ├── app.ico             应用图标（exe 图标来源）
│   └── icon_source.jpg     图标设计稿
├── pdfdatetool/
│   ├── dates.py            日期识别与「保持原格式」渲染
│   ├── core.py             正文文字 / 表单域 / 注释 的读取与改写
│   ├── batch.py            批量收集、输出路径、汇总报告
│   ├── ota.py              自动更新：版本比对、查 Release、下载、就地替换
│   └── gui.py              tkinter 界面
├── tools/
│   ├── make_samples.py     生成样例 PDF（扫描件表单 + 数字版发票）
│   ├── inspect_pdf.py      诊断某个 PDF 里日期藏在哪
│   ├── selftest.py         端到端自测
│   ├── gui_smoke.py        界面冒烟测试
│   ├── make_icon.py        设计稿 → 多尺寸 ico
│   └── frozen_check.py     验证打包后的 exe 能真正读写 PDF
├── samples/                样例 PDF
├── dist/                   打包产物（PDFDateTool.exe）
└── output/                 默认输出目录（运行后自动创建）
```

## 开发者：自测

```
.venv\Scripts\python.exe tools\selftest.py     # 日期引擎 + 三层改写 + OTA 版本判定
.venv\Scripts\python.exe tools\gui_smoke.py    # 界面构建与交互
```

`selftest.py` 会生成样例、扫描、替换、按页面实际文字回读校验，并在 `samples/_out/` 输出改动前后的渲染图供目视比对。

## 开发者：打包 exe

双击 **`build.bat`** 即可（自动装依赖 → 打包 → 输出 `dist\PDFDateTool.exe`）。等价命令：

```
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe build_exe.py
```

版本号只有一处来源——`pdfdatetool/__init__.py` 的 `__version__`，打包时会同步写进 exe 的文件属性，保证「属性里看到的版本」和「OTA 比对用的版本」一致。

打包是 `--windowed` 的，没有控制台，出问题看不到报错。用下面这条验证打包是否完整（自造 PDF → 扫描 → 替换 → 回读）：

```
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --console ^
    --name FrozenCheck --collect-all pymupdf --paths . ^
    --distpath build\frozen --workpath build\frozenwork ^
    --specpath build\frozenwork tools\frozen_check.py
build\frozen\FrozenCheck.exe build\frozen_report.txt
```

## 发布新版本

1. 改 `pdfdatetool/__init__.py` 里的 `__version__`；
2. 跑一遍自测，再 `build.bat` 打包；
3. 提交并打 tag 推送：`git tag v1.1.0 && git push origin main --tags`；
4. 在 GitHub 上建同名 Release，**把 `dist/PDFDateTool.exe` 作为资产上传**——OTA 就是从这里取更新包的。

## 环境要求

- Windows，Python 3.10+（已在 Python 3.12 上验证）
- 运行依赖：`pymupdf`（仅此一项），界面用 Python 自带的 tkinter
- 打包依赖：`pyinstaller`、`pillow`（见 `requirements-dev.txt`）