# Protocol v2 验收基线

状态：T0 文档基线（`verified`）

日期：2026-09-05

RFC：[investigation-protocol-v2.md](rfcs/investigation-protocol-v2.md)

计划：[R5 完整计划](repo_issue_intelligence_protocol_v2_execution_plan_r5.md)

本文件把当前可执行的 V1 characterization 与未来 V2 acceptance 分开。下面出现的
“未运行”“未来”均是有意的状态，不是测试通过的替代说法；T0 不运行真实 provider、
不发送外部数据、不迁移用户数据库。

## 1. 状态约定

| 标签 | 含义 |
|---|---|
| `characterization` | 在当前基线可执行，用来记录 V1 既有行为；不证明 V2 |
| `future-v2` | 需要相应 PR/G0/G1 后才能执行；当前不声称通过 |
| `in_progress` | T0 文档/fixture/基线正在实现或等待独立审查 |
| `verified` | 对应范围已实际验证，并完成独立审查；不表示已合并或 V2 已实现 |
| `merged` | 对应改动已合并；不能由 `verified` 自动推断 |

验收结果至少记录 checkout commit、branch、tracked/analysis-scope dirty、Python/依赖
版本、命令退出码、产物路径和测试状态。不得用 SHA256 或其他新增内容哈希替代 Git
revision、结构化字段和行为断言。

## 2. 当前可执行的 V1 characterization

这些 characterization 只描述当前 V1 入口和历史行为，不能把旧实现的通过解释为 V2
能力。它们可以在隔离临时目录/数据库运行，不需要真实模型。

| ID | 目的 | 精确命令/入口 | 期望观察 |
|---|---|---|---|
| `V1-C1` | 既有单元/API/CLI 基线 | `uv run pytest -q tests/test_agent_workflow.py tests/test_agent_evaluation.py tests/test_api.py tests/test_cli.py` | V1 workflow、API 和 CLI 测试按当前基线完成；记录实际退出码 |
| `V1-C2` | evidence 与 provider contract 的严格校验 | `uv run pytest -q tests/test_evidence.py tests/test_llm_client.py tests/test_codex_cli.py` | 既有 evidence-ID、schema、CLI error 分类仍有效；不放宽 malformed output |
| `V1-C3` | legacy AgentStore round-trip | `uv run pytest -q tests/test_agent_workflow.py tests/test_api.py` | V1 `agent-run/show/review` 可读写临时 legacy DB；不要求 `user_version` 迁移 |
| `V1-C4` | 离线 demo characterization | `uv run rii rank examples/issues.json --output <tmp>/ranked.json`；`uv run rii index examples/demo_repository --output <tmp>/repository-map.json`；`uv run rii investigate-issue examples/issues.json --issue 184 --repo examples/demo_repository --output <tmp>/issue-184.json` | 三个输出可解析；不调用 LLM、不修改 demo 仓库 |
| `V1-C5` | 默认入口不切 V2 | `uv run pytest -q tests/test_protocol_v2_baseline.py` | baseline 测试只验证 V1/default path 和 T0 fixture 预期；由实际测试输出决定状态 |

若环境没有 `uv`，应使用仓库已有的等价环境命令并记录替代原因；不能把“命令启动”
当作通过。`V1-C5` 使用仓库中的 `tests/test_protocol_v2_baseline.py`；文件或其依赖
缺失时该条保持未运行，不创建 fake pass。

最小静态门：

```bash
uv run ruff check .
git diff --check
```

本节的命令是 characterization 入口，不是 V2 acceptance 命令；真实输出应在独立
review/协调记录中登记。

当前 T0 characterization 记录（2026-09-05，CPython 3.11.5，
`uv sync --frozen --extra dev`）：

测试对象是 `codex/protocol-v2-t0` 分支上的 base `905f90a` 加当前 T0 未提交改动；
运行时 tracked/analysis-scope 为 dirty。记录如下：

- `UV_CACHE_DIR=<tmp>/uv-cache uv run pytest -q`：退出码 `0`，`417 passed`，
  `20.80s`；有 2 条环境/弃用 warning。
- `uv run ruff check .`：退出码 `0`，`All checks passed!`。
- 普通 `python -m compileall -q src tests` 因默认 `__pycache__` 权限退出码 `1`；
  `PYTHONPYCACHEPREFIX=<tmp>/pycache .venv/bin/python -m compileall -q src tests`：
  退出码 `0`。
- `git diff --check`：退出码 `0`。
- SQLite integrity/FK/readback/recreate、JSON 结构、文档链接、以及离线 demo 的 3 个
  JSON 输出：均通过。
- 手工重新生成 legacy fixture：通过；自动测试尚未把 generator 输出与已提交 fixture
  做逐项比对。

以上证据支持 T0 文档、fixture 和 V1 characterization baseline 的 `verified` 状态；
不证明任何 V2 Store、迁移、resume、HTTP、review/retry 或 G0/G1 门已通过。非阻塞盲区
包括 A/B/C 场景的部分 `v1_observed` 字段尚未逐项断言；未来实现 PR 仍须补齐对应
acceptance。

## 3. T0 fixture 场景与 V2 gate 映射

T0 场景 ID 定义在 [RFC §12](rfcs/investigation-protocol-v2.md#12-t0-场景索引与验收入口)。
现有 V1 fixture 的 lower-snake-case IDs 是 canonical 名称，RFC 中的 `T0-F*` 是契约
别名；fixture manifest 和测试必须保持这些语义，不必重命名已有 artifact。映射表只说明
未来要覆盖的验收范围，不表示任何 A 门已通过。

| T0 场景 | 契约焦点 | 未来计划门 |
|---|---|---|
| `T0-F1` / `t0_5_legacy_schema0_fixture` | legacy0 读回、旧 snapshot/review 保留 | A01、A33 |
| `T0-F2`/`T0-F3` | report/evidence/attempt 分阶段继续 | A13–A15、A22、A24、A32、A44 |
| `T0-F4` / `t0_6_multi_issue_provider_failure` | per-Issue failure isolation、成功 sibling 保留 | A13、A22、A24 |
| `T0-F5` | active unique、conditional finalize、late finalize、pointer rollback | A17、A18、A24、A44、A47 |
| `T0-F6` | 本地执行链停止确认、unknown 不自动重发 | A15、A43、A48 |
| `T0-F7` / `t0_6_clean_tracked`, `t0_6_official_demo`, `t0_6_untracked_decoy`; `T0-F8` | committed revision 重建与 tracked_worktree 拒绝 resume | A03、A06–A08、A27、A31、A41 |
| `T0-F9` / `t0_6_e7_primary`; `T0-F10` | Mapping primary、strict evidence、requested/reported/local | A09、A10、A16、A36、A37 |
| `T0-F11` | mixed/partial review 与幂等目标绑定 | A23、A38–A40、A50 |
| `T0-F12` | unsupported source input fail closed | A08、A41 |
| `T0-F13` | destination migration、无双写、G1 默认切换 | A01、A33–A35、A42、A45 |

## 4. 未来 V2 acceptance（当前未实现、未运行）

R5 计划 §20 的完整 A01–A53 矩阵是唯一验收定义，本文件不复制其测试正文。下面只
提供责任分组，便于实现者找到未来测试和 T0 场景；每一行当前状态均为 `future-v2`。

| 责任分组 | 计划验收 ID | 依赖/执行时点 | 当前状态 |
|---|---|---|---|
| legacy schema、事务回滚、唯一 DDL、显式 destination | A01–A02、A33–A35、A46–A47 | PR2A/PR2B；需真实 SQL/FK/rollback | `future-v2` |
| Git scope、captured revision、tracked-only、特殊输入拒绝 | A03–A08、A27、A31、A41 | PR1A/PR1B；需 clean/decoy/symlink/LFS 等 fixture | `future-v2` |
| evidence seal、Mapping、primary、引用覆盖 | A09–A12、A24、A36 | PR2B/PR3/PR4；不调用真实模型也可测试 | `future-v2` |
| Issue 隔离、阶段 resume、unknown、执行权与 attempt | A13–A18、A22、A32、A42–A44、A48 | PR4/G0/PR5A/PR5B；含跨进程和 CLI 子进程场景 | `future-v2` |
| API principal、allowlist、evidence 读取、撤销 | A19–A21、A30、A40 | PR6/PR7A/PR7B；需认证/授权测试 | `future-v2` |
| mixed review、幂等、不可变目标 | A23、A38–A39、A49–A50 | PR7B；需 review/start 与 review/retry 竞争测试 | `future-v2` |
| map 不重复、legacy adapter、rank-only 回归 | A25–A26、A28 | PR4/PR3/PR7A/G1；不得改变既有 ground truth | `future-v2` |
| current-result、任务归属、标题/来源结构 | A29、A51–A53 | PR8/G1；需目录和链接检查 | `future-v2` |

未来模块级命令由各 PR 的 Definition of Done 决定，例如：

```text
uv run pytest -q tests/test_protocol_v2_baseline.py     # T0/V1 baseline
uv run pytest -q tests/test_agent_store_migrations.py   # PR2A
uv run pytest -q tests/test_agent_store_v2.py tests/test_evidence_ledger.py  # PR2B
uv run pytest -q tests/test_issue_execution.py tests/test_agent_workflow.py  # PR4
uv run pytest -q tests/test_execution_claims.py tests/test_agent_resume.py   # PR5A/5B
uv run pytest -q tests/test_api_security.py tests/test_review_service.py     # PR6/7B
```

这些命令只在对应测试和实现进入当前基线后执行；在 T0 文档中列出不等于已执行或已
通过。完整门禁仍需 focused tests → 集成/API/CLI/store → G1 deterministic regression，
并按计划记录未验证项。

## 5. 必须保留的边界检查

验收记录必须明确检查下列容易误判的情况：

1. **阶段恢复**：deterministic report 已提交但 evidence/LLM 尚未完成时，继续同一
   Issue 的下一阶段；不因 report 存在而跳过整 Issue。
2. **两层执行互斥**：run 编排单执行者和 `(run_id, issue_number)` 的
   `in_progress` 唯一索引分别测试；一层不能代替另一层。
3. **unknown**：父进程退出不证明 CLI 子进程已停；必须确认本地执行链停止后才可
   终结 unknown 或使用 `--recover-unknown`，且未知请求默认不自动重发。
4. **committed provenance**：首次 capture 要求 scope clean；后续 resume 从保存的
   commit/prefix/manifest 重建，不使用当前 checkout clean 检查或当前字节。
5. **mixed review**：partial/mixed/all review 状态不能投影成错误的 run-level approve/
   reject；review 使用 principal、idempotency key 和 expected version。
6. **legacy 过渡**：source legacy0 保持 V1 可写；V2 destination create-only；没有
   默认切换或同 run 双写，G1 前默认路径仍是 V1。

## 6. T0 当前结论

本文件和 RFC 已冻结执行契约，baseline artifact 和测试输出按实际运行结果记录；T0
文档、fixture 和基线已完成本地验证并经独立审查，状态为 `verified`。PR1A–PR8、G0、
G1 仍为 `planned`，默认路径仍是 V1。未来 V2 acceptance 的任何空缺、失败或未运行项
都必须继续显式列出，不得用 V1 characterization 代替；当前没有真实 LLM 调用或用户
数据库迁移。
