# 本地证据筛选

本入口只在调查涉及大文件、多文件或长日志时考虑使用。它是可选的本地相关性筛选步骤：小任务直接按普通流程读取；用户停用后走普通本地调查。筛选候选不决定模型或 Agent、项目绑定、权限、执行或完成。

## 当前能力

- `scripts/evidence_filter.py` 只读取当前项目根目录下显式列出的相对普通文件；不跟随符号链接，不遍历整个项目，也不写文件。
- 输入是通过标准输入传递的 JSON：`query`、`files`，以及可选 `enabled`。不要把文件正文放进命令行参数。
- 从当前已选 `SKILL.md` locator 解析脚本位置，以已绑定项目根目录为工作目录，用 `python3` 启动该 Skill 目录下的 `scripts/evidence_filter.py`；JSON 只通过标准输入传入，不要写进 shell 参数或命令历史。
- 默认实现仅在本机运行确定性的词项相关性排序。本地 selector 接收通过敏感门禁的查询、临时编号（如 `C0001`）和候选正文；源文件路径保留在本地映射中，不进入 selector 或输出。该可替换接口只允许本地实现，不能直接用作未来外部服务的传输边界。
- 当前版本没有 Jev API 适配器、网络请求、凭据读取、遥测或模型调用。未来接入任何外部服务都需要独立审批；敏感门禁通过也不表示业务资料获准外发。
- 唯一允许的来源是当前已选 Skill `SKILL.md` locator 的同级 `scripts/evidence_filter.py`。若当前 locator 或脚本不可验证、Python 执行面不支持安全读取，停止使用筛选器并按普通本地方式调查；不要搜索其他安装目录。

筛选器固定限制为：JSON 输入 1 MiB、查询 4,096 字符、最多 64 个文件、单文件 16 MiB、总输入 64 MiB、窗口最多 4,096 字节且重叠 512 字节、最多 20,000 个候选、最多回读 24 段。它要求 Linux/WSL 上支持目录相对且禁止跟随符号链接的读取能力。超限、编码错误、路径不安全或来源变化时不返回候选正文。

## 调用合同

标准输入必须是一个严格 UTF-8 JSON 对象，只允许以下字段：

```json
{"query":"error code","files":["logs/app.txt"],"enabled":true}
```

- `query` 是非空字符串；`files` 是不重复的项目相对普通文件路径数组；两者必填。
- `enabled` 是可选布尔值，默认为 `true`。未知字段、重复键、错误类型、绝对路径和路径穿越均被拒绝。
- 标准输出只有一行 JSON，固定包含 `schema_version`、`status`、`mode`、`candidate_count`、`selected_count`、`readback_count`、`fallback_reason` 和 `items`。
- `status` 只取 `OK`、`BLOCKED` 或 `UNKNOWN`；`mode` 只取下表五种值。`OK` 只表示本次本地筛选链完整执行，不表示任务完成或事实不存在。
- `items` 中每项只含 `source_ref`、`candidate_id`、`byte_start`、`byte_end` 和经过复核的 `text`；失败、拦截或旁路时为空数组。
- `fallback_reason` 只使用固定原因：`DISABLED`、`NO_CANDIDATE_WINDOWS`、`NO_LOCAL_MATCH`、`INPUT_SCHEMA_INVALID`、`PATH_UNSAFE`、`SENSITIVE_INPUT_BLOCKED`、`INPUT_LIMIT_EXCEEDED`、`INVALID_UTF8`、`SECURE_READ_UNAVAILABLE`、`SOURCE_CHANGED_DURING_READBACK` 或 `SELECTOR_ERROR`；无回退时为 `null`。

## 敏感内容与路径

筛选前会检查查询、输入相对路径、解析后的源路径和完整文件正文，包括普通文本文件。查询或正文中的文件 URI、绝对文件路径、凭据赋值、认证头、私钥标记、常见服务令牌、邮箱或电话号码会返回 `BLOCKED`，不调用筛选器、不回读正文，也不回显输入。实际源路径仅留在本地，不会进入筛选器或输出。该检测器是保守的模式检查，不是通用业务数据分类器；未命中不等于敏感性已被认证。

源路径不会进入筛选器或 JSON 输出。候选编号只在单次调用中使用，不含路径或稳定内容摘要。任何 `UNKNOWN`、`BLOCKED` 或 `BYPASS` 都不应被解释为“文件里没有相关事实”。

## 模式和回读

| 模式 | 含义 | 处理 |
|---|---|---|
| `LOCAL_FILTER` | 本地词项筛选完成 | 仅使用经过精确回读校验的候选段 |
| `BYPASS` | 用户停用筛选 | 不读源文件；继续普通本地调查 |
| `LOCAL_FALLBACK` | 主筛选器异常后切到简单本地排序 | 显示固定回退原因并只使用回读通过的段 |
| `BLOCKED` | 敏感门禁命中 | 不输出正文；说明筛选被安全门禁拦截 |
| `UNKNOWN` | 输入、执行面或来源状态不能判定 | 不输出正文；先恢复普通本地调查或报告证据不足 |

成功结果会重新打开来源文件，核对文件身份和整份原文摘要，再按候选字节范围回读并比较段摘要。若来源在筛选期间改变，结果为 `UNKNOWN`，所有候选正文均丢弃。

每段回读结果带有本次调用内的匿名来源号（如 `F0001`）、候选号和字节起止位置，调用方按原输入文件顺序在本地映射来源号；结果中不包含源路径。

向用户只简要报告模式、候选数、选中数、回读数和回退原因；不要展示源路径、查询原文、敏感输入或内部异常。选中段可以作为证据原文使用，但必须保留来源号与字节范围，不要把编号本身当作证据。

筛选器只表示相关性选择。若某项已确认的必要事实仍缺少证据，最多再做一次有明确缺口依据的补充筛选；仍未找到时报告“证据不足”，不得据此断言事实不存在或判定任务完成。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
