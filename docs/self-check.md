# 一次只读自检，先看卡在哪一步

适用于拿到**包含 `scripts/doctor.py` 的完整源码**、希望检查已有安装的 Linux/WSL 用户，需要 Python 3.11+。请从[仓库当前源码](https://github.com/MsrWang/vibe-leader)获取；已发布的 3.0.3 发行包没有这个入口。脚本只读取本机材料，不安装、修复、改配置、启动 Codex 或调用模型，也不自动保存或上传报告。

## 运行

先确认终端使用的 `CODEX_HOME` 就是当前 Codex 宿主的真实目录，然后在完整源码根运行：

```bash
python3 -B scripts/doctor.py
```

脚本只检查该目录下的 `skills/vibe-project-lead-zh` 和相邻安装清单。变量未设置、路径不匹配或目录不可用时，会告诉你需要确认什么；它不会遍历其他目录猜测安装位置。

需要结构化摘要时：

```bash
python3 -B scripts/doctor.py --json
```

输出到终端，不含本机路径、原始清单和日志。它不是自动收集使用次数的遥测；分享前仍请检查自己追加的内容。

## 看结果

| 栏目 | 结果怎样理解 |
|---|---|
| 安装 `VERIFIED` | 所选目录中的文件、属性与安装清单一致。 |
| 安装 `NOT_FOUND` | 所选目录未安装；不代表其他宿主或其他目录也没有安装。 |
| 安装 `DRIFT` | 文件或属性发生变化，需核对原因和恢复记录。 |
| 安装 `UNKNOWN` | 路径、清单或读取条件不足；按原因继续调查。 |
| 版本 `KNOWN_CONTENT` | 文件内容匹配内置的 3.0.3 指纹。旧清单没有发行版本字段，不能证明下载来源。 |
| 版本 `UNKNOWN_CONTENT` | 没匹配内置指纹；可能是其他版本，不直接当成损坏。 |
| 源码比较 `MATCH` / `DIFFERENT` | 安装文件内容与运行工具的这份源码一致／不同，不受文件模式差异影响。 |
| 宿主发现 `NOT_CHECKED` | 没有提供发现记录，需要在当前宿主查看。 |
| 宿主发现 `SNAPSHOT_MATCH` | 提供的记录与当前所选 Skill 文件匹配；记录的时效和当前 App 状态尚未确认。 |
| 宿主发现 `SNAPSHOT_BLOCKED` / `UNKNOWN` | 记录有阻断或无法判定，可能是缺失、禁用、同名冲突、路径变化或加载错误。 |
| 实际调用 `NOT_CHECKED` | 自检没有启动模型，不能证明实际加载或使用成功。 |

按照报告末尾的“下一步”处理即可。`CODEX_HOME` 未设置时先核对宿主路径；`manifest_target_mismatch` 表示清单记录的目标不一致；`manifest_not_found`、`unsafe_install_state` 表示清单或状态目录需要检查。不要把删除目录或覆盖安装当成通用修复方式。

退出码 **0** 表示本轮本地文件核验完成，**2** 表示环境、输入或证据不足，**3** 表示安装漂移或所提供发现记录存在阻断。**退出 0 不代表 App 或用户验收通过。**

## 已有宿主发现记录时

这一步可选。只有手头已有对应项目的 `skills/list` 原始 JSON 响应时才使用：

```bash
python3 -B scripts/doctor.py \
  --discovery /absolute/path/skills-list.json \
  --project /absolute/path/your-project
```

需要的是包含 `result.data`、对应项目 `cwd`、`skills`、`errors` 的原始响应，不是库存汇总报告、截图或手工补写的成功记录。两个参数必须一起提供；项目目录须绝对、规范化、真实存在，记录最多 1 MiB。

此入口复用既有路由检查，只核对 `vibe-project-lead-zh` 的唯一身份、启用状态、加载错误和精确文件位置。它不启动发现进程，不验证记录来源或时效，也不替代 App 的当前观察。更详细的旧接口说明见[维护方法](maintenance-and-change.md#已有选择工具能帮助判断到哪一步)。

## 最后补一次真实观察

在当前项目中选择“中文跨项目研发主管”，发送：

```text
使用 $vibe-project-lead-zh。请先核对当前项目，只读查看项目说明，
用中文告诉我当前目标和下一步。暂不修改文件。
```

观察 Skill 是否可选且来源正确、是否正常加载、回复是否符合这次请求。文件核验通过但 Skill 不可选时，先回到宿主发现问题；可选但表现不符合预期时，记录最小输入和实际表现。

[日常接续](project-workflow.md) · [安装与恢复](getting-started.md) · [反馈方式](../CONTRIBUTING.md#报告问题)

## 验证范围

2026-10-22，在 WSL2 x86_64、CPython 3.14.4 上通过 21 项功能测试，使用临时目录中的真实安装器输出，覆盖正常安装、缺失与漂移、版本内容区分、发现记录校验、异常输入、退出码、文本与 JSON 输出，以及只读约束。

每次 CLI 检查前后比较临时输入的内容、模式和修改时间；另用进程内审计钩子阻止并检查写入、网络和子进程操作。读取可能更新访问时间，这些检查不等于操作系统级沙箱证明。针对非 Linux 的分支测试仅模拟平台标识，不是 Mac 或 Windows 原生实测。

```bash
python3 -B -m unittest -v tests.test_doctor
```

同日的全量默认回归结果为 `Ran 1070 tests; OK (skipped=6)`。6 项跳过记录涉及需要专用临时盘或显式启用的跨文件系统、Windows 互操作检查；本次未启用这些入口。全量回归使用隔离的临时 `CODEX_HOME`。首页精简遗漏的一条纠偏说明已根据回归结果补回，原有断言保留。

本工具不属于 13 文件 Skill 的自动执行流程，运行它无需升级现有 Skill。检查是顺序读取，不是原子快照；有并发修改时不能据此授权安装或恢复。实际 App 加载与用户使用仍需各自观察；Mac 和 Windows 原生不在此入口支持范围内。

<!-- SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/. -->
