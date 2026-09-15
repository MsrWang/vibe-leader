# 第三方来源与发行许可范围

Vibe Leader 纳入公开源码的项目自有内容采用 MPL-2.0，维护者署名 Jason Wong。
本声明只针对最终公开发行包中经逐文件确认的内容，不为未纳入发行包的历史材料授权。
项目采用开源许可不代表所有第三方材料均由维护者创作。

## OpenAI Codex 协议参考

固定上游：
https://github.com/openai/codex/tree/86cc9f2177cad015befd595286d8767a650f7d13

版本标签 rust-v0.146.0-alpha.9.2；其协议源码采用 Apache-2.0。
许可证见 [Apache-2.0](LICENSES/Apache-2.0.txt)，上游原 NOTICE 见
[OpenAI Codex NOTICE](LICENSES/openai-codex-NOTICE.txt)。
原 NOTICE 完整保存包括其 Ratatui 归属内容；本包的协议测试夹具本身不是终端 UI 的分发。

[协议来源对照](protocol-source-reference.json) 列出二十个最小测试夹具的固定参考。
这些夹具不是未经修改的官方 schema：它们有字段删减、类型简化、枚举收窄、
局部严格约束及辅助定义拆分。workbench/evaluation_surface.py 的
THREAD_ENVIRONMENTS_DESCRIPTION 引用了同版本协议说明，保留其 Apache 来源。
二十个 schema 的同名 .license 文件逐一提供 Apache-2.0、修改说明和固定上游定位；
schema JSON 本身不变。该 Python 模块的对应字符串前另附 Apache 来源，项目自有
校验逻辑采用 MPL-2.0，不把上游内容改署为维护者原创。

该版本存在实验接口；默认稳定 schema 会过滤实验字段和方法。
这里未核验历史二进制，也未重新生成完整实验 schema。
来源对照中的 reference_sha256 是加入候选通知和改造之前的夹具参考摘要，
不是未来发行文件的最终摘要；最终字节由发行清单另外绑定。

## 文件通知布局

可注释的项目自有 Python、Markdown、YAML、TOML 与支持文件附 MPL-2.0 文件通知；
SKILL.md 的 frontmatter 仍保持在文件开头。11 个 runtime 文件使用文件内通知，不新增
第 12 个安装文件。只复制 runtime 不等于取得完整源码发行包。

严格 JSON 与进程输出 TXT 使用原文件名加 .license 的旁置通知，不把许可字段插进
协议或测试输入；分发时必须将这两份文件一起保留。二十个固定 schema 的旁置文件
采用 Apache-2.0，其余项目自有数据旁置 MPL-2.0。合成样本不是当前宿主运行结果。

完整源码应包含本目录的许可全文、上游原 NOTICE、来源对照及各旁置通知；最终包的
根目录 LICENSE 和全部路径以发行清单另行绑定，不从本页推导已获准的导出范围。
MPL 文件通知参照 [Mozilla Exhibit A](https://www.mozilla.org/en-US/MPL/2.0/)。

原独立源码候选的隐私、许可与来源核对、限定环境验证和用户接受已完成；公开文档与演示的新增部分单列核对。以上不构成全面法律清权，不授权额外公开行为。

<!--
SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->
