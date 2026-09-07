# Protocol v2 验收基线

状态：T0 文档基线（`verified`；T0 改动已随 PR60 合并）

日期：2026-09-06

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

<a id="pr2b-local-validation"></a>
### PR2B 实现验证（2026-09-07，verified，尚未合并）

基线为已合并的 PR63 `ca6792592954f05c3aed76a70f4bc0d98126e845`，实现分支
`codex/protocol-v2-pr2b`，本地验证时 tracked 工作区包含本次变更。没有运行真实 provider、
迁移用户数据库、运行 benchmark 或改变默认 V1 工作流。仅在 disposable 数据库中验证：

- 2B.1–2B.2：Run 输入、选中顺序和唯一 report 不可变；阶段更新条件提交。
- 2B.3–2B.5：完整 evidence set/items/pointer 同事务封存；失败无半集合；V2 collector
  使用固定 RepositoryView、整行截断及准确范围/字符数；fake provider 获得封存的逐字段原值。
- 2B.6：请求参数与冻结配置一致，单行 attempt 条件终结；success 与 selected 指针同事务，
  指针失败整体回滚；null 和零回报区分；并发读使用一致快照，LLM 状态从 attempts 派生。
- 2B.7–2B.8：有序 EvidenceLookup 不依赖 normalizer/HTTP；trace 仅接受小型元数据和引用。
- 2B.9–2B.10：owner-only 数据库/目录、显式 create-only 工具、私有迁移 receipt、
  原 source 保持 V1 可写、导入 legacy 副本只读、旧 writer 拒绝 V2；详见
  [隐私与保留政策](protocol-v2-data-protection.md)。

先完成 Store CRUD/失败测试再注册 agent-db CLI。Store/evidence focused 17 passed；
包含 lifecycle、legacy、collector、CLI 和原 migration 回归的集成 focused 初次 98 passed
（Store 的最终一致读回归另行通过）；最终全量 `python -m pytest -q` 为 609 passed、
1 条既有 Starlette 弃用警告。Ruff、格式（改动区域）、compileall、diff 检查通过。
原始测试日志保存在验证环境，不将本机路径或真实源码证据加入公共文档。

代码提交 `1b24cf0` 的 [Python 3.11/3.12 CI](https://github.com/xbz123/repo-issue-intelligence/actions/runs/34162842223)
均为 609 passed、1 条既有警告，Ruff 通过。独立 Astra Standards 与 Spec 双轴审查
覆盖 `ca67925...1b24cf0` 全部差异，分别 0 项违规/阻塞；任务状态为 verified，PR64 尚未合并。
PR3 normalizer、PR4 Agent/G0、恢复/HTTP/review 及 G1 均不由本 PR 提前宣称完成。

<a id="pr2a-local-validation"></a>
### PR2A 本地实现验证记录（2026-09-07，历史初始验证）

PR63 最终已合并为 `ca67925`；以下初始计数保留作为历史证据，最终补修见下节。

PR2A 仅新增独立 migration 模块和 focused tests；没有修改 `AgentStore._initialize`、CLI/API、
默认 V1 路径或任何用户数据库。`inspect_agent_database` 只读区分 empty/legacy0/knownv2/
unknown/corrupt；`backup_agent_database` 使用 SQLite backup、只读 source 与原子 create-only
发布，拒绝已有目标、symlink、缺失 source，并保留 WAL 中已提交数据而不复制 source sidecar。
V2 DDL、索引、触发器和版本写入由一个显式逐语句事务维护；legacy-copy 保留旧三表及原 payload，
不把旧 JSON 转成 V2 attempt。故障点覆盖 DDL/index/version/commit，均在 commit 前校验并完整回滚。
当时独立有界复核已通过且无阻塞项；当时状态为 `verified`，未切换默认 V2。

| 范围 | 命令 | 结果 |
|---|---|---|
| PR2A focused | `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_agent_store_migrations.py` | 退出码 0，10 passed；原始日志已保存 |
| 全量 pytest（最新 guard 后） | `PYTHONPATH=src .venv/bin/python -m pytest -q` | 退出码 0，520 passed，1 warning；原始日志已保存 |
| Ruff | `.venv/bin/ruff check . --no-cache` | 退出码 0，`All checks passed!`；原始日志已保存 |
| Ruff format（新增文件） | `.venv/bin/ruff format --check src/repo_issue_intelligence/agent_store_migrations.py tests/test_agent_store_migrations.py` | 退出码 0，2 files already formatted；原始日志已保存 |
| compileall | `PYTHONPYCACHEPREFIX=<tmp>/pycache .venv/bin/python -m compileall -q src tests` | 退出码 0；原始日志已保存 |
| diff check | `git diff --check` | 退出码 0 |

该初始记录不表示迁移已公开为 CLI/API、默认 V2 已切换或用户数据库已迁移。

#### PR2A 四项失效路径补充（2026-09-07，基线 `b11a812`）

本轮仅补 migration 内核的既有契约，不改变 R5 范围或提前实现 PR7B 服务：

- 2A.4/A46：INSERT 冲突保护覆盖七表的主键、非主键唯一约束及显式 rowid；
  在 recursive_triggers 开/关和重新连接后，REPLACE 均不能覆盖已有记录或重新打开终态。
  UPDATE 的 rowid 也不可变，防止 UPDATE OR REPLACE 隐式删除另一记录。
- §3.7：初始 review_version 必须为零，只随追加 review 递增；回退、无 review 跳增、
  旧基线审查均被拒绝，review 和版本更新同事务回滚。完整审查服务验收仍归 PR7B。
- 2A.1/2A.5：接受 ANALYZE 创建的标准 sqlite_stat1/sqlite_stat4；只允许已知结构，
  畸形统计表及额外用户对象仍拒绝，不泛化放行 sqlite 前缀。
- 2A.2：备份连接前后及发布前检查 source 的设备号、inode 和 ctime；路径替换、
  symlink 替换以及替换后恢复原 inode 均有失败测试，拒绝时不发布目标并清理临时文件。
  打开 canonical source URI 后先开启只读事务固定 SQLite 快照，再检查词法和解析后
  路径的全部父目录身份；覆盖父目录替换恢复及 symlink 隐藏祖先的同类竞态。
  原路径及 canonical 路径均与首次 source 身份比较，覆盖 resolve 期间 symlink 改指后恢复。

本地证据：REPLACE 初始 12 项失败；ANALYZE 初始 3 项失败；源身份替换初始 6 项失败，
补充的替换后恢复场景初始 2 项失败；review_version 回归测试先失败后通过。
首轮 focused 为 50 passed，本地全量为 558 passed；随后 `a4f07f6` 的 Python 3.11/3.12
CI 各为 560 passed、1 条既有 Starlette 弃用警告。独立 Astra Spec 审查又复现了
UPDATE OR REPLACE 及父目录替换恢复两条同类遗漏，均已补回归测试和最小修复。
修订后 focused 为 61 passed，涵盖 WAL 缺失 shm 时拒绝后稳定重试；本地全量重验
571 passed、1 条既有警告，Ruff、format、compileall 和 diff 检查通过。
`9451eb1` 的双版本 CI 各 571 passed。最终复核另发现 canonical 路径未绑定初始身份的
竞态；新增测试先失败，修复后 focused 为 62 passed，静态检查仍通过。最终 CI 与独立
复核以 PR 最新提交为准，不把前一提交的验证结果当作后续修复的完整证据。

边界：这些 guard 改变尚未冻结的 V2 schema；旧 PR2A 临时 V2 库会被严格识别为不匹配，
不会自动原地修补。正常 WAL-only 提交仍可备份；备份期间主文件写入/checkpoint 或元数据
变化会保守拒绝，需要在稳定窗口重试。打开快照时祖先目录发生无关变化也会保守拒绝。
WAL 缺失共享内存索引时，SQLite 首次只读访问可创建 -shm，此时拒绝发布并允许在稳定后
重试；测试确认源 db/WAL 内容不变，重试保留已提交 WAL 数据。
身份检查依赖文件系统可靠的 inode/ctime，
不是对拥有本机文件系统管理权限的攻击者的隔离边界。源库和已有目标均不覆写。

<a id="pr1a-local-validation"></a>
### PR1A 本地验证记录（2026-09-06）

验证在 PR1A 源码树上进行，基线为已合并的 T0 PR60
`0d8f4bdfc448b01b715eea4d983914088f8ae9c6`；验证时工作树/分析范围为 dirty。没有真实
LLM、benchmark 或外部传输。

| 范围 | 命令 | 结果 |
|---|---|---|
| URL/retry-policy/repository-context focused closeout | `.venv/bin/python -m pytest -q tests/test_repository_context.py tests/test_run_configuration.py` | 退出码 0，54 passed |
| 全量 pytest（最终配置、remote、status 与 rename 复核后） | `.venv/bin/python -m pytest -q` | 退出码 0，473 passed，1 warning |
| ruff | `.venv/bin/ruff check .` | 退出码 0，`All checks passed!` |
| compileall | `PYTHONPYCACHEPREFIX=<tmp>/pycache .venv/bin/python -m compileall -q src tests` | 退出码 0 |
| diff check | `git diff --check` | 退出码 0 |

PR1A 1A.1–1A.8 的本地测试已通过：覆盖 scoped Git identity/manifest、固定 commit
tree、rename/deleted/unmerged 边界、remote 去敏、实际导入源码 provenance、
requested/default/omitted 与预算来源（含 direct `BudgetConfiguration` 显式 `None` 回归）、
深冻结输入、统一 aware `as_of`，以及共享 URL 安全校验。URL 安全校验对所有 scheme
拒绝 userinfo（含 percent-encoded userinfo、分离的 CLI option/value 及 scheme-less
authority 形态），对多层 percent-decoded `?/#` 分隔符 fail-closed，同时保留安全
encoded path 的原始拼写；普通非 URL CLI label 仍按既有语义保留。retry policy 仅接受
`backoff` 数字序列、受支持的 `categories` 字符串序列和正整数 `max_attempts`，并在
request/client-default、mapping budget 与 direct `BudgetConfiguration` 入口统一校验。
Repository capture 对同路径 staged tracked 状态保持权威，不让 untracked 替代内容覆盖
删除 manifest；remote 的 host/path 均进行 bounded percent-decode，HTTPS/SSH/SCP 的多层
query、fragment、userinfo 与 `urlsplit` 异常均 fail-closed 且不回显原值。
rename source 在同 scope 或跨 scope 被重建为 untracked 时仍从 tracked manifest 排除，
untracked 事实只保留在独立审计字段。
为覆盖最终配置、remote 与 status 代码复核，全量套件重新执行一次（非机械多轮），结果为
473 passed。Luna 独立审查已通过且无阻塞；是否合并另以 PR 状态为准。这不表示 PR1B 物化、数据库/API、
默认 V2、真实 provider 或 benchmark 已交付。

### PR1B 本地实现记录（2026-09-07，`merged`，`d83f051`）

PR1B 的源码边界已实现，独立有界复核已通过且无阻塞项，并已随 `d83f051` 合并；仍未切换
默认入口。PR1A 已随 `d386dc8` 合并。
`repository_view.py` 提供显式的
`prepare_repository_view(snapshot)`：committed 模式用 captured revision 的 raw blob
物化运行专属临时根，tracked_worktree 模式只复制当前 tracked 常规文件并让删除路径在
视图中缺席。两者均共用 captured manifest；scope 内 LFS pointer、gitlink/submodule、
冲突、external filter，以及绝对/越界/循环/目录/untracked symlink 目标 fail closed，
不运行 hooks、filters、smudge/textconv 或网络 fetch。view 关闭时只清理自己创建的临时根。

`build_repository_map(view)` 与 `collect_evidence(..., repository_view=view)` 共用同一
view/manifest。`run_protocol_v2_investigation(...)` 是纯内存的显式 PR1B 内部入口，只
执行一次 map 构建和多 Issue deterministic investigation/evidence；不创建 AgentRun、
不写 legacy Store、不调用 LLM。history/blame/source-line Git 查询显式绑定 map 的
`git_root`、`captured_revision` 和 `analysis_prefix`；V1 `run_agent`、CLI/API 默认路径
保持原行为。

首次 `capture_repository_context` 对当前 effective Git attributes（含 info/global）批量
检查并拒绝 external filter；committed view 另在不读取当前 overlay 的隔离 Git context
中按 captured tree 检查 `.gitattributes`，deterministic resume 只使用这一固定来源。
`tracked_worktree` 保留当前 attrs 语义。普通反序列化 snapshot 只有在调用方能证明其来自
已接受的 capture 时才可作为恢复输入；当前 PR1B 不提供 Store、resume CLI 或恢复服务。
view 还校验 canonical `git_root`、`analysis_root == git_root/analysis_prefix`、真实 Git
关联及 manifest 路径绑定。source-line/blame 支持 SHA-1/SHA-256 的 40/64 位 OID。

最终本地证据（工具输出未另存日志文件）：受影响测试 `tests/test_repository_context.py`
与 `tests/test_repository_view.py` 为 `57 passed in 21.71s`，退出码 0；全量命令（以
可移植命令名记录；本次在项目 venv 对应环境执行）
`PYTEST_ADDOPTS='-p no:cacheprovider' PYTHONPATH=src python -m pytest -q` 为
`510 passed, 1 warning in 44.79s`，退出码 0；Ruff 命令
`PYTHONPATH=src python -m ruff check --no-cache .` 为 `All checks passed!`，退出码 0；
compileall 退出码 0；最终 `git diff --check` 退出码 0。
此前 `500 passed` 是 filter-triple 修复前的中间证据，不作为最终计数。该记录不表示
G0/G1、V2 Store、resume 或真实 provider 已交付。

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
文档、fixture 和基线已完成本地验证并经独立审查，状态为 `verified`；PR1A 和 PR1B 已合并，
其中 PR1B 合并提交为 `d83f051`。PR2A 已随 PR63 合并为 `ca67925`；PR2B 已验证但尚未合并，
PR3 已在 PR65 验证但未合并；PR4/G0 的分支验收见
[foreground CLI beta](protocol-v2-execution.md)，PR5–PR8、G1 仍为 `planned`，
默认路径仍是 V1。未来 V2 acceptance 的任何空缺、失败或未运行项都必须继续显式列出，不得
用 V1 characterization 代替；当前没有真实 LLM 调用或用户数据库迁移。
