# SPDX-License-Identifier: MPL-2.0
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skill" / "vibe-project-lead-zh"
EXPECTED_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/project-binding.md",
    "references/evidence-screening.md",
    "references/manager-workflow.md",
    "references/safety-gates.md",
    "references/acceptance-and-supervision.md",
    "references/adaptive-delegation.md",
    "references/portfolio.md",
    "references/deployment-governance.md",
    "references/human-delivery.md",
    "references/SKILL_INDEX_ZH.md",
    "scripts/evidence_filter.py",
}
EXPECTED_OPENAI_YAML_SHA256 = (
    "8b8238c65872c43858611a60399aff4009569feb4c934eb88ce1bb38f7712d71"
)


def read(relative: str) -> str:
    return (SKILL_ROOT / relative).read_text(encoding="utf-8")


def frontmatter_keys(text: str) -> set[str]:
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    if match is None:
        return set()
    return {
        line.split(":", 1)[0].strip()
        for line in match.group(1).splitlines()
        if ":" in line
    }


class RuleAlignmentContractTests(unittest.TestCase):
    """Static alignment checks, not proof of model behavior or authorization."""

    def section(self, filename: str, heading: str) -> str:
        text = read(f"references/{filename}")
        marker = f"## {heading}\n"
        self.assertEqual(text.count(marker), 1, f"missing/ambiguous {heading}")
        return text.split(marker, 1)[1].split("\n## ", 1)[0]

    def test_protected_projects_do_not_depend_on_a_maintainer_path(self):
        binding = self.section("project-binding.md", "首命令不变量")
        for required in ("受保护或高敏感项目", "明确标记", "用户明确指定", "精确匹配", "未标记"):
            with self.subTest(required=required):
                self.assertTrue(required in binding, f"missing protected-project condition: {required}")
        for filename in ("project-binding.md", "safety-gates.md"):
            with self.subTest(filename=filename):
                text = read(f"references/{filename}")
                self.assertFalse(
                    re.search(r"/home/[^/\s`]+/projects/|TikTok Video Factory|\bTVF\b", text),
                    f"maintainer-specific project coupling in {filename}",
                )

    def test_resume_does_not_blanket_revoke_unconsumed_local_authority(self):
        forbidden = (
            ("project-binding.md", "清除所有尚未消费的 C-F 操作审批"),
            ("adaptive-delegation.md", "尚未消费的批次审批和结果一律失效"),
            ("acceptance-and-supervision.md", "旧 C-F 审批已清除"),
        )
        for filename, clause in forbidden:
            with self.subTest(filename=filename):
                self.assertFalse(clause in read(f"references/{filename}"), f"blanket revocation in {filename}")

    def test_resume_consumers_point_to_one_permission_lifecycle(self):
        consumers = (
            ("project-binding.md", "恢复"),
            ("adaptive-delegation.md", "中断恢复"),
            ("manager-workflow.md", "恢复与 freshness"),
            ("acceptance-and-supervision.md", "同一任务恢复"),
            ("human-delivery.md", "Stable resume 与失效"),
        )
        for filename, heading in consumers:
            body = self.section(filename, heading)
            for required in ("safety-gates.md", "审批绑定与生命周期"):
                with self.subTest(filename=filename, required=required):
                    self.assertTrue(required in body, f"missing lifecycle authority in {filename}: {required}")

    def test_batch_resume_never_revives_expired_engineering_results(self):
        batch = self.section("safety-gates.md", "审批绑定与生命周期")
        for required in ("明确允许稳定恢复", "剩余次数", "期限", "不重置", "已终止", "E/F 外部审批永不恢复"):
            with self.subTest(required=required):
                self.assertTrue(required in batch, f"missing batch lifecycle condition: {required}")
        result = self.section("adaptive-delegation.md", "中断恢复")
        for required in ("批次剩余批准", "不恢复旧 brief/result", "TASK_INTERRUPTED_OR_RESUMED", "UNKNOWN / BLOCKED"):
            with self.subTest(required=required):
                self.assertTrue(required in result, f"missing engineering result boundary: {required}")

    def test_pause_is_a_stop_not_an_implicit_cancellation(self):
        human = self.section("human-delivery.md", "Stable resume 与失效")
        safety = self.section("safety-gates.md", "审批绑定与生命周期")
        self.assertFalse("用户暂停、取消或改变目标" in human, "pause still shares unconditional invalidation")
        for required in ("暂停", "停止新动作", "重新核验", "取消", "终止"):
            with self.subTest(required=required):
                self.assertTrue(required in safety, f"missing pause/cancel boundary: {required}")

    def test_text_observations_do_not_masquerade_as_engineering_delegation(self):
        text = read("references/adaptive-delegation.md")
        for required in ("主线程", "工程子任务", "纯文本样本", "不伪造工程身份", "不放宽工程校验器", "宿主强制规则"):
            with self.subTest(required=required):
                self.assertTrue(required in text, f"missing role boundary: {required}")


class DocumentedDelegationInterfaceTests(unittest.TestCase):
    """Exercise documented payload shapes through the real, pure receiver.

    Identity, usage and traces are synthetic fixtures, not native observations.
    These tests catch unusable schema/field examples, not model disobedience.
    """

    def documented_payload(self, kind, fixture):
        text = read("references/adaptive-delegation.md")
        match = re.search(
            rf"委派 {kind} 的 schema ID 是 `([^`]+)`，顶层字段必须\s*"
            r"精确包含：\s*```text\n(.*?)\n```", text, re.DOTALL,
        )
        self.assertIsNotNone(match, f"missing documented {kind} payload contract")
        schema_id, fields_text = match.groups()
        fields = [field.strip() for field in fields_text.split(",")]
        self.assertEqual(len(fields), len(set(fields)), "duplicate documented fields")
        self.assertFalse(set(fields) - set(fixture), "unknown documented payload fields")
        value = {field: fixture[field] for field in fields}
        value["schema_id"] = schema_id
        return value

    def evaluate(self, brief, result, context, *, runtime=None):
        from workbench import delegation_contract as contract
        from tests.test_delegation_contract import runtime_context_fixture
        return contract.evaluate_delegation_result(
            brief, result,
            current_main_identity=brief["baseline"]["main_thread_identity"],
            current_authority_identity=result["return_baseline"],
            runtime_context=runtime if runtime is not None else runtime_context_fixture(brief),
            reception_context=context,
        )

    def test_documented_brief_shape_is_receivable_not_legacy_diagnostic_only(self):
        from tests.test_delegation_contract import (
            valid_brief, result_fixture, reception_context_fixture,
        )
        template = valid_brief(mode="REVIEW")
        brief = self.documented_payload("brief", template)
        result = result_fixture(brief)
        context = reception_context_fixture(template, result)
        from workbench import delegation_contract as contract
        context["brief_sha256"] = contract.canonical_contract_digest(brief)
        value = self.evaluate(brief, result, context)
        self.assertEqual(value["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW", value)
        self.assertTrue(value["consumable"])
        self.assertFalse(value["write_authorized"])

    def test_documented_result_shape_binds_the_brief_and_is_receivable(self):
        from tests.test_delegation_contract import (
            valid_brief, result_fixture, reception_context_fixture,
        )
        brief = valid_brief(mode="REVIEW")
        result = self.documented_payload("result", result_fixture(brief))
        value = self.evaluate(brief, result, reception_context_fixture(brief, result))
        self.assertEqual(value["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW", value)
        self.assertFalse(value["write_authorized"])
        result["brief_sha256"] = "0" * 64
        rejected = self.evaluate(brief, result, reception_context_fixture(brief, result))
        self.assertEqual(rejected["decision"], "REJECTED")
        self.assertIn("RESULT_BRIEF_MISMATCH", rejected["reasons"])

    def test_documented_resume_fragment_preserves_stop_and_runtime_guards(self):
        from tests.test_delegation_contract import (
            valid_brief, result_fixture, reception_context_fixture, runtime_context_fixture,
        )
        snippets = re.findall(r"```json\n(.*?)\n```",
                              read("references/adaptive-delegation.md"), re.DOTALL)
        examples = [json.loads(snippet) for snippet in snippets]
        fragments = [value for value in examples if "reception_context_fields" in value]
        self.assertEqual(len(fragments), 1, "missing/ambiguous executable resume fragment")
        fragment = fragments[0]
        brief = valid_brief(mode="REVIEW")
        brief["reception"] = fragment["reception"]
        brief["expiry"] = fragment["expiry"]
        result = result_fixture(brief)
        fixture = reception_context_fixture(brief, result)
        fields = fragment["reception_context_fields"]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertFalse(set(fields) - set(fixture))
        context = {field: fixture[field] for field in fields}
        context["events"] = ["SAME_TASK_RESUME"]
        value = self.evaluate(brief, result, context)
        self.assertEqual(value["decision"], "ACCEPTED_FOR_MAIN_THREAD_REVIEW", value)
        self.assertFalse(value["write_authorized"])
        for event in ("USER_PAUSED", "USER_CANCELLED", "APPROVAL_REVOKED", "TASK_INTERRUPTED"):
            with self.subTest(event=event):
                stopped = dict(context, events=[event, "SAME_TASK_RESUME"])
                value = self.evaluate(brief, result, stopped)
                self.assertEqual(value["freshness"], "STALE", value)
                self.assertFalse(value["consumable"])
                self.assertIsNone(value["consumption_record"])
        for field, changed in (("codex_version", "0.148.0"), ("core_sha256", "2" * 64)):
            with self.subTest(runtime_field=field):
                runtime = runtime_context_fixture(brief)
                runtime["current"][field] = changed
                value = self.evaluate(brief, result, context, runtime=runtime)
                self.assertEqual(value["freshness"], "STALE", value)
                self.assertIsNone(value["consumption_record"])


class SkillContractTests(unittest.TestCase):
    def assert_contains_all(self, text: str, values: tuple[str, ...]) -> None:
        for value in values:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def read_adaptive_delegation(self) -> str:
        path = SKILL_ROOT / "references" / "adaptive-delegation.md"
        self.assertTrue(path.is_file(), "adaptive delegation reference is missing")
        return path.read_text(encoding="utf-8")

    def test_runtime_package_has_exact_approved_thirteen_files(self):
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, EXPECTED_FILES)

    def test_frontmatter_and_explicit_entry_are_stable(self):
        text = read("SKILL.md")

        self.assertEqual(frontmatter_keys(text), {"name", "description"})
        self.assertIn("name: vibe-project-lead-zh", text)
        self.assertIn("中文跨项目研发主管", text)
        self.assertIn("$vibe-project-lead-zh", text)
        self.assertIn("未显式进入时，不改变普通 Codex Coding 工作流", text)
        self.assertLessEqual(len(text.splitlines()), 500)

    def test_main_loop_continues_stable_work_until_meaningful_checkpoint(self):
        text = read("SKILL.md")
        section = text.split("## 主循环\n", 1)[1].split("\n## ", 1)[0]

        self.assert_contains_all(
            section,
            (
                "不表示每完成一个普通步骤就结束当前回合",
                "连续推进一组相互依赖的本地步骤",
                "不反复结束当前回合让用户重新要求继续",
            ),
        )

        workflow = (ROOT / "docs/project-workflow.md").read_text(encoding="utf-8")
        self.assert_contains_all(
            workflow,
            (
                "连续推进到一个有意义的检查点",
                "不需要我每隔几分钟重新说一次“继续”",
                "当前任务仍继续推进",
                "不是当前工作的前置条件",
                "不要求为了测试中断",
            ),
        )

    def test_lightweight_deviation_card_is_discoverable_and_low_noise(self):
        workflow = (ROOT / "docs/project-workflow.md").read_text(encoding="utf-8")
        card_path = ROOT / "docs/deviation-correction.md"

        self.assertTrue(card_path.is_file(), "deviation correction card is missing")
        self.assert_contains_all(
            workflow,
            (
                "感觉当前推进有偏差",
                "纠偏判断",
                "不需要先说清技术原因",
                "deviation-correction.md",
            ),
        )
        self.assert_contains_all(
            card_path.read_text(encoding="utf-8"),
            (
                "默认只显示三行",
                "最小修正",
                "现在继续",
                "没有新证据时不重复",
                "不会产生新的授权",
            ),
        )

    def test_main_loop_routes_lightweight_deviation_correction(self):
        skill = read("SKILL.md")
        workflow = read("references/manager-workflow.md")
        marker = "## 轻量纠偏\n"
        self.assertEqual(workflow.count(marker), 1, "missing/ambiguous 轻量纠偏")
        correction = workflow.split(marker, 1)[1].split("\n## ", 1)[0]

        self.assert_contains_all(
            skill,
            (
                "方向、范围或目标可能偏离",
                "停止扩大范围",
                "轻量纠偏",
                "最小修正后继续推进",
            ),
        )
        self.assert_contains_all(
            correction,
            (
                "不绑定固定关键词",
                "必要中间步骤",
                "执行偏差",
                "范围偏差",
                "局部占据主线",
                "用户目标改变",
                "暂无偏差证据",
                "证据不足",
                "默认只显示三行",
                "没有新证据时不重复",
                "不要求用户再次说明",
                "继续原已授权行动",
                "不产生新的授权",
            ),
        )

    def test_maintenance_guide_separates_rule_revision_install_and_behavior_evidence(self):
        text = (ROOT / "docs/maintenance-and-change.md").read_text(encoding="utf-8")

        self.assert_contains_all(
            text,
            (
                "最初先把它记录为执行与计划接续遗漏",
                "用户随后又明确反馈",
                "最小规则修订",
                "实际安装已经完成",
                "当前窗口是否重新加载新版仍未验证",
                "当前任务继续推进",
            ),
        )

    def test_change_impact_guide_covers_complex_rule_upgrade(self):
        text = (ROOT / "docs/maintenance-and-change.md").read_text(encoding="utf-8")

        self.assert_contains_all(
            text,
            (
                "复杂应用：连续推进规则与实际安装",
                "变化后的用户结果",
                "受影响能力与依赖",
                "仍适用的旧证据",
                "必须补验的部分",
                "旧五次对话观察",
                "不需要重新安装",
            ),
        )

    def test_3_0_2_public_materials_explain_lightweight_correction(self):
        release_path = ROOT / "docs/release-3.0.2.md"
        self.assertTrue(release_path.is_file(), "3.0.2 release note is missing")

        release = release_path.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        getting_started = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")
        maintenance = (ROOT / "docs/maintenance-and-change.md").read_text(encoding="utf-8")
        demo_readme = (ROOT / "demo/README.md").read_text(encoding="utf-8")
        demo_html = (ROOT / "demo/index.html").read_text(encoding="utf-8")
        demo_js = (ROOT / "demo/app.js").read_text(encoding="utf-8")

        for filename, text in (
            ("README.md", readme),
            ("CHANGELOG.md", changelog),
            ("getting-started.md", getting_started),
            ("maintenance-and-change.md", maintenance),
            ("demo/README.md", demo_readme),
            ("demo/index.html", demo_html),
        ):
            with self.subTest(filename=filename):
                self.assertIn("3.0.2", text)

        self.assert_contains_all(
            release,
            (
                "默认只显示三行",
                "纠偏判断",
                "最小修正",
                "现在继续",
                "不产生新的授权",
                "六个合成情境",
                "实际安装",
                "重新加载",
                "长期自然项目",
            ),
        )
        self.assert_contains_all(
            readme,
            (
                "docs/release-3.0.2.md",
                "docs/deviation-correction.md",
                "不需要先说清技术原因",
            ),
        )
        self.assert_contains_all(
            getting_started,
            (
                "3.0.1 或更早安装",
                "受控升级",
                "安装成功、宿主重新加载和实际行为",
            ),
        )
        self.assert_contains_all(
            maintenance,
            (
                "轻量自动纠偏",
                "行为基线",
                "重复信号",
                "原已授权行动",
            ),
        )
        self.assertIn('<option value="deviation">感觉项目跑偏</option>', demo_html)
        self.assert_contains_all(
            demo_js,
            (
                "deviation:",
                "我感觉当前推进有偏差",
                "纠偏判断",
                "最小修正",
                "现在继续",
            ),
        )
        for forbidden in ("fetch(", "XMLHttpRequest", "<form", "analytics"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, demo_html + demo_js)

    def test_3_0_3_public_materials_explain_local_evidence_screening(self):
        release_path = ROOT / "docs/release-3.0.3.md"
        self.assertTrue(release_path.is_file(), "3.0.3 release note is missing")

        release = release_path.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        getting_started = (ROOT / "docs/getting-started.md").read_text(
            encoding="utf-8"
        )
        demo_readme = (ROOT / "demo/README.md").read_text(encoding="utf-8")
        demo_html = (ROOT / "demo/index.html").read_text(encoding="utf-8")

        for filename, text in (
            ("README.md", readme),
            ("CHANGELOG.md", changelog),
            ("getting-started.md", getting_started),
            ("demo/README.md", demo_readme),
            ("demo/index.html", demo_html),
        ):
            with self.subTest(filename=filename):
                self.assertIn("3.0.3", text)

        self.assert_contains_all(
            release,
            (
                "13 个文件",
                "本地证据筛选",
                "显式调用",
                "不调用 Jev API",
                "不读取凭据",
                "不判断任务完成",
                "实际安装",
                "重新加载",
                "长期自然任务仍需观察",
            ),
        )
        self.assert_contains_all(
            readme,
            (
                "docs/release-3.0.3.md",
                "本地证据筛选",
                "13 个文件",
            ),
        )
        self.assert_contains_all(
            changelog,
            (
                "3.0.3 — 本地证据筛选",
                "13 个文件",
                "不调用 Jev API",
            ),
        )
        self.assert_contains_all(
            getting_started,
            (
                "3.0.2 或更早安装",
                "受控升级",
                "release-3.0.3.md",
            ),
        )
        self.assert_contains_all(
            demo_readme,
            (
                "本地证据筛选",
                "静态示例不会运行筛选器",
            ),
        )
        self.assert_contains_all(
            demo_html,
            (
                "安装的 Skill 是 13 个文件",
                "docs/release-3.0.3.md",
                "本地证据筛选",
            ),
        )

    def test_3_1_public_materials_define_macos_candidate_boundary(self):
        paths = {
            "README.md": ROOT / "README.md",
            "CHANGELOG.md": ROOT / "CHANGELOG.md",
            "release-3.1.0.md": ROOT / "docs" / "release-3.1.0.md",
            "macos-acceptance-3.1.md": ROOT / "docs" / "macos-acceptance-3.1.md",
            "getting-started.md": ROOT / "docs" / "getting-started.md",
            "limitations.md": ROOT / "docs" / "limitations.md",
            "demo/README.md": ROOT / "demo" / "README.md",
            "demo/index.html": ROOT / "demo" / "index.html",
        }
        materials = {}
        for name, path in paths.items():
            with self.subTest(name=name, contract="file-exists"):
                self.assertTrue(path.is_file(), f"missing public material: {name}")
            materials[name] = path.read_text(encoding="utf-8")

        bounded_runtime = "稳定版 CPython 3.11–3.14（最低 3.11；新安装推荐 3.14.7）"
        reference_boundary = "2026 Mac mini（M6）仅为参考验收目标，不是已验证结论"
        for name, text in materials.items():
            for required in (
                bounded_runtime,
                reference_boundary,
                "不接入外部 Jev",
                "13 个文件",
            ):
                with self.subTest(name=name, required=required):
                    self.assertIn(required, text)
            for forbidden in (
                "Python 3.11+",
                "稳定版 Python 3.11+",
                "当前处于本地候选冻结阶段",
                "候选资产尚未发布",
                "当前仅完成本地准备",
                "当前文档只完成第一个门",
                "3.0.3 继续作为稳定版",
                "3.0.3 仍是稳定版",
                "真实 Mac 验收尚未完成",
                "当前仍是候选准备",
                "3.1.0 macOS 候选：",
                "<span>3.1.0 候选</span>",
                "页面按 3.0.3 标明当前版本",
                "本候选没有证明 Intel Mac",
                "WSL 模拟测试已证明 Mac 原生通过",
                "Codex App 验收已完成",
                "结论：Mac 本机就绪",
                "结论：兼容验收通过",
            ):
                with self.subTest(name=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)

        release = materials["release-3.1.0.md"]
        acceptance = materials["macos-acceptance-3.1.md"]
        limitations = materials["limitations.md"]
        for gate in (
            "本地候选冻结",
            "GitHub 候选 prerelease",
            "Mac 候选实装验收",
            "GitHub 正式发布",
            "Mac 稳定版实装",
            "Hugging Face 同步",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, release)
        self.assertIn("外部动作分别批准", release + acceptance)
        self.assertIn("生命周期状态由外部 Release 元数据", release)
        self.assertIn("经签名或平台回读的收据", release)

        for required in (
            "https://github.com/MsrWang/vibe-leader.git",
            "MsrWang/vibe-leader",
            "api.github.com/repos/MsrWang/vibe-leader/releases/tags/",
            "assets[].digest",
            "三个下载资产",
            "SHA256SUMS.txt 不是自身信任根",
            'git show "$VIBE_EXPECTED_COMMIT:scripts/release_archive.py"',
            "cmp",
            'CODEX_HOME="$VIBE_TEMP_CODEX_HOME"',
            'CODEX_HOME="$VIBE_REAL_CODEX_HOME"',
            "python3 -B -m unittest -v tests.test_evidence_filter",
            "普通文件",
            "符号链接拒绝",
            "特殊文件拒绝",
            "来源变更",
            'VIBE_BASELINE_TAG="v3.0.3"',
            "prepare-upgrade",
            "inspect-upgrade",
            "restore-version",
            "原始 manifest 和状态目录",
            "清理与残留",
            "私有原始证据",
            "公开去标识收据",
            "绝对路径只保留在私有原始证据",
            "环境、安装前状态或资产摘要",
            "重新执行真实恢复演练",
            "候选回滚",
            "稳定版实装",
        ):
            with self.subTest(acceptance_required=required):
                self.assertIn(required, acceptance)
        self.assertIn("Intel Mac 未验收", limitations)
        self.assertIn("WSL 测试不是 Mac 原生验证", "".join(materials.values()))
        self.assertIn(
            "用户观察前不得声称 Codex App 验收完成",
            "".join(materials.values()),
        )

    def test_openai_metadata_is_chinese_and_explicit_only(self):
        text = read("agents/openai.yaml")

        self.assertIn('display_name: "中文跨项目研发主管"', text)
        self.assertIn(
            'short_description: "用中文管理项目调查、需求规划、实施验证、用户验收与安全审批门禁"',
            text,
        )
        self.assertIn("使用 $vibe-project-lead-zh", text)
        self.assertIn("allow_implicit_invocation: false", text)

    def test_1_1_contract_names_no_second_state_source(self):
        text = "\n".join(
            (
                read("SKILL.md"),
                read("references/manager-workflow.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "Codex 原生 resume、Goal 和 memory",
                "`project-checkpoint.md`",
                "自定义 session registry",
                "自动写入的 resume manifest",
                "压缩 Hook",
            ),
        )

    def test_binding_requires_independent_pwd_and_two_captures(self):
        text = read("references/project-binding.md")

        self.assert_contains_all(
            text,
            (
                "workspace binding",
                "路径转换成功不等于",
                "STOP_WRONG_WORKSPACE",
                "两次完整捕获",
                "STATE_CHANGED_DURING_CAPTURE",
                "不自动第三次重试",
                "BARE_GIT",
            ),
        )
        pwd_position = text.find("独立执行 `pwd`")
        capture_position = text.find("## 两次完整捕获")
        self.assertGreaterEqual(pwd_position, 0)
        self.assertGreaterEqual(capture_position, 0)
        self.assertLess(pwd_position, capture_position)

    def test_stable_resume_keeps_local_envelope_but_never_revives_external_gates(self):
        text = "\n".join(
            (
                read("references/manager-workflow.md"),
                read("references/safety-gates.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "stable same-task resume",
                "未消费的本地包络",
                "E/F 外部审批永不恢复",
                "已消费的一次性审批永不复用",
                "不能替代本轮项目绑定",
                "平台强制权限提示不能被本 Skill 取消",
            ),
        )

    def test_freshness_unknown_cannot_authorize_write(self):
        text = "\n".join(
            (
                read("references/project-binding.md"),
                read("references/manager-workflow.md"),
                read("references/safety-gates.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "NO_COMPARISON_BASELINE",
                "STOP_FRESHNESS_STALE",
                "STOP_FRESHNESS_UNKNOWN",
                "AMBIGUOUS > UNKNOWN > STALE > FRESH",
                "批准本身不能把 `UNKNOWN` 改成 `FRESH`",
            ),
        )

    def test_route_local_skill_failure_is_not_global_failure(self):
        text = "\n".join(
            (
                read("references/manager-workflow.md"),
                read("references/SKILL_INDEX_ZH.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "稳定中文映射",
                "本次动态发现",
                "BLOCKING_SUPERVISOR",
                "BLOCKING_ROUTE",
                "NON_BLOCKING_DRIFT",
                "无关 Skill 漂移不得全局阻断",
            ),
        )

    def test_upgrade_requires_old_archive_and_new_approval(self):
        text = read("references/safety-gates.md")

        self.assert_contains_all(
            text,
            (
                "EXCHANGE_SUPPORTED",
                "NOREPLACE_ONLY",
                "切换能力",
                "阶段",
                "非原子窗口",
                "旧版本归档",
                "恢复旧版本必须使用新的 E 级审批",
            ),
        )

    def test_cross_project_switch_requires_new_task(self):
        text = "\n".join(
            (
                read("references/project-binding.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "STOP_PROJECT_SWITCH",
                "不同项目之间不得在同一任务内重新绑定",
                "新任务重新选择主管",
                "不继承上一个项目的 C-F 操作审批",
            ),
        )

    def test_runtime_file_set_is_exactly_thirteen(self):
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_bytes()

        self.assertEqual(actual, EXPECTED_FILES)
        self.assertEqual(len(actual), 13)
        self.assertEqual(hashlib.sha256(metadata).hexdigest(), EXPECTED_OPENAI_YAML_SHA256)
        self.assertIn("allow_implicit_invocation: false", metadata.decode("utf-8"))

    def test_reliability_modules_do_not_read_codex_home_at_import_time(self):
        modules = (
            ROOT / "workbench" / "project_identity.py",
            ROOT / "workbench" / "project_freshness.py",
            ROOT / "workbench" / "skill_inventory.py",
            ROOT / "workbench" / "delegation_contract.py",
            ROOT / "workbench" / "outcome_governance.py",
            ROOT / "workbench" / "execution_governance.py",
            ROOT / "workbench" / "strategy_governance.py",
            ROOT / "workbench" / "acceptance_governance.py",
            ROOT / "workbench" / "progress_view.py",
            ROOT / "scripts" / "install_skill.py",
        )
        script = r'''
import importlib.util
import os
import pathlib
import sys

sentinel = os.path.abspath(sys.argv[1])
module_paths = [pathlib.Path(value) for value in sys.argv[2:]]
compiled = []
for index, path in enumerate(module_paths):
    name = f"import_contract_{index}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    compiled.append((name, spec.loader.get_code(name)))

def is_sentinel(value):
    try:
        candidate = os.path.abspath(os.fspath(value))
    except TypeError:
        return False
    return candidate == sentinel or candidate.startswith(sentinel + os.sep)

def guard(function):
    def wrapped(path, *args, **kwargs):
        if is_sentinel(path):
            raise AssertionError(f"CODEX_HOME accessed at import time: {path}")
        return function(path, *args, **kwargs)
    return wrapped

original_lstat = os.lstat
for name in ("open", "stat", "lstat", "listdir", "scandir", "readlink"):
    setattr(os, name, guard(getattr(os, name)))
os.environ["CODEX_HOME"] = sentinel
for name, code in compiled:
    module = type(sys)(name)
    module.__file__ = str(module_paths[int(name.rsplit("_", 1)[1])])
    module.__package__ = ""
    sys.modules[name] = module
    exec(code, module.__dict__)
try:
    original_lstat(sentinel)
except FileNotFoundError:
    pass
else:
    raise AssertionError("module import created CODEX_HOME state")
'''
        with tempfile.TemporaryDirectory() as tempdir:
            sentinel = Path(tempdir) / "must-not-be-read-or-created"
            result = subprocess.run(
                [sys.executable, "-c", script, sentinel, *modules],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_references_are_direct_and_present(self):
        text = read("SKILL.md")
        links = set(re.findall(r"\(references/([A-Za-z0-9_.-]+)\)", text))

        self.assertEqual(
            links,
            {
                "project-binding.md",
                "manager-workflow.md",
                "safety-gates.md",
                "acceptance-and-supervision.md",
                "adaptive-delegation.md",
                "portfolio.md",
                "deployment-governance.md",
                "human-delivery.md",
                "evidence-screening.md",
                "SKILL_INDEX_ZH.md",
            },
        )
        self.assertNotRegex(text, r"references/[^)]+/")

    def test_adaptive_delegation_reference_is_direct_and_runtime_package_has_thirteen_files(self):
        skill = read("SKILL.md")
        adaptive_path = SKILL_ROOT / "references" / "adaptive-delegation.md"

        self.assertTrue(adaptive_path.is_file())
        self.assertIn(
            "[自适应委派](references/adaptive-delegation.md)",
            skill,
        )
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, EXPECTED_FILES)
        self.assertEqual(len(actual), 13)
        self.assertNotRegex(self.read_adaptive_delegation(), r"\]\([^)]*\.md\)")

    def test_human_delivery_route_and_gates_are_explicit(self):
        skill = read("SKILL.md")
        delivery = read("references/human-delivery.md")
        combined = "\n".join(
            (
                skill,
                read("references/manager-workflow.md"),
                read("references/safety-gates.md"),
                read("references/acceptance-and-supervision.md"),
                delivery,
            )
        )

        self.assertIn("references/human-delivery.md", skill)
        self.assert_contains_all(
            combined,
            (
                "OUTCOME_CONTRACT_V1",
                "EXECUTION_ENVELOPE_V1",
                "STRATEGY_ATTEMPT_V1",
                "ACCEPTANCE_DECISION_V1",
                "PROGRESS_REPORT_V1",
                "第四次实质失败",
                "不得第五次尝试",
                "U0",
                "U1",
                "U2",
                "U3",
                "用户可以提高验收等级",
                "write_authorized=false",
            ),
        )

    def test_safety_supplement_contracts_are_explicit(self):
        delivery = read("references/human-delivery.md")
        combined = "\n".join(
            (
                read("SKILL.md"),
                delivery,
                read("references/manager-workflow.md"),
                read("references/safety-gates.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            combined,
            (
                "每个动作必须携带 FILE_SET 或 BEHAVIOR scope",
                "task、envelope、worktree、environment、allowlist、permission 和 side-effect",
                "resume_state_digest 不得恢复已消费动作",
                "终止状态后不得追加 attempt",
                "只读敏感数据仍为 U3",
                "PRE_ACTION_APPROVAL",
                "POST_ACTION_OBSERVATION",
                "ROLLBACK_VERIFICATION",
                "USER_EVENT_RECEIPT_V1",
                '不能使用 recorded_by="USER" 自证',
                "completion_state",
                "completion_basis 重新派生",
                "完成措辞由结构化状态生成",
                "敏感值失败关闭",
            ),
        )

        pre_action = delivery.find("PRE_ACTION_APPROVAL")
        external_execution = delivery.find("执行外部动作")
        external_result = delivery.find("取得外部动作结果")
        post_action = delivery.find("POST_ACTION_OBSERVATION")
        rollback = delivery.find("ROLLBACK_VERIFICATION")
        accepted = delivery.find("U3 验收接受")
        for position in (
            pre_action,
            external_execution,
            external_result,
            post_action,
            rollback,
            accepted,
        ):
            self.assertGreaterEqual(position, 0)
        self.assertLess(pre_action, external_execution)
        self.assertLess(external_execution, external_result)
        self.assertLess(external_result, post_action)
        self.assertLess(post_action, rollback)
        self.assertLess(rollback, accepted)

    def test_safe_progress_projection_contract_is_explicit(self):
        delivery = read("references/human-delivery.md")
        workflow = read("references/manager-workflow.md")
        combined = "\n".join(
            (
                read("SKILL.md"),
                delivery,
                workflow,
                read("references/safety-gates.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            combined,
            (
                "原始目标仅由 Outcome Contract 权威保留",
                "安全目标摘要不参与完成裁决",
                "original_goal_sha256",
                "RETAINED_IN_OUTCOME_CONTRACT",
                "不得回退展示 original_goal",
                "SAFE_PUBLIC_TEXT_V1",
                "task-list 和 inline-code 内容失败关闭",
                "只有合法 STABLE_RESUME 可参与完成",
                "集合语义列表按 UTF-8 规范排序",
                "不得替用户确认真人观察",
            ),
        )
        self.assertIn(
            "Outcome authority -> safe projection -> Progress validation -> "
            "JSON/Markdown export -> applicable human observation",
            workflow,
        )

    def test_safety_003_uses_absolute_marker_and_closed_message_contract(self):
        text = "\n".join(
            (
                read("references/human-delivery.md"),
                read("references/safety-gates.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "Authorization 自由文本一律失败关闭",
                "NFKC 只用于安全扫描，不改写展示原文",
                "AUTHORIZATION_VALUE_HIDDEN",
                "认证请求头的值不得进入公开进度。",
                "不得恢复自然语言白名单或 word-boundary 猜测",
                "原始目标仍只由 Outcome authority 保留",
                "不得替用户确认 U2 观察",
            ),
        )

    def test_safety_004_unifies_scanning_and_human_rejection_contract(self):
        text = "\n".join(
            (
                read("references/human-delivery.md"),
                read("references/safety-gates.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "NFKC -> casefold -> 删除 Unicode Cf",
                "全部内容 detector 只消费这一份扫描投影",
                "原始文本只在全部检查通过后原样返回",
                "Progress 在构建后和两个 renderer 前执行最终递归复扫",
                "核心错误统一为 `UNSAFE_PUBLIC_TEXT`，消费者统一为 `SECRET_VALUE_REJECTED`",
                "不得按语言、标点或 Markdown wrapper 追加补丁",
                "安全中文、本地路径、SHA-256 和无值标识符必须保持原样",
                "R1=STOP_CONTRACT_FAILURE / 87/88 visible-complete ranges",
                "R2=STOP_CONTRACT_FAILURE / 0/102 accepted coverage",
                "R3=STOP_CRITICAL_HIGH / 102/102 READ_COMPLETE",
                "U2 真人观察不得自动填写或判定 PASS",
            ),
        )

    def test_delivery_order_blocks_premature_uat_and_user_acceptance(self):
        text = "\n".join(
            (
                read("references/human-delivery.md"),
                read("references/acceptance-and-supervision.md"),
            )
        )
        critical = text.find("关键能力验证")
        final_uat = text.find("最终 UAT")
        observations = text.find("POST_ACTION_OBSERVATION")
        accepted = text.find("USER_ACCEPTED")

        self.assertGreaterEqual(critical, 0)
        self.assertGreater(final_uat, critical)
        self.assertGreaterEqual(observations, 0)
        self.assertGreater(accepted, observations)

    def test_portfolio_reference_is_direct_and_read_only(self):
        skill = read("SKILL.md")
        portfolio = read("references/portfolio.md")

        self.assertIn("[多项目 Portfolio](references/portfolio.md)", skill)
        self.assert_contains_all(
            portfolio,
            (
                "write_authorized=false",
                "FRESH",
                "STALE",
                "UNKNOWN",
                "AMBIGUOUS",
                "NO_PORTFOLIO_BASELINE",
                "STOP_PROJECT_SWITCH",
                "新任务",
                "独立执行 `pwd`",
                "不继承",
                "不回写",
                "Private",
                "Public",
            ),
        )
        self.assertNotRegex(portfolio, r"\]\([^)]*\.md\)")

    def test_portfolio_cannot_authorize_project_write_or_carry_approval(self):
        text = "\n".join((read("SKILL.md"), read("references/portfolio.md")))

        self.assert_contains_all(
            text,
            (
                "Portfolio",
                "项目审批",
                "写入资格",
                "不能",
                "required_new_task",
                "inherited_approvals",
                "[]",
            ),
        )

    def test_read_scope_matches_validator_path_contract(self):
        text = self.read_adaptive_delegation()

        self.assertIn("`read_scope` 是逐文件的仓库相对 POSIX 路径", text)
        self.assertNotIn(
            "`read_scope` 是逐文件的仓库相对 POSIX 路径或绝对路径",
            text,
        )

    def test_small_or_sequential_task_stays_solo_without_brief(self):
        text = "\n".join(
            (
                read("references/manager-workflow.md"),
                self.read_adaptive_delegation(),
            )
        )

        self.assert_contains_all(
            text,
            (
                "SOLO -> DELEGATION_CANDIDATE -> APPROVED_NATIVE_CALL -> MAIN_THREAD_VERIFY",
                "小任务默认 `SOLO`",
                "强顺序依赖",
                "不创建 brief",
                "收益不足",
            ),
        )

    def test_delegation_batch_budget_and_derived_ids_are_explicit(self):
        text = "\n".join(
            (
                self.read_adaptive_delegation(),
                read("references/safety-gates.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "D-1.2-NATIVE-<001-999>/<01-99>",
                "max_agents",
                "max_turns_per_agent",
                "max_wall_time_minutes",
                "max_model_calls",
                "model_policy",
                "token_budget",
                "一个 D 级批次",
                "派生 `delegation_id`",
                "每次新批准使用未占用的批次 ID",
                "历史批次 ID 不得复用",
            ),
        )

    def test_writer_requires_isolated_worktree_and_single_writer(self):
        text = self.read_adaptive_delegation()

        self.assert_contains_all(
            text,
            (
                "READ_ONLY",
                "REVIEW",
                "ISOLATED_WRITER",
                "独立 linked worktree",
                "一个 worktree 同时只有一个写入者",
                "write_allowlist",
                "write_authorized",
                "false",
            ),
        )

    def test_result_requires_source_return_and_main_thread_freshness(self):
        text = "\n".join(
            (
                self.read_adaptive_delegation(),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "source freshness",
                "return freshness",
                "main-thread freshness",
                "source_baseline_sha256",
                "return_baseline",
                "HEAD",
                "dirty fingerprint",
                "write_eligibility=BLOCKED",
            ),
        )

    def test_external_approval_is_not_inherited_and_nested_delegation_stops(self):
        text = "\n".join(
            (
                self.read_adaptive_delegation(),
                read("references/safety-gates.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "NESTED_DELEGATION",
                "ACCOUNT_OR_CREDENTIAL_ACCESS",
                "NETWORK_OR_EXTERNAL_SERVICE",
                "INSTALL_OR_CODEX_HOME_CHANGE",
                "PUSH_PR_RELEASE",
                "DEPLOYMENT",
                "MAIN_THREAD_DECISION",
                "WRITE_OUTSIDE_ALLOWLIST",
                "FILESYSTEM_OR_GIT_METADATA_WRITE",
                "NESTED_DELEGATION_REQUESTED",
                "不继承",
            ),
        )

    def test_main_thread_verifies_conflicts_consumes_once_and_decides(self):
        text = "\n".join(
            (
                self.read_adaptive_delegation(),
                read("references/acceptance-and-supervision.md"),
            )
        )

        self.assert_contains_all(
            text,
            (
                "RETURNED",
                "STOPPED",
                "FAILED",
                "UNKNOWN",
                "BASELINE_DRIFT",
                "IDENTITY_INCOMPLETE_OR_BLOCKED",
                "ALLOWLIST_EXPANSION_REQUIRED",
                "BUDGET_EXHAUSTED",
                "EXTERNAL_PERMISSION_REQUIRED",
                "UNKNOWN_RESULT",
                "一次消费",
                "需求符合性审查",
                "质量与安全审查",
                "主线程裁决",
            ),
        )

    def test_solo_fallback_does_not_create_a_private_orchestrator(self):
        text = "\n".join(
            (
                read("references/manager-workflow.md"),
                self.read_adaptive_delegation(),
            )
        )

        self.assert_contains_all(
            text,
            (
                "SOLO_FALLBACK",
                "原生能力",
                "能力缺失",
                "固定 Agent 阵容",
                "后台 Agent",
                "Agent 池",
                "多层 Agent 树",
                "私有编排器",
            ),
        )
        self.assertNotIn("创建第二套委派运行时", text)

    def test_binding_reference_contains_fail_closed_states(self):
        text = read("references/project-binding.md")

        self.assert_contains_all(
            text,
            (
                "首命令不变量",
                "BOUND_GIT",
                "BOUND_NON_GIT",
                "STOP_PATH_MISMATCH",
                "STOP_IDENTITY_AMBIGUOUS",
                "STOP_PROJECT_SWITCH",
                "UNKNOWN",
                "dirty_fingerprint",
                "fingerprint_complete",
                "<unborn>",
                "写入前复核",
                "用户修改",
                "项目切换",
                "恢复",
            ),
        )
        self.assertIn("独立执行 `pwd`", text)
        self.assertIn("受保护或高敏感项目", text)

    def test_binding_reference_lists_read_only_git_primitives(self):
        text = read("references/project-binding.md")

        self.assert_contains_all(
            text,
            (
                "git rev-parse --show-toplevel",
                "git rev-parse --path-format=absolute --git-dir",
                "git rev-parse --path-format=absolute --git-common-dir",
                "git symbolic-ref --short -q HEAD",
                "git rev-parse --verify --quiet HEAD",
                "git symbolic-ref -q HEAD",
                "git show-ref --exists",
                "git ls-files --stage -z",
                "git ls-files --others --exclude-standard -z",
                "git ls-tree -rz --full-tree",
                "core.fsmonitor=false",
                "--no-lazy-fetch",
                "GIT_CONFIG_GLOBAL",
                "GIT_*",
                "git remote",
            ),
        )

        self.assertNotIn("\ngit status ", text)
        self.assertNotIn("\ngit diff ", text)

    def test_manager_workflow_contains_intake_and_professional_contract(self):
        text = read("references/manager-workflow.md")

        self.assert_contains_all(
            text,
            (
                "真实业务目标",
                "当前可交付结果",
                "明确非目标",
                "用户能观察的验收标准",
                "需要先调查的事实",
                "按依赖排列的小步工作",
                "每步证据和回滚点",
                "主要风险",
                "下一检查点",
                "只有用户才能决定的事项",
                "需求与产品",
                "项目结构与架构",
                "实现与代码质量",
                "测试、审查与安全",
                "部署与运营",
                "教学与决策支持",
            ),
        )

    def test_manager_workflow_contains_sources_teaching_and_self_check(self):
        text = read("references/manager-workflow.md")

        self.assert_contains_all(
            text,
            (
                "项目事实",
                "本地运行时行为",
                "当前官方文档",
                "一手资料",
                "公开资料搜索",
                "现场事实",
                "来源事实",
                "推断",
                "建议",
                "用户决定",
                "渐进教学",
                "提案",
                "关键假设",
                "实质反例",
                "证据缺口",
                "修订",
                "剩余争议",
                "S0",
                "S1",
                "S2",
            ),
        )

    def test_skill_routing_preserves_attention_and_inventory_cwd_scope(self):
        workflow = read("references/manager-workflow.md")
        index = read("references/SKILL_INDEX_ZH.md")

        self.assertIn("数量是注意力预算，不是硬上限", workflow)
        self.assertIn("不能为了省上下文遗漏强制门或用户明确点名的 Skill", workflow)
        for text in (workflow, index):
            self.assert_contains_all(
                text,
                (
                    "inventory cwd",
                    "不同 cwd 的完整哈希不可直接比较",
                    "相同规范化 inventory cwd",
                    "逐 locator 校验",
                    "UNINDEXED_PROJECT_SKILL",
                ),
            )

    def test_safety_reference_contains_complete_approval_contract(self):
        text = read("references/safety-gates.md")

        self.assert_contains_all(
            text,
            (
                "| A |",
                "| B |",
                "| C |",
                "| D |",
                "| E |",
                "| F |",
                "审批请求 ID",
                "动作",
                "目标",
                "环境",
                "数据范围",
                "凭据范围",
                "调用范围",
                "预期副作用",
                "前置证据",
                "成功判定",
                "失败与 unknown",
                "回滚",
                "请回复",
                "dirty_fingerprint",
                "UNKNOWN",
                "不重试",
                "不换 Provider",
            ),
        )

    def test_acceptance_reference_separates_evidence_and_user_acceptance(self):
        text = read("references/acceptance-and-supervision.md")

        self.assert_contains_all(
            text,
            (
                "EV0",
                "EV1",
                "EV2",
                "需求符合性审查",
                "质量与安全审查",
                "已通过",
                "警告",
                "未验证",
                "未知",
                "用户验收",
                "下一门禁",
                "warning 不并入 PASS",
                "skipped 不冒充 NOT_APPLICABLE",
                "技术验证不代替用户体验",
                "原生恢复推翻条件",
                "NEEDS_REVISION",
            ),
        )


class DeploymentGovernanceSkillTests(unittest.TestCase):
    def test_deployment_reference_is_direct_and_conditionally_routed(self):
        skill = read("SKILL.md")
        path = SKILL_ROOT / "references" / "deployment-governance.md"

        self.assertTrue(path.is_file(), "deployment governance reference is missing")
        self.assertIn(
            "[部署与发布治理](references/deployment-governance.md)",
            skill,
        )
        self.assertRegex(skill, r"部署.*准备度.*发布")

    def test_reference_names_profiles_states_and_decisions(self):
        text = read("references/deployment-governance.md")
        for value in (
            "VERCEL_WEB",
            "LINUX_HOST",
            "DOCKER_SERVICE",
            "PYTHON_SQLITE",
            "FRONTEND_BACKEND_SPLIT",
            "STATIC_SITE",
            "READY",
            "CONDITIONAL",
            "NOT_READY",
            "Go",
            "Conditional Go",
            "No-Go",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_reference_is_read_only_and_separates_approvals(self):
        text = "\n".join((read("SKILL.md"), read("references/deployment-governance.md")))
        for value in (
            "write_authorized=false",
            "默认只读",
            "staging、production 和 rollback",
            "分别批准",
            "不得读取或保存凭据",
            "不得产生外部副作用",
            "不得回写业务项目",
            "Codex 原生主线程",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_reference_requires_explicit_evidence_expiry(self):
        text = read("references/deployment-governance.md")
        self.assertIn("valid_until_utc", text)
        self.assertIn("EVIDENCE_EXPIRED", text)
        self.assertIn("NOT_READY / No-Go", text)

    def test_reference_requires_profile_applicability_and_environment_binding(self):
        text = read("references/deployment-governance.md")
        for value in (
            "profile_applicability",
            "全部 `applicability` 条件为 `true`",
            "全部 `non_applicability` 条件为 `false`",
            "PROFILE_NOT_APPLICABLE / NOT_READY / No-Go",
            "目标环境必须与本轮评估的 `environment` 一致",
            "rollback 是独立动作，不是环境名",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_reference_rejects_future_evidence_and_exposes_references(self):
        text = read("references/deployment-governance.md")
        for value in (
            "不得晚于实际评估时间",
            "EVIDENCE_FUTURE",
            "证据引用",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_reference_requires_closed_evidence_category_contracts(self):
        text = read("references/deployment-governance.md")
        for value in (
            "authentication",
            "tls",
            "migration",
            "backup_recovery",
            "observability",
            "rollback",
            "sqlite",
            "封闭字段",
            "严格布尔值",
            "`conflict=false`",
            "未知字段",
            "NOT_READY / No-Go",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_runtime_package_has_exact_thirteen_files_and_no_deep_reference_links(self):
        actual = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, EXPECTED_FILES)
        self.assertEqual(len(actual), 13)
        self.assertNotRegex(read("references/deployment-governance.md"), r"\]\([^)]*\.md\)")


if __name__ == "__main__":
    unittest.main()
