# 3.0.4：解压后可用的只读自检

3.0.4 为 Linux/WSL 用户提供完整的自检工具包，方便检查已有安装、继续项目和反馈问题。

## 下载与运行

从 [v3.0.4 发行页](https://github.com/MsrWang/vibe-leader/releases/tag/v3.0.4)下载 `Vibe-Leader-3.0.4-GitHub.zip` 和 `SHA256SUMS.txt`，放在同一目录。在 Linux/WSL 终端运行：

```bash
sha256sum -c SHA256SUMS.txt
python3 -m zipfile -e Vibe-Leader-3.0.4-GitHub.zip .
cd Vibe-Leader-3.0.4
python3 -B scripts/doctor.py
```

需要 Python 3.11+；无需 Git 或额外 Python 包即可运行自检。保留完整解压目录，不要只复制 `doctor.py`。运行前确认当前终端的 `CODEX_HOME` 对应正在使用的 Codex 宿主；未设置或不能确认时，工具会返回需要关注的状态，不会猜测安装目录。

需要便于反馈的摘要时使用 `python3 -B scripts/doctor.py --json`。状态含义、退出码和可选的发现记录检查见[只读自检说明](self-check.md)。

## 本版本包含什么

- `scripts/doctor.py` 及完整依赖模块：检查安装清单、版本文件内容和可选的宿主发现记录，给出下一步。
- 精简首页与[日常接续请求](project-workflow.md#常用入口)，减少查找常用操作的步骤。
- 使用问题与体验反馈模板，详见[反馈方式](../CONTRIBUTING.md#报告问题)。

**已有 3.0.3 安装无需重装。** 3.0.4 的 13 个 Skill 文件及安装器与 3.0.3 相同，因此自检显示“文件内容匹配 3.0.3”属于预期；它识别的是 Skill 内容，不是整个工具包的发行版本。首次安装或从 3.0.2 及更早版本升级，按[安装指南](getting-started.md)处理。

## 验证与限制

自检在 WSL2 x86_64、CPython 3.14.4 上通过 21 项功能测试；默认全量回归为 `Ran 1070 tests; OK (skipped=6)`。测试使用临时安装和合成材料，覆盖正常、缺失、漂移、异常输入、发现记录、输出与只读约束，详见[验证范围](self-check.md#验证范围)。

自检只读取本机材料，不调用模型、不改配置、不自动保存或上传报告。文件检查完成不证明当前 App 已加载，也不替代实际使用观察。Mac 3.1 仍按独立候选版本的证据范围处理，3.0.4 自检入口不支持 macOS 或 Windows 原生 Python。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
