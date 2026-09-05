# Investigation Protocol v2 RFC

状态：T0 契约基线（`verified`；已完成本地验证与独立审查；V2 尚未实现）

日期：2026-09-05

适用计划：[Protocol v2 R5 完整计划](../repo_issue_intelligence_protocol_v2_execution_plan_r5.md)

本文冻结 Protocol v2 第一阶段的最小数据契约、状态语义、服务边界和过渡规则。
它是实现 PR 的契约入口，不是对 R5 计划的逐段复制，也不表示源码、数据库迁移或
V2 命令已经存在。除非另有说明，`MUST`/“必须”表示实现和测试的硬约束。

T0 验证记录见 [protocol-v2-acceptance.md](../protocol-v2-acceptance.md)。验证对象为
`codex/protocol-v2-t0` 分支上的 base `905f90a` 加当前 T0 未提交改动，而不是仅测试
原始 base commit；PR1A–PR8、G0、G1 仍为 `planned`。

## 1. 范围与发布边界

V2 解决四件事：输入 provenance 可追溯、原始 evidence 可查询、每个 Issue 独立提交、
模型失败和恢复可解释。它不改变现有排名、provider 五字段 schema、rank-only hybrid
排序或 benchmark ground truth，也不执行生成的命令、修改目标仓库或自动创建 PR。

T0 只交付本文件、验收基线及对应文档/fixture 契约。实现按 R5 的 12 个 PR 推进；
G0 是专用数据库上的前台 opt-in CLI 门，G1 才允许默认切换到 V2。T0、G0、G1
之间不得把“计划”写成“已实现”。

## 2. 唯一所有权模型

每项数据只能有一个权威所有者；读取 DTO 可以组合字段，但不能再次持久化一份可
独立修改的结果。

| 对象 | 唯一权威内容 | 创建后规则 |
|---|---|---|
| `RepositorySnapshot` | `git_root`、`analysis_root`、`analysis_prefix`、捕获 revision、范围 manifest、dirty/unsupported 事实 | 不可变；不能用当前 checkout 覆盖 |
| `RunConfiguration` | requested backend/provider/model、参数来源、预算/超时/retry、协议和运行时版本 | 不可变；更换配置必须新 run |
| `RunInputs` | 原始 Issue snapshot、ranking `as_of`、排序结果、选中编号和 ordinal | 不可变；resume 不重新 sync/rank |
| `IssueExecution` | 复合身份 `(run_id, issue_number)`、唯一 deterministic report、证据集引用、selected attempt 引用、`review_version` | 每个身份一个不可变 report；不增加 report revision |
| `EvidenceSet`/`EvidenceItem` | 发送前的完整、封存、带顺序的原始证据及截断元数据 | 集合一次原子写入后 seal；retry 复用，不覆盖 |
| `LLMAttempt` | 一次模型调用的 request、唯一 state、analysis/error/reported/local | 一次插入、一次终结、终态不可变；不另建 Result 表 |
| `ReviewRecord` | principal、幂等 key、目标引用、decision/correction 和返回结果 | 只追加；更正追加新记录 |
| `Trace` | 小型诊断摘要和引用 | 不保存完整 map、源码或第二份 outcome |

`IssueExecution` 的 `llm_state`、attempt 次数、当前分析和 run 聚合状态均为读取时
投影，不是独立可写状态。`selected_analysis_attempt_id` 只引用同一 Issue 的成功
attempt，不复制 `analysis_json`。

## 3. 物理关系与外键约束

下面是实现必须满足的列组；具体 DDL 只有 `agent_store_migrations.py` 一个权威来源。
列名可在实现中使用等价的安全命名，但不能改变语义。

```text
agent_v2_runs
  run_id PK
  parent_run_id NULL FK agent_v2_runs(run_id)
  snapshot_json NOT NULL
  configuration_json NOT NULL
  inputs_json NOT NULL
  selection_json NOT NULL
  status NOT NULL
  created_at, updated_at NOT NULL

agent_v2_issues
  (run_id, issue_number) PK, FK agent_v2_runs(run_id)
  deterministic_state NOT NULL
  deterministic_report_json NULL
  evidence_set_id NULL FK agent_v2_evidence_sets(evidence_set_id)
  selected_analysis_attempt_id NULL FK agent_v2_llm_attempts(attempt_id)
  review_version NOT NULL DEFAULT 0

agent_v2_evidence_sets
  evidence_set_id PK
  (run_id, issue_number) FK agent_v2_issues(run_id, issue_number)
  collection_context_json NOT NULL
  sealed NOT NULL CHECK (sealed IN (0, 1))
  sealed_at NULL
  UNIQUE (evidence_set_id, run_id, issue_number)

agent_v2_evidence_items
  (evidence_set_id, evidence_id) PK
  evidence_set_id FK agent_v2_evidence_sets(evidence_set_id)
  ordinal, file, symbol, requested_range, actual_range,
  truncation_reason, char_count, content NOT NULL/nullable as applicable
  UNIQUE (evidence_set_id, ordinal)

agent_v2_llm_attempts
  attempt_id PK
  (run_id, issue_number) FK agent_v2_issues(run_id, issue_number)
  (evidence_set_id, run_id, issue_number)
    FK agent_v2_evidence_sets(evidence_set_id, run_id, issue_number)
  ordinal, request_json, started_at NOT NULL
  state NOT NULL
  finished_at, analysis_json, error_json, reported_json, local_json NULL

agent_v2_reviews
  review_id PK
  (run_id, issue_number) FK agent_v2_issues(run_id, issue_number)
  evidence_set_id NULL FK agent_v2_evidence_sets(evidence_set_id)
  selected_attempt_id NULL FK agent_v2_llm_attempts(attempt_id)
  principal_id, idempotency_key, operation, expected_review_version,
  decision, payload_json, response_json, created_at NOT NULL
  UNIQUE (run_id, issue_number, principal_id, idempotency_key, operation)

agent_v2_traces
  trace_id PK, run_id FK agent_v2_runs(run_id), small payload only
```

外键必须同时校验 run、Issue、evidence set 和 attempt 的归属；不能只用一个全局 ID
让跨 Issue 引用通过。Issue 指向尚未创建的 evidence/attempt 时使用实现支持的延迟
约束或按事务顺序完成绑定，提交后不得存在悬空引用。默认不删除历史 evidence、
attempt、review 或 backup，FK 删除策略不得绕过保留边界。

## 4. Attempt：单行、一次终结、终态只读

### 4.1 字段分组

创建时写入并冻结：`attempt_id`、所属 `(run_id, issue_number)`、`evidence_set_id`、
`ordinal`、requested 配置引用/实际安全序列化参数、`started_at`。请求字段不能被
终结操作、retry 或 review 修改。

终结时最多写一次：`state`、`finished_at`、`analysis_json`、`error_json`、
`reported_json`、`local_json`。`state` 是唯一 outcome；这些 JSON 不再带第二份
`status`/`outcome` 权威字段。`finished_at` 是本地终结或确认中断的时间，不声称远端
模型在该时刻已停止。

允许的 state 只有：`in_progress`、`success`、`failure`、`unknown`。

| state | finished_at | analysis_json | error_json | reported/local |
|---|---|---|---|---|
| `in_progress` | `NULL` | `NULL` | `NULL` | `NULL` |
| `success` | 非 `NULL` | 必须是通过 Analysis V2 校验的分析 | `NULL` | 已观察值或安全观测对象；缺项保持 `NULL` |
| `failure` | 非 `NULL` | `NULL` | 必须为脱敏错误类别/详情 | 保留已知观测；不猜测 |
| `unknown` | 非 `NULL` | `NULL` | 必须说明本地中断/结果不确定 | 只保留可确认的量 |

`reported_json` 与 `local_json` 中的缺失参数必须保持 `NULL`，不能用 requested 值
回填。凭据、完整 provider 原始诊断和任意 secret-bearing URL 不进入这些字段。

### 4.2 开始、终结与数据库保护

请求发出前先提交一次 `in_progress` 插入；同一 `(run_id, issue_number)` 的并发
请求由数据库部分唯一索引拒绝：

```sql
CREATE UNIQUE INDEX one_active_attempt_per_issue
ON agent_v2_llm_attempts(run_id, issue_number)
WHERE state = 'in_progress';
```

所有成功、失败和未知路径都调用同一个条件终结操作；不能分别维护结果表：

```sql
UPDATE agent_v2_llm_attempts
SET state = :terminal_state,
    finished_at = :finished_at,
    analysis_json = :analysis_json,
    error_json = :error_json,
    reported_json = :reported_json,
    local_json = :local_json
WHERE attempt_id = :attempt_id
  AND state = 'in_progress';
```

调用方必须检查 affected-row count 恰为 1。count 为 0 表示已终结或竞争冲突：返回
明确冲突，不改 selected 指针，也不发起第二次模型请求。`success` 与
`IssueExecution.selected_analysis_attempt_id` 的更新在同一短事务内完成并核验
attempt/evidence 所属；指针失败则整次事务回滚。`failure`/`unknown` 不产生成功
指针。迟到 finalizer 不能改旧 attempt、新 attempt 或 selected 指针。

除 `in_progress → success/failure/unknown` 外没有状态转换。实现必须通过 partial
unique index、`CHECK`/`NOT NULL`、条件 `UPDATE` 和必要的触发器或等价写保护共同
拒绝请求字段修改、终态重写和终态重新打开；只检查新值而不检查旧 state 不足以满足
该约束。

单行 attempt 唯一性不是远端 exactly-once 保证。run 编排还必须有一个独立的单执行者
约束（前台 run claim/进程锁/等价机制），避免同一 run 的两个编排器各自推进阶段。
这两层不能互相替代：编排锁保护阶段推进，partial index 保护同一 Issue 的模型调用。

## 5. Evidence 与 Analysis V2 契约

evidence 必须完整写入并 seal 后，才允许调用 provider。发送内容直接从同一 sealed set
读取；客户端不得悄悄重新采样或二次截断。读取适配固定为只读类型：

```python
EvidenceLookup = Mapping[str, EvidenceSnippet]
# 同时传入按重要性排序且唯一的 input_evidence_ids
normalize_analysis_v2(response, input_evidence_ids, evidence_lookup)
```

`normalize_analysis_v2` 是纯函数，不导入 Store、`sqlite3`、认证或 seal 逻辑。provider
继续使用现有五字段 schema；prompt 约定 `hypothesis.evidence_ids` 按重要性排序，
本地取首个 ID 为 primary。所有引用先做唯一性、coverage、归属和存在性检查：
未知 `E999` 或缺失任一输入 ID 必须整体拒绝，不能跳过首个 ID 改取另一个证据。
`affected_component`、validation step 等派生字段从首个 cited ID 在 Mapping 中对应的
真实对象生成。不得新增 provider `primary_evidence_id` 字段，也不得让 rank-only
`EvidenceRerankAnalysis` 依赖本契约。

请求和回报分开保存：

```text
RunConfiguration.requested      # 用户意图、client default 来源、omitted/null
Attempt.request                 # 本次实际安全序列化请求
Attempt.reported                # provider/CLI 实际可观察回报，缺失为 null
Attempt.local                   # elapsed、exit/category、invocation 等本地量
```

requested 与 reported 不同只产生诊断，不自动重发直到“匹配”；reported 缺失不被
requested 伪装成 effective。CLI 只记录其可观察事件，不能猜测私有参数或把 invocation
ID 当作 HTTP response ID。

## 6. 阶段与状态

### 6.1 Issue 阶段

对外分开返回三类状态：

```text
deterministic_state = pending | running | succeeded | failed
llm_state           = disabled | pending | in_progress | succeeded | failed
                      | skipped_no_evidence | interrupted_unknown
review_state        = pending | approved | rejected | needs_information
```

阶段推进是按 Issue 的小步骤保存，而非“有 report 就认为整个 Issue 完成”：

```text
deterministic pending → running → succeeded/failed
succeeded → evidence collecting → sealed/failed
sealed + enabled → attempt in_progress → success/failure/unknown
sealed + no evidence → skipped_no_evidence
reviewable result → review pending → approved/rejected/needs_information
```

如果 deterministic report 已提交但 evidence 尚未 seal，resume 必须继续该 Issue 的
evidence 阶段；如果 evidence 已 seal 但还没有 attempt，必须继续 start；如果 attempt
已 start 但没有终态，必须先处理其不确定性。任何一个中间 checkpoint 都不能因为 report
已存在而跳过整个 Issue，也不能重复已提交的 deterministic 阶段。

### 6.2 Run 聚合状态

V2 合法 run 状态为：`RUNNING`、`INTERRUPTED`、`AWAITING_REVIEW`、
`PARTIALLY_REVIEWED`、`REVIEW_COMPLETED`、`FAILED`。

| 条件 | run 状态和语义 |
|---|---|
| 输入无效或捕获拒绝且尚未创建 run | 返回输入错误；不创建虚假 run |
| 有合法执行者仍处理 | `RUNNING` |
| 已确认本地执行中断，存在未完成/不确定请求 | `INTERRUPTED` |
| 有可审查结果且本轮尚无决定 | `AWAITING_REVIEW` |
| 只有部分可审查 Issue 已有决定 | `PARTIALLY_REVIEWED` |
| 全部可审查 Issue 已有本轮决定 | `REVIEW_COMPLETED` |
| 数据库、共享 map/context、冻结输入或未预期程序错误；或所有 deterministic 均失败且无可用调查 | `FAILED`，但已提交 sibling 保留可读 |

`REVIEW_COMPLETED` 不表示业务 Issue 已解决。V2 不把 mixed review 压成 run-level
`approved`/`rejected`；旧 V1 投影只有在全部可审查项同一决定时才可给出兼容状态，
否则返回显式 mixed 状态。

## 7. 异常分类与失败隔离

| 类别 | 处理 | 是否创建/终结 attempt |
|---|---|---|
| `INVALID_INPUT`、不支持的初次捕获 | fail closed；返回 4xx/输入错误，不创建 run | 否 |
| `NO_EVIDENCE`、evidence collector 的 Issue-local 缺失 | 该 Issue 标 `skipped_no_evidence` 或 local failed；不调用 provider；其他 Issue 继续 | 无 provider attempt，或终结已有 attempt 为 failure |
| provider auth/quota/schema/invalid response、可重试 transport | 隔离到该 Issue；保留 deterministic/report/evidence；按单 Issue policy 产生 failure attempt | 是（若已 start） |
| timeout/断网且本地无法知道是否已 dispatch | 不伪装为普通 failure；保持 `in_progress` 直到确认本地停止，之后才可终结 `unknown` | 是 |
| CLI 传输未获当次许可、HTTP principal/scope/provider 授权失败 | 在 start 前拒绝，不发送 evidence | 否 |
| 数据库/FK/事务错误、共享 map/context 错误、冻结输入损坏、未预期程序异常 | run-level fatal，状态 `FAILED`；不吞异常；已提交 sibling 保留可读 | 视发生阶段；不可伪造成功 |

“Issue-local”只适用于不会污染共享上下文、数据库和其他 Issue 的错误。不能用
`except Exception: continue` 把程序 bug 降级为 provider failure。错误详情必须脱敏，
分类、发生阶段、attempt ID 和可观察 telemetry 足够用于恢复。

## 8. Provenance、源码视图与恢复

### 8.1 committed 模式

首次 committed capture 必须检查 analysis scope 内 tracked 内容 clean、无冲突且
输入类型受支持，然后绑定 captured commit 的 tree。`git_root` 的范围外 dirty 和
untracked 事实单独记录；untracked decoy 不进入 manifest。LFS pointer、gitlink/
submodule、未解决 index 冲突、需要外部 filter 的 scope 内输入、越界/循环/目录 symlink
均明确拒绝。视图按 raw blob 物化，不执行 hooks、smudge/textconv、安装脚本或网络
fetch。

首次捕获一旦成功，resume 的身份依据是保存的 commit、analysis prefix、representation
和 manifest；它从原 commit 重建运行视图和一次 map。resume **不得**用当前 checkout
再次做 clean 检查来证明原输入未变，也不得把当前 HEAD、当前未跟踪文件或当前工作区
字节混入运行。原 checkout 后续变更不改变 committed evidence；首次捕获若不 clean
则拒绝创建该运行，而不是事后用 resume 修补。

### 8.2 tracked_worktree 模式

这是显式的本地开发模式，只记录 best-effort/current-content，不宣称内容可由 commit
重现。其 deterministic resume 第一版必须拒绝；已有 sealed evidence 可在同配置下做
纯 LLM retry。不能用 mtime/size/PID 不存在冒充内容或执行已停止的证明。

### 8.3 中断与 unknown

run 编排单执行者与 attempt partial unique index 是两层约束。父进程退出不等于 CLI
子进程退出：在把遗留 attempt 终结为 `unknown` 或允许 `--recover-unknown` 前，必须
确认本地执行链（包含 CLI 子进程/进程组）已停止。无法证明时保持 `in_progress` 并拒绝
恢复。远端是否已完成仍可能未知，协议不承诺 exactly-once。

以下阶段继续规则是固定的：

| checkpoint | resume 动作 |
|---|---|
| deterministic report 已提交、evidence 未 seal | 在原 Issue 继续 collect/seal |
| evidence 已 seal、无 attempt | 继续原 Issue 的 start |
| attempt `in_progress` 且本地执行已确认停止 | 终结为 `unknown`；旧请求不重写 |
| attempt `in_progress` 且本地仍活跃/无法确认 | 拒绝 recover，等待操作者处理 |
| 已终结 `unknown` | 默认不重发；显式 `--recover-unknown` 且已确认本地停止后，新建不同 attempt |
| committed run 已提交的 deterministic Issue | 跳过已提交阶段，但继续其尚未完成的后续阶段 |

未知请求绝不自动重发。`--recover-unknown` 是同一 `agent-retry-llm` 命令的显式风险
授权，不把 unknown 改写成 failure，也不增加第三个 `agent-recover` 命令。

## 9. 服务接口与边界

接口名冻结如下；它们不是要求在 T0 立即实现的公共 API。

| 接口 | 责任和依赖边界 |
|---|---|
| `capture_repository_context(root, mode)` | 校验 Git/analysis scope，返回 snapshot + manifest |
| `capture_requested_run_configuration(...)` | 只记录 requested/default/omitted 和安全版本；不读取凭据值 |
| `prepare_repository_view(snapshot)` | 按固定 commit/scope 建立运行视图并管理生命周期 |
| `create_run(snapshot, configuration, inputs, selection)` | 一次性保存 immutable run 上下文 |
| `save_deterministic_result(run_id, issue, report)` | 按阶段保存唯一 report，不把 review_version 当执行锁 |
| `seal_evidence_set(...)` | 一个事务写全 evidence items 后 seal |
| `start_attempt(...)` | 短事务插入 `in_progress`，由唯一 active index 取得调用资格 |
| `finalize_attempt(attempt_id, terminal_fields)` | 条件终结、检查 rowcount；成功时同事务更新 selected 指针 |
| `normalize_analysis_v2(...)` | 纯内存引用校验/primary 派生，无 Store/sqlite/auth |
| `process_issue(run_context, issue_id)` | 顺序编排单 Issue，隔离 provider 错误，不吞 fatal |
| `resume_agent_run(run_id)` | 只按冻结 committed 输入继续未完成阶段 |
| `retry_issue_llm(run_id, issue_id, recover_unknown=False)` | 复用冻结 config/evidence；不读取当前 checkout |
| `require_cli_external_transfer(...)` | CLI 当次许可，默认拒绝真实外部传输 |
| `authorize_repository_operation(principal, scope, operation)` | HTTP 当前认证主体/范围/provider 操作授权 |
| `submit_issue_review(...)` | 认证→幂等重放→目标/version/active 校验→原子追加 review |

CLI 在调用应用服务前检查当次 provider/endpoint/analysis scope 的外部传输许可；
默认关闭，G0 仍仅支持前台、专用 V2 DB、单写、opt-in。HTTP 在调用同一应用服务前
必须完成 loopback/token、服务端 principal、analysis scope allowlist 和当前 provider
策略授权；不信任请求 body 中的 reviewer/principal，不接收任意 base URL。应用服务不
解析 CLI flag、不导入 HTTP 安全模块，PR5B 不依赖 PR6/PR7。

冻结命令边界：

```text
agent-run <issues> --protocol v2
agent-show <run-id> --protocol v2
agent-resume <run-id> --protocol v2
agent-retry-llm <run-id> --issue <number> --protocol v2
agent-retry-llm <run-id> --issue <number> --protocol v2 --recover-unknown
```

不新增 `agent-recover`。HTTP V2 的读取/审查/retry endpoint 在其安全 PR 完成前不
宣称可用；健康检查不等于敏感资源授权。

## 10. Review、幂等与 mixed 状态

Review 绑定现有 `(run_id, issue_number)`、当时的 `evidence_set_id`、
`selected_attempt_id`（无 LLM 成功时为 `NULL`）和 `expected_review_version`，不绑定
未定义的 report revision。写入顺序固定为：认证 principal 和资源授权 → 查找
`(run, Issue, principal, idempotency_key, operation)` → 同 key 同规范化 payload 返回
原响应 → 同 key 异 payload 返回 `409 IDEMPOTENCY_PAYLOAD_MISMATCH` → 新 key 比较
version/目标引用/无 active attempt → 一个事务追加 review 并递增 version。

principal 与 key 独立：共享 token 不能伪装多人身份；token 不进入 payload/key。权限
撤销后，即使是同 key 重放也必须重新鉴权并拒绝。新 key + 最新 version 可追加更正，
原记录不覆盖。`approved`、`rejected`、`needs_information` 是 Issue 本轮决定；run
聚合只报告全量、部分、mixed 或待审查，不能在 mixed 时生成批准/拒绝的假象。

## 11. legacy 0 迁移与默认切换矩阵

识别 legacy 时必须同时检查 `PRAGMA user_version`、`sqlite_master` 的表/列/FK/索引。
`user_version=0` 不是空库判据；已有三张旧表的库仍是 legacy source。迁移采用
SQLite backup 复制到**不同且不存在的** destination，再在 destination 上显式事务升级。
源库保持版本、表和 V1 写入；V2 中导入的历史副本只读。不存在同库双写、增量镜像、
自动覆盖目标或默认启动时偷偷 apply migration。

| 时点 | legacy source | V2 destination | 默认路径 |
|---|---|---|---|
| T0、PR1A/1B、PR2A、PR3 | 正常 V1 读写 | 未启用（测试临时库除外） | V1 |
| PR2B | 不改版本、继续 V1 写入 | 仅显式 create/migrate 到新目标 | V1 |
| PR4 + G0 | 继续 V1 | `--protocol v2` 专用库；历史副本只读 | V1，V2 CLI opt-in |
| PR5A–PR7B | 继续 V1 | 增加受控 resume/retry/HTTP/review | 仍未切换 |
| PR8 | 不影响 | current-result 独立目录 | 仍未切换 |
| G1 | 停止默认 V1 写入，源库保留只读 | 选定一个明确 V2 正式目标 | 默认 V2 |

新配置、model、prompt 或 budget 不在原 run 上 update，而是创建带 `parent_run_id`
的新 run。迁移失败必须回滚 destination 且源库可读；未知版本或非目标结构 fail
closed。旧 JSON/旧读取器可通过只读 adapter 读取，但“可读”不等于允许匿名旧 API
继续写入 V2。

## 12. T0 场景索引与验收入口

T0 fixture 和测试使用以下契约场景 key；已有 V1 baseline manifest 的 canonical
lower-snake-case IDs 作为括号别名保留，不要求重命名 fixture。实现可以在测试文件中
增加更细断言，但不改变语义。场景与计划验收矩阵的对应关系见
[protocol-v2-acceptance.md](../protocol-v2-acceptance.md)。

| 场景 | 必须表达的契约 | 计划验收 |
|---|---|---|
| `T0-F1` (`t0_5_legacy_schema0_fixture`) | legacy `user_version=0`，含 success/failure/snapshot/review，可由 V1 读回 | A01、A33 |
| `T0-F2` | deterministic report 已提交、evidence 未 seal；resume 继续同一 Issue | A13、A14、A22、A32 |
| `T0-F3` | evidence sealed 无 attempt；或 attempt 无终态；按阶段继续 | A14、A15、A24、A44 |
| `T0-F4` (`t0_6_multi_issue_provider_failure`) | A/B/C：A、C 成功各一次，B provider failure + 局部 retry，三份 report 可查 | A13、A22、A24 |
| `T0-F5` | start/start、finalize/finalize、late finalize、selected pointer rollback | A17、A18、A24、A44、A47 |
| `T0-F6` | unknown 前确认 parent 与 CLI 子进程均停止；无确认不 recover/不发送 | A15、A43、A48 |
| `T0-F7` (`t0_6_clean_tracked`, `t0_6_official_demo`, `t0_6_untracked_decoy`) | committed 初次 clean；捕获后 checkout 变化/decoy 不影响原 commit resume | A03、A06、A07、A27、A31 |
| `T0-F8` | tracked_worktree deterministic resume 拒绝；sealed evidence retry 可行 | A31 |
| `T0-F9` (`t0_6_e7_primary`) | E7 primary 映射为 E7；E999 首位整体 fail closed | A09、A10、A36 |
| `T0-F10` | requested=A、reported=B 分开保存；缺 metadata 为 null | A16、A37 |
| `T0-F11` | pending/partial/mixed/all review 聚合不冒充批准/拒绝 | A23、A38、A39、A50 |
| `T0-F12` | scope 内 LFS/gitlink/submodule/conflict/filter/symlink 明确拒绝 | A08、A41 |
| `T0-F13` | source 继续 V1 writable，destination create-only，无双写，默认仅 G1 切换 | A01、A33–A35、A42、A45 |

每个实现 PR 的测试、状态和限制以计划为准；本 RFC 的 T0 状态已在本地验证和独立审查
完成后标为 `verified`，但不能据此标为 `merged` 或宣称 V2 已实现。

## 13. 参考与非目标

- 详细任务依赖、PR 归属和 A01–A53 的完整定义：R5 计划 §4–§25。
- 当前任务索引只保存 ID/依赖/status/链接：
  [R5 checklist](../repo_issue_intelligence_protocol_v2_task_checklist_r5.md)。
- 当前 V1 runtime ownership 的实现参照：`src/repo_issue_intelligence/agent_store.py`、
  `agent_workflow.py`、`llm_client.py`、`models.py`、`api.py`。
- T0 不新增文件/产物哈希校验；Git commit OID 仅作为原生 Git revision identity，
  不是本协议的内容校验或幂等 key。
