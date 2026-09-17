# 2.3.1：报告表达与输入可靠性

2.3.1 是基于公开 2.3 源码的维护更新，改进辅助报告的表达和 Skill 配套工具的输入异常处理。这组成果来自 3.0 开发过程，不代表完整 3.0 已完成或取得用户接受。

## 普通使用者会遇到什么变化

主管的自然语言任务仍通过现有 Skill 显式启动，操作见[入门指南](getting-started.md)。本版本的 11 个 Skill 安装文件与公开 2.3 一致，已经使用同一版本的人无需为了这批工具变化重装 Skill。

辅助报告在有完整输入时，把当前结论、已有方案的选择理由和下一步放在一起解释，另行说明用户当前需要做什么。方案选择理由不会被当作任务完成的证据；警告和未知仍直接可见，完整依据放在普通链接打开的详情页。报告函数需显式使用，不会因安装 Skill 自动接管日常对话。

## 工具维护者怎样使用

完整源码中的 `workbench/progress_view.py` 提供既有报告接口，`workbench/skill_inventory.py` 和 `workbench/skill_route.py` 提供库存与选择工具。本次新增返回两份 Markdown 文本的 `render_progress_documents` 接口；原有完整 Markdown 接口、输入数据格式和现有命令参数保持兼容。请在本版本完整源码目录中使用以下入口，保留相应依赖文件。所有演示均为合成内容；安装与真实宿主配置继续见[技术指南](getting-started.md)。

### 查看或生成辅助报告

可以直接阅读[合成报告首页](examples/progress.md)，再从首页打开详细记录。需要核对生成过程时，在完整源码根目录运行下面的命令；它读取随附的[合成报告数据](examples/progress-report.json)，创建新的 `report-preview` 目录并写入两份 Markdown。目录已存在时会停止，保留原文件；此命令不执行示例中的导出工作。

```bash
python3 -B - <<'PY'
import json
from pathlib import Path
from workbench.progress_view import render_progress_documents

report = json.loads(Path("docs/examples/progress-report.json").read_text(encoding="utf-8"))
documents = render_progress_documents(report)
output = Path("report-preview")
output.mkdir()
for name, text in documents.items():
    (output / name).write_text(text, encoding="utf-8")
print("请打开 report-preview/progress.md")
PY
```

`render_progress_documents` 返回固定名称 `progress.md` 与 `progress-details.md` 的文本，本身不写文件；两页应放在同一目录，保持相对链接有效。它与原 `render_progress_markdown` 都接收经过验证的完整 report 对象，真实接入方应先用 `build_progress_report` 从对应合同构造它。随附数据只演示展示和未完成状态，不能替换真实项目证据。

### 查看能力导航

```bash
python3 -B -m workbench.skill_route --navigation
```

这会显示随源码提供的能力说明，不查询当前安装，也不修改配置。需要对已保存的发现结果做选择检查时，先看 `python3 -B -m workbench.skill_route --help`，再提供真实的 `--input`、`--cwd` 和需要检查的 `--target`。工具会核对发现结果及相关本地 Skill 路径，输出检查结果；不会加载指令正文或授予写入权限。

### 从已有发现结果生成库存报告

先运行 `python3 -B -m workbench.skill_inventory --help` 查看参数。离线入口如下；把占位路径和版本替换为这份发现结果实际对应的值，输出选择一个新的文件位置：

```bash
python3 -B -m workbench.skill_inventory \
  --input /absolute/path/to/saved-skills.json \
  --codex-version captured-version \
  --codex-bin /absolute/path/to/codex \
  --policy workbench/policy.toml \
  --output /absolute/path/to/new-index.md
```

离线模式中的程序路径和版本用于记录来源，不会启动该 Codex 程序；输入中的本地 Skill 路径仍会被读取和校验。指定输出可能覆盖同名文件，因此应先选择新路径。退出 0 表示生成且未发现库存漂移，3 表示已生成但存在漂移提示，4 表示输入或协议错误，应先查看返回原因。本说明不启动真实查询或改变已安装 Skill。

如果输入的发现结果或配置不是 UTF-8，库存工具返回 `protocol_error`，选择工具返回 `UNKNOWN`，退出码均为 4，并说明 UTF-8 要求。此时从原始来源重新导出有效 UTF-8 文件，核对内容后再决定是否运行；工具不会猜测编码或改写输入。异常路径不会创建新的报告或覆盖已有报告。真实查询的其他行为不由这个异常输入检查证明。

## 验证和剩余范围

本次修订把简明首页和完整依据分成两页，通过普通相对链接导航；保留单页完整 Markdown 接口供已有调用方使用。报告输入、JSON 结果与完成判断保持一致。本修订已在候选目录运行 88 项相关确定性检查，失败、错误和跳过均为 0；另外实际执行了 7 项使用入口检查，包括文档命令和已有文件保护。

两页报告不依赖 HTML 折叠交互；首页保留必要提醒，详情页完整呈现记录。目标阅读器中的代表样例已确认首页清楚、详情链接可用；这一观察不外推到所有阅读器。新的环境适应、长期使用收益和复杂业务变更仍需相应真实案例。源码维护由当前任务根据具体目标与授权范围进行；本版本没有新增后台自维护或自动修改已安装 Skill 的机制。其他限制继续见[限制说明](limitations.md)。

## 回退

新版本可以保留在独立目录中使用。需要恢复原工具行为时，停止使用这份 2.3.1 源码目录并返回原 2.3 完整源码；保存自己生成的结果文件。这一选择不涉及已安装 Skill 的切换。实际安装、公开发布和外部服务动作仍按各自目标与具体授权处理。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
