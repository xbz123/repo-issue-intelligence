# repo-issue-intelligence：Protocol v2 完整实施计划

版本：R5。日期：2026-09-05。状态：实施设计待确认；全部实现任务均为 planned，文档修订不代表 T0、G0 或 G1 已通过。

本文是功能定义、依赖、验收和设计约束的唯一来源；配套 checklist 只保存任务 ID、依赖定位、状态及本文链接。目标仓库位置为 `docs/repo_issue_intelligence_protocol_v2_execution_plan_r5.md` 与同目录 checklist。此处仅提供文档交付，不表示文件已提交或合并进仓库。

源码参照基线：`2a7282cd4b1e9118df2864b8e595aa4d09081dcc`。可核验的仓库内来源见第 25 节；本计划不以聊天附件、未归档旧草案或个人机器路径作为实施必需来源。所有新表、接口、命令和门槛均为待实现设计。

## 本次修订登记

| 问题 | 确定修订 | 责任范围 |
|---|---|---|
| Attempt 与独立 Result 表重复拥有终态 | 合并为一个 attempt 行；state 是唯一 outcome，结果、错误和 telemetry 均在同一行一次性终结 | D03/D09、§3.4、PR2A/2B/4/5A |
| PR5B 要求尚未实现的 HTTP 鉴权 | PR4 先交付最小 CLI 传输确认；PR5B 仅复用它；HTTP principal/allowlist/撤销检查在 PR7B 集成 | D20、4.10、5B.8、6.7、7B.7 |
| PR5A 提前测试 review/start | PR5A 只测 attempt 并发和终结；review/retry/start 的原子资格检查及竞争测试移到 PR7B | 5A.7、7B.5 |
| 未定义的 report revision | 每个 Issue 一个不可变 report；审查绑定现有 IssueExecution 身份、evidence_set_id 与 selected_attempt_id | §3.7、7B.2 |
| 数据集角色更正被推迟 | 将原任务 F1.1 前移到第一阶段 PR8；新 holdout 仍在第二阶段 F1.2–F1.7 | PR8、F1 |
| checklist 复制详细实施和验收 | 缩为四列任务索引；依赖列链接本文所属工作包，不重复维护依赖定义 | §23.3、配套 checklist |
| 标题及来源不可稳定审计 | 修正为 23.1/23.2；移除不可用附件和旧草案来源依赖，使用仓库相对路径及基线 commit | §23、§25 |

153 个任务 ID 全部保留，不新增实现 PR。F1.1 改归 PR8 后，第一阶段为 122 项、第二阶段为 31 项。保留 12 个实现 PR、T0、G0、G1 以及第二阶段 F1–F6，共 21 个工作包/门节点。计数表示计划范围，不表示实现完成。

单表方案的准确术语是 **insert-on-start / finalize-once / terminal-immutable**：请求创建后不可变，终态只允许补全一次，完成历史不可改写。它不是每个数据库写操作都严格 INSERT-only 的事件账本；ReviewRecord 仍是只追加记录。

## 1. 交付目标与范围

第一阶段交付 Protocol v2：可追溯的输入、可查询的原始证据、逐 Issue 提交、模型失败隔离、可解释的恢复行为、受保护的审查接口、单一当前结果目录。

保留八个工作包，但将高风险包拆为 **12 个独立实现 PR**：PR1A、PR1B、PR2A、PR2B、PR3、PR4、PR5A、PR5B、PR6、PR7A、PR7B、PR8。另有 T0 设计基线、G0 opt-in CLI 验收、G1 正式发布验收。G0 的最小 CLI 实现在 PR4 内完成，不新增一个大型实现 PR。

第一阶段不改检索评分、Top-20/40 容量、rank-only hybrid 排序契约或既有 benchmark ground truth。不引入 ORM、通用事件溯源框架、后台 worker、自动执行仓库命令、共享 repository-map cache、多租户或 SSO。

第二阶段另立任务处理拒答、复现计划真实性、独立评测、检索重构、概率校准、优先级和重复聚类。不得把这些延后能力标成 Protocol v2 已修复。

## 2. 本版确定的设计决策（T0 落库与验收）

| 编号 | 决策 |
|---|---|
| D01 | Provider 保留当前五字段 schema。Prompt 规定 hypothesis.evidence_ids 按重要性排序。全部 ID 校验通过后，本地取首个 ID 为 primary。 |
| D02 | provider_schema_version、prompt_version、analysis_protocol_version 分别记录，不因为 schema 未变就视为协议未变。 |
| D03 | DeterministicReportV2 不保存 llm_analysis。成功分析仅存于 agent_v2_llm_attempts.analysis_json；IssueExecution.selected_analysis_attempt_id 只是同 Issue 成功 attempt 的引用，不保存结果副本。 |
| D04 | RepositorySnapshot 描述代码输入，RunConfiguration 描述分析方法，RunInputs 描述原始 Issue、评分时刻和选中顺序，三者创建后不可变。 |
| D05 | 区分 git_root、analysis_root、analysis_prefix、运行期 materialized_root。对外路径相对 analysis_root，Git 查询使用 git_root + captured revision + prefix。 |
| D06 | committed 默认 tracked-only，并使用固定 revision 的运行专属源码视图；tracked_worktree 是显式本地模式，不承诺仅凭 SHA 重现。 |
| D07 | 第一版 tracked_worktree 不支持跨进程 deterministic resume；已保存 evidence 的纯 LLM retry 可支持。 |
| D08 | Evidence 必须在请求前完整提交，重试复用相同 evidence set。ID 在 run + Issue + evidence set 中解释。 |
| D09 | 每次应用层模型调用仅占一个 attempt 行。请求字段不可变；state 是唯一 outcome，in_progress→success/failure/unknown 时与结果/错误/telemetry 同事务一次补全，终态不可覆盖；无独立结果表。backend 不可观察的量保留 unknown，不承诺远端 exactly-once。 |
| D10 | 普通 provider 错误隔离到 Issue；共享上下文、数据库和未预期程序错误不得吞掉。 |
| D11 | 第一版 API 限可信 loopback，并加本地 token、服务端 principal、allowlist 和外部传输策略；不把共享 token 当独立人类身份。 |
| D12 | PR2A 不改变 legacy 0 的读写。V2 使用独立目标库；显式迁移后的历史副本只读，原 legacy 源库在正式切换前仍由 V1 使用。G1 才停止默认 V1 写入；没有同一运行的双写。 |
| D13 | 第一版不自动删除历史 evidence、模型结果和运行产物；权限、保留范围和人工清理规程必须文档化。 |
| D14 | 不新增 SHA256 或其他内容哈希做文件/产物校验或幂等 key；Git object identity 仅作为原生 Git 协议身份。 |
| D15 | normalize_analysis_v2 只依赖已验证请求的有序 ID 和 EvidenceLookup=Mapping[str, EvidenceSnippet]。它不导入 Store，不开 SQLite，不负责 seal/认证；编排保证输入来自本次请求。 |
| D16 | RunConfiguration 保存请求意图与客户端默认值；attempt 的终态字段保存规范化分析或本地错误，以及 reported/local 可观测量。reported 缺失为 null，不由 requested 回填；这些 JSON 不再包含第二份 outcome/status。 |
| D17 | principal 表示主体，idempotency_key 表示一次审查请求，expected_review_version 表示审查基线。三者独立；同主体可用新 key 和最新版本追加更正。 |
| D18 | G0 仅 opt-in CLI、独立 V2 DB、前台单进程；不开放 V2 HTTP 或手动恢复。G1 才默认切换，PR8 不阻塞 G0。 |
| D19 | 所有 V2 DDL、约束和升级逻辑只有 agent_store_migrations.py 一个权威来源；Store 只验证、调用，不复制 CREATE/ALTER 语句。 |
| D20 | CLI 传输确认由 PR4 交付，PR5B 仅核对冻结输入/请求配置和当前 CLI 传输确认；HTTP 身份、allowlist、权限撤销和 review/start 集成属于 PR7B，不成为 PR5B/PR5A 的隐含依赖。 |
| D21 | IssueExecution 身份沿用唯一键 (run_id, issue_number)，文中的 issue_execution_id 是这一复合身份的领域名称；每个身份只保存一个不可变 deterministic report，不新增 report_revision。 |

### 2.1 输入冻结的精确定义

`git ls-files` 是文件范围工具，不是历史内容冻结工具。committed 模式的捕获点必须绑定 commit 的树，不能在冻结后继续读取会改变的用户 index/checkout。

PR1B 采用范围受控的 revision 物化视图：只物化 analysis_prefix 内允许的 tracked 内容，按固定 commit 读取；不运行仓库 hooks、外部 filters、安装脚本或 lazy network fetch。运行期间索引和 evidence 共用该视图。Git history/blame 查询显式绑定 captured revision，不依赖后来变化的 HEAD。

Git worktree 可用于开发任务隔离，但 worktree lock 只保护生命周期，不能当成文件不可写或内容已冻结的证明。运行视图不是恶意同权限本机进程的安全沙箱；MVP 信任本机操作者。

首版源码视图策略在本版已确定，T0 只落库和补测试，不再留作“以后选择”：

- committed 从 captured commit 的 tree 列举 analysis_prefix 下文件，原始 blob 读取后物化；不使用 checkout/smudge/textconv，不启用 lazy fetch，不执行仓库脚本。
- analysis scope 内出现 gitlink/submodule、LFS pointer、未解决 index 冲突，或者需要外部 filter 转换的 tracked 输入，捕获直接拒绝，返回结构化原因；分析范围外的这些文件不影响子目录任务。
- LFS 不下载、不把 pointer 当代码；submodule 不递归；filter 只作只读检查、不执行。属性/配置无法可靠判定时拒绝，而非假装已支持。
- symlink 只允许最终解析至同一分析范围内、同一 captured manifest 中的 tracked 常规文件；拒绝目录链接、循环、绝对/越界目标和 untracked 目标。未支持平台语义也明确拒绝。
- committed 范围有 tracked 修改/删除时按 clean 要求拒绝；tracked_worktree 可显式记录已删除 tracked 路径并在本地视图中缺席，不能从 HEAD 补回；两模式均拒绝冲突和上述不支持输入。
- raw blob 与工作目录 CRLF/编码/过滤表示有差异时，只比较同一 representation 的排序，不宣称对任意 clean checkout 都完全零变化。

范围枚举、物化和后续 evidence 必须共用一个 manifest；临时根目录在 run 结束后按运行所有权清理，审计用的封存 evidence 保留。不存在新增内容哈希校验。

### 2.2 恢复与重试

| 操作 | 输入 | 第一版规则 |
|---|---|---|
| committed deterministic resume | 固定 revision、原始 Issue、原始配置 | 重建同一范围视图并重新构建一次 map；跳过已提交 Issue |
| tracked_worktree deterministic resume | 无法由 SHA 证明的工作目录内容 | 拒绝，要求新运行 |
| 明确失败的 LLM retry | 已持久化 Issue/evidence/config | 不读取当前目标源码、不重新排名 |
| 更换模型/prompt/budget | 新配置 | 创建新 run，记录 parent_run_id，不覆盖旧 run |
| 已成功或已审查分析的再分析 | 新分析尝试目的 | 创建新 run；第一版不原地替换已审查结果 |
| 请求可能已发送但无终态 | 不确定远端状态 | interrupted_unknown；默认不重发；确认本地执行已终止并显式 --recover-unknown 后才新建 attempt，旧终态保留 |

## 3. 目标数据模型与唯一所有权

```text
Run
  immutable RepositorySnapshot
  immutable RunConfiguration
  immutable RunInputs + frozen selection
  |
  +-- IssueExecution
        deterministic_report
        sealed EvidenceSet -> EvidenceItem[]
        LLMAttempt[] (请求不可变，单行一次终结，终态只读)
        selected_analysis_attempt_id
        ReviewRecord[]
```

### 3.1 RepositorySnapshot

至少记录：git_root、analysis_root、analysis_prefix、commit_oid、branch、detached、analysis_scope_dirty、git_root_dirty、untracked_in_scope_count、mode、remote_selection_source、normalized_remote_identity、repository_identity（可空）、captured file manifest、input representation、capture time、unsupported/skipped paths。

clean 要求以分析范围的 tracked 内容为准。Git root 其他目录的改动另行记录，不应无意阻止官方子目录 demo。untracked 文件不进入输入，但数量和忽略事实可以记录。未解决合并冲突、无法解析的范围或缺失必要 Git 对象必须在捕获阶段失败。

### 3.2 RunConfiguration 与 AttemptObservation：请求/回报分离

RunConfiguration 是不可变的**客户端请求配置**，不是服务端 effective 配置。最少分成：

| 位置 | 字段/含义 |
|---|---|
| RunConfiguration.client | backend、provider 标识、安全规范化 endpoint、HTTP/CLI 运行版本 |
| RunConfiguration.requested | requested_model、requested_parameters（reasoning/tier/temperature/seed 等）、output/evidence budgets、timeout、retry policy |
| RunConfiguration.parameter_origins | 字段来源 user_config/client_default/omitted；缺省未发送和显式 null 必须可区分 |
| RunConfiguration.protocol | provider_schema/prompt/analysis/retrieval/index 版本，top_k 与 selection protocol |
| RunConfiguration.engine | importlib.metadata 包版本、实际导入源码 revision/dirty、Python/runtime；unknown 明确记录 |
| LLMAttempt.request | config 引用、实际序列化的安全请求参数或 CLI flags、Issue/evidence_set 引用、attempt ordinal、started_at |
| LLMAttempt.reported | 同一行 reported_json：reported_model、reported_service_tier、reported_parameters、response_id、provider_request_id、system_fingerprint、reported_usage；无回报为 null |
| LLMAttempt.local | 同一行 local_json：local_elapsed_ms、CLI exit/category、invocation 标识及远端完成是否可知等本地观测；不含第二份 attempt outcome |

模型回报与请求不同，保留两者并产生 `reported_model_differs_from_requested` 诊断（缺回报则 null）；不能仅凭名称差异断言底层物理路由，也不能自动重发“直到匹配”。默认不改变已有成功判定，若将来需要严格模型匹配策略应单独显式配置。

响应里没有 temperature、seed、tier 等字段时保持 unknown/null，不能把请求值复制到 reported。HTTP response `id`、HTTP request ID 和 CLI invocation/thread ID 不是可无条件互换的概念；按接口可验证的含义分别记录。

API 与 Codex backend 必须共同遵守；CLI 只回报本地事件能看见的值，不解析私有凭据或猜测未暴露的模型参数。历史 `LLMAnalysisResult.model=self.model` 只能标为 legacy/requested 来源，不能被迁移成 reported_model。

实际引擎源码必须与记录一致；editable install 不能记另一个 checkout 的 HEAD。unknown 或 dirty 引擎第一版拒绝严格跨进程恢复，但可以显式开发运行。凭据值不进入 config；凭据轮换不是算法变更。

恢复比较的是冻结的 requested 配置、实际可观察的客户端/runtime、输入与协议；绝不要求未来 reported fingerprint 与过去相同。Base URL 禁止 userinfo/敏感 query；secret 独立配置，避免审计地址与实际请求地址不一致。

### 3.3 RunInputs

冻结完整输入 Issue snapshots、ranking 的 as_of 时刻、优先级结果、选中 Issue 的编号和 ordinal、selection protocol。恢复时不重新抓取 GitHub、不使用当前时间重算排序、不因重新排序改变执行对象。

### 3.4 V2 表与 attempt 单一终态

| 表 | 唯一权威内容 |
|---|---|
| agent_v2_runs | 不可变 snapshot/config/inputs、parent_run_id、全局控制信息；不保存完整 map |
| agent_v2_issues | 唯一 deterministic report、deterministic 执行状态、evidence_set_id/selected_analysis_attempt_id 引用、review_version；不复制 attempt outcome 或 analysis |
| agent_v2_evidence_sets | 集合身份、所属 Issue/snapshot/budget/collector protocol、seal 状态 |
| agent_v2_evidence_items | 请求的原始片段、顺序、文件/symbol/实际范围与截断信息 |
| agent_v2_llm_attempts | 每次调用一行：不可变请求事实、唯一 state、可空 analysis/error/reported/local 字段、开始和本地终结时间；唯一 active-attempt 约束 |
| agent_v2_reviews | 只追加的 decision/correction、principal/key/payload、所审查目标引用、审查版本与原返回结果 |
| agent_v2_traces | 小型诊断摘要和引用，不重复源码、map 或 attempt 终态副本 |

不另建结果表。对外 DTO 可以把 analysis/error/reported/local 组织成嵌套对象，但不能把该 DTO 再保存为第二份 outcome 或分析内容。下游始终从 attempt 行投影读取。

#### 3.4.1 Attempt 字段分组

- 创建后不可变：attempt_id、所属 (run_id, issue_number)、evidence_set_id、ordinal、requested 配置引用/实际安全请求参数、started_at。
- 仅终结一次的字段：state、finished_at、analysis_json、error_json、reported_json、local_json。
- `state` 唯一允许值为 in_progress/success/failure/unknown；analysis_json 和 error_json 不再保存另一个 success/error/status 标志。
- `finished_at` 表示本地终结或确认中断分类的时刻，不声称远端模型在该时刻停止。
- 请求、analysis、error 与 telemetry 均不回填未知参数；缺 usage 与真实零 usage 保持可区分。

#### 3.4.2 合法字段组合

| state | finished_at | analysis_json | error_json | reported/local |
|---|---|---|---|---|
| in_progress | NULL | NULL | NULL | 终态字段尚未补全，NULL |
| success | 非 NULL | 必须是通过 Analysis V2 校验的非 NULL 分析 | NULL | 已知值或含 null 的安全观测对象 |
| failure | 非 NULL | NULL | 非 NULL 的脱敏错误类别/详情 | 保留已知回报与本地观测，不猜测 |
| unknown | 非 NULL | NULL | 非 NULL 的本地中断/结果不确定原因 | 只保存可确认的量 |

这些字段组合由 migration 模块中的 CHECK/NOT NULL 约束和 Store 的模型验证共同保障。迁移模块中的最小写保护负责拒绝请求字段变更、非 in_progress 的终态更新和从终态重新打开；Store 不暴露任意 UPDATE/DELETE。不要用仅校验新值的 CHECK 冒充 OLD→NEW 转换保护。T0 固定具体约束，PR2A/PR2B 用直接 SQL 和服务测试验证。

#### 3.4.3 一次终结与 selected 指针

所有结果路径调用同一个 Store 终结操作，使用参数化条件更新。以下只表达核心语义，完整 DDL 仍在 PR2A 实施：

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

在同一短事务中检查 affected-row count 必须为 1。若为 0，读取并返回明确的已终结/冲突结果，不更新 Issue 指针，更不发起另一个模型请求。仅 success 可将同一 Issue 的 selected_analysis_attempt_id 指向该 attempt；必须核验 attempt 与当前 evidence set 的归属，再同事务提交。failure/unknown 不产生 selected 成功指针。指针提交失败则整次本地事务回滚。

```sql
CREATE UNIQUE INDEX one_active_attempt_per_issue
ON agent_v2_llm_attempts(run_id, issue_number)
WHERE state = 'in_progress';
```

已终结 unknown 的 attempt 永远保留 unknown；显式重试新增不同 attempt_id。迟到的旧回调匹配不到 in_progress，不能修改新 attempt 或 selected 指针。没有重新引入 owner token/generation。

#### 3.4.4 派生视图不成为第二事实源

Issue 的 `llm_state`、attempt 次数、当前分析以及 run 统计均为读取时派生：没有调用时由 llm_enabled/evidence/stage 区分 disabled、pending、skipped_no_evidence；有调用时从最新 attempt.state 映射 succeeded/failed/interrupted_unknown。数据库不再保存另一份可独立修改的 Issue 级 LLM outcome。selected_analysis_attempt_id 是显式引用，不是复制的结果状态。

attempt 是“创建时插入、只终结一次、终态不可变”，不是严格 INSERT-only；review 才是每次决定均插入新记录。DDL 仅由 migration 模块维护，PR2A 无默认迁移，PR2B 仅在明确目标库激活。

### 3.5 迁移过渡与默认路径

| 合并/发布时点 | legacy 源库 | V2 目标库 | 默认执行 |
|---|---|---|---|
| T0 / PR1A / PR1B / PR2A / PR3 | 维持 V1 正常读写 | 未启用，测试临时库除外 | V1；新源码/分析入口尚不自动切换 |
| PR2B | 原 source 不改版本和表，不停止其 V1 写入 | 显式创建空 V2 库，或 backup 到新 destination 后迁移副本 | 仍 V1；仅存储工具可用 |
| PR4 + G0 | 继续 V1；不得与 V2 同一路径共写 | --protocol v2 指向专用 DB，历史副本只读 | 默认 V1；V2 CLI opt-in |
| PR5–PR7 | 同上，受新的 API 安全策略约束 | 增加受控恢复/查询/review | 未经 G1 不改默认协议 |
| G1 | 正式切换前停止旧运行、备份，源库保留只读 | 选择明确的 V2 库作为新运行目标 | 默认 V2，legacy 只读适配 |

第一版迁移命令以 create-only destination 为默认且唯一支持的公开升级路径：源库保持不变，先用 SQLite backup 复制到新目标，再在目标上事务升级。不存在复制完成后继续同步源库的双写或增量镜像。复制出的历史记录标明来源及迁移时刻；V1 尚在运行的条目不会变成可恢复的 V2 IssueExecution。

若用户尚未迁移，只使用新空 V2 beta 库，也完全支持。G1 不自动合并 beta 库和 legacy 库；选定正式目标，其他库保留可读。将 legacy 写入器错误指向 V2 库时，新版本程序必须在建表/写入前拒绝并给出正确命令；不支持旧二进制绕过此检查共写 V2 库。

该过渡策略继续保留：PR2A 可以独立合并；PR2B 可以独立交付存储工具；PR4 后才开放可运行的 V2，任何前置 PR 都不提前掐断 V1。

### 3.6 状态规则

Issue 对外分开呈现 deterministic_state、llm_state、review_state。deterministic_state 随本地阶段保存；llm_state 从配置/evidence/attempt 派生；review_state 从审查记录派生，避免重复终态权威。

- deterministic_state：pending/running/succeeded/failed。
- llm_state：disabled/pending/in_progress/succeeded/failed/skipped_no_evidence/interrupted_unknown。
- review_state：pending/approved/rejected/needs_information。

run 的对外状态按全局故障、执行活动和审查进度聚合：

| 场景 | 状态 |
|---|---|
| 输入非法，尚未创建运行 | 返回输入错误，不创建虚假 run |
| 全局基础设施/数据库/程序错误，或所有 deterministic 均失败且无可用调查 | FAILED；此前已提交结果保留可读 |
| 当前有合法执行权并仍在处理 | RUNNING |
| 已确认执行进程中断，存在未完成或不确定请求 | INTERRUPTED |
| 执行结束，有可审查调查，尚无审查结论 | AWAITING_REVIEW |
| 一部分可审查调查已审查 | PARTIALLY_REVIEWED |
| 全部可审查调查具有本轮决定 | REVIEW_COMPLETED |

所有 LLM 失败但 deterministic 成功仍可审查。确定性的“无足够证据”结果可审查为 needs_information；真正 deterministic 执行失败不冒充可用分析。执行错误计数单独展示，REVIEW_COMPLETED 不代表业务 Issue 已解决。

### 3.7 审查目标与权限边界

`issue_execution_id` 沿用现有唯一键 `(run_id, issue_number)`，不是新增 UUID 或 report revision。该身份下 deterministic report 写入成功后不可变；若要用新代码或配置重新调查，创建新 run。

每条审查绑定这个复合身份、`evidence_set_id` 和 `selected_attempt_id`（即提交时看到的 selected_analysis_attempt_id）。没有 LLM 成功时 selected_attempt_id 为 null；未形成证据集时 evidence_set_id 可为 null，但必须与当时实际状态相符。提交时比较目标引用、expected_review_version 和无 active attempt。`review_version` 仅在追加审查决定时递增，不用于 attempt owner，也不要求 PR5A 实现 review 服务。

| 功能 | 首次实现 | 集成/测试责任 |
|---|---|---|
| 当前 CLI 是否允许发原 evidence 到所选 provider | PR4 的最小本地确认，默认拒绝真实外部传输 | PR5B 复用与测试；不导入 API 鉴权模块 |
| HTTP principal、token、资源 allowlist 与当前策略 | PR6 | PR7B 在调用 retry 服务前重新鉴权/授权，包括 recover-unknown |
| start/start、finalize/finalize、迟到 finalize | PR5A | 仅 attempt/Store 层，不调用 review 服务 |
| review/start、review/retry、撤销权限后的 HTTP retry | PR7B | 安全链与恢复链汇合后完成真实并发和端点测试 |

PR6 可复用已有配置输入，但不得把 PR4 的简单 CLI 确认替换为要求 HTTP principal 的实现，使 PR5B 产生逆向依赖。CLI 入口先做当前发送确认，HTTP 入口先做当前认证/授权，再调用同一个只管冻结输入、请求配置和 attempt 的应用服务；应用服务不解析 CLI flag，也不导入 HTTP 安全模块。HTTP 边界的认证与授权不可因复用该服务而省略。

## 4. PR 总览、可合并状态与门槛

| PR/门 | 目标 | 依赖 | 合并后的可用范围 |
|---|---|---|---|
| T0 | 修订 RFC、接口、真实 fixture | 无 | 默认行为不变 |
| PR1A | requested 配置、仓库身份、固定输入模型 | T0 | 辅助能力可测；默认仍 V1 |
| PR1B | 首版固定源码视图与 opt-in 接入 | PR1A | V2 内部 I/O 就绪；默认仍 V1 |
| PR2A | DB 识别、dry-run、backup、唯一 migration 实现与测试 | T0 | 不改 AgentStore 默认初始化，不开放 apply |
| PR2B | V2 Store/ledger 与显式目标库 migration 入口 | PR2A、PR1A | 新库/迁移副本可 CRUD；不宣称 Agent runtime 可运行 |
| PR3 | 纯内存 normalizer 与 requested/reported 适配 | T0 | 不依赖 SQLite；V2 分析契约单测就绪 |
| PR4 | 单 Issue 工作流与最小 --protocol v2 CLI | PR1B、PR2B、PR3 | V2 前台 CLI 可纵向执行 |
| G0 | opt-in CLI beta 集成门 | PR4 | 验证通过后发布 CLI beta；不要求 HTTP/resume/PR8 |
| PR5A | attempt_id/unique active/条件更新 | G0 | 跨进程请求互斥；不开放 unknown 自动恢复 |
| PR5B | agent-resume、agent-retry-llm --recover-unknown | PR5A | 同配置受控恢复；无第三个 recover 命令 |
| PR6 | loopback/token/principal/allowlist/传输 | G0、PR1A | API 安全就绪，V2 HTTP 仍由 PR7 接入 |
| PR7A | 受保护查询与 V1 读取投影 | PR6、G0 | V2 查询 API 与完整 CLI 展示 |
| PR7B | per-Issue review、幂等与 retry HTTP 入口 | PR5B、PR7A | 受保护的完整本地 V2 交互 |
| PR8 | current-result 目录与一致性 | T0 | 独立交付；不阻塞 G0 |
| G1 | 正式验收、迁移切换和发布 | PR1A、PR1B、PR2A、PR2B、PR3、PR4、G0、PR5A、PR5B、PR6、PR7A、PR7B、PR8 | 默认 V2；legacy 正式只读 |

执行 DAG（“+”表示所有前置均满足）：

```text
T0 → PR1A → PR1B
T0 → PR2A；PR2A + PR1A → PR2B
T0 → PR3
PR1B + PR2B + PR3 → PR4 → G0
G0 → PR5A → PR5B
G0 + PR1A → PR6 → PR7A
PR5B + PR7A → PR7B
T0 → PR8（独立，不阻塞 G0）
全部第一阶段 PR + G0（包含 PR8）→ G1
```

PR1A、PR2A、PR3、PR8 可在 T0 后并行，避免共享文件区域。PR2B 提供 `Mapping[str, EvidenceSnippet]` 的数据库读取适配，不要求 PR3 已合并；二者在 PR4 集成。G0 之后再开始并发恢复与 V2 HTTP 实现，安全设计本身可在 T0 预先冻结。

<a id="t0"></a>

## 5. T0：RFC 与验收基线

工作包前置：无。

主要落点：docs/rfcs/investigation-protocol-v2.md、docs/protocol-v2-acceptance.md、tests/fixtures/。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-t0-1"></a>T0.1 范围冻结 | 采用本版 D01–D21；固定 12 PR、G0/G1；数据集角色任务 F1.1 归 PR8，其余 F1→F4 保持第二阶段 | 默认路径直到 G1 不切换；任务归属无重复 |
| <a id="task-t0-2"></a>T0.2 所有权冻结 | 固定输入/report/evidence/review 所有权；attempt.state 是唯一 outcome，analysis/error/telemetry 同行；Issue 的 LLM 状态只作投影 | 表定义不存在独立结果表或 Issue 终态副本；合法字段组合已定 |
| <a id="task-t0-3"></a>T0.3 状态与异常 | 写全部合法转换、状态优先级、预期 Issue 错误与 fatal 异常分类 | 状态表测试用例清单 |
| <a id="task-t0-4"></a>T0.4 兼容与过渡矩阵 | 写清原 legacy 源库可写、V2 历史副本只读、两库不共写及默认 V1→G1 V2 时点 | PR2A 单独合并仍能 V1 agent-run；不提前禁写 |
| <a id="task-t0-5"></a>T0.5 真实 fixture | 用旧实现生成含 success/failure/review/snapshot 的小型 synthetic legacy DB；不得含用户源码/凭据 | user_version=0，fixture 可由旧模型读回 |
| <a id="task-t0-6"></a>T0.6 回归基线 | 保存 clean tracked、官方 demo、untracked decoy、E7、多 Issue 失败基线；记录源码环境 | 机器可读预期，不把计划当已运行结果 |
| <a id="task-t0-7"></a>T0.7 接口与文档契约 | 冻结 Mapping、requested/reported、独立 key、特殊源码拒绝规则，以及 CLI/HTTP 分层接口；详细任务仅在 plan，checklist 只链接 | PR3 无 Store、PR5B 无 HTTP 鉴权依赖；任务链接唯一；来源使用仓库路径 |

不改默认执行路径，不改 benchmark ground truth。T0 完成后方可独立实现持久化契约。

<a id="pr1a"></a>

## 6. PR1A：仓库身份与完整 RunConfiguration

工作包前置：T0。

主要落点：repository_context.py（新增）、run_configuration.py（新增）、protocol_v2_models.py（新增）、scoring.py/service.py（仅传入冻结 as_of）、__init__.py 的版本兼容入口。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-1a-1"></a>1A.1 路径解析 | 解析 git_root/analysis_root/prefix，校验 prefix 无路径穿越、支持空格和 Unicode | demo 子目录正确；错误路径 fail closed |
| <a id="task-1a-2"></a>1A.2 Git 身份 | 捕获 commit、branch、detached、范围 dirty、root dirty、untracked；区分 `.git` 文件和目录 | 普通 checkout、linked worktree、detached 都可识别 |
| <a id="task-1a-3"></a>1A.3 范围文件 | 使用 NUL 分隔 tracked 路径，统一转相对 analysis_root；区分 deleted、unmerged、symlink、submodule | untracked 不进入；删除/冲突有明确结果 |
| <a id="task-1a-4"></a>1A.4 Remote | 显式 remote 优先，其次 origin；无明确唯一来源则 identity unknown，不猜；规范化 SSH/HTTPS | 无 origin、多 remote、userinfo、非 GitHub hosts 测试 |
| <a id="task-1a-5"></a>1A.5 版本来源 | 使用 importlib.metadata，并记录实际导入引擎源码 revision、dirty、Python/runtime | editable 安装不误记另一个 checkout；unknown 有明确行为 |
| <a id="task-1a-6"></a>1A.6 客户端请求配置 | 导出 requested_model、request_parameters、client-default 来源与 omitted 字段，保存安全 CLI flags/预算/版本；不生成 effective 字段 | 未回报服务端参数绝不由请求值回填；配置不含凭据 |
| <a id="task-1a-7"></a>1A.7 冻结输入与选择 | 保存输入 Issue、as_of、ranked results、selected ordinals；恢复不调用 sync/rank | 固定时刻排序可重放；未选 Issue 的排序结果可审计 |
| <a id="task-1a-8"></a>1A.8 兼容且不开默认 | 旧 AgentRun 增带默认值的兼容字段或外层适配；版本来源修正，V2 capture 仅内部/显式启用 | 历史 JSON 可读，默认 V1 执行与写入保持可用 |

验收：tests/test_repository_context.py、tests/test_run_configuration.py、相关 scoring/CLI 单测。该 PR 不迁移数据库，不开放 API，不引入共享 cache。

<a id="pr1b"></a>

## 7. PR1B：固定代码读取视图与真实执行接入

工作包前置：PR1A。

主要落点：repository_view.py（新增）、repository_index.py、investigator.py 中源码/历史 I/O 边界、evidence.py 的读取入口、agent_workflow.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-1b-1"></a>1B.1 committed 视图 | 从 captured revision 生成运行专属、仅含授权 prefix 内容的受控物化视图；索引与 evidence 共用 | 捕获后修改原 checkout 不改变本次 evidence |
| <a id="task-1b-2"></a>1B.2 首版拒绝策略 | raw blob 物化；分析范围内 LFS pointer、gitlink/submodule、冲突、外部 filter 输入明确拒绝；不执行 hooks/filter/网络 | 逐类 rejected reason 测试；不支持不是静默跳过或读取 pointer |
| <a id="task-1b-3"></a>1B.3 子目录 I/O | 外部展示 relative-to-analysis；Git 查询转 prefix 路径；history/blame 显式 commit | demo 不扩大范围；不依赖变动的 HEAD |
| <a id="task-1b-4"></a>1B.4 symlink 边界 | 仅允许同范围同 manifest 的 tracked 常规文件作为最终目标；拒绝目录链接、循环、越界和 untracked 目标 | 允许的内链可读；每个拒绝路径有 fixture |
| <a id="task-1b-5"></a>1B.5 tracked_worktree | 只捕获 tracked 文件的本地视图，记录 best-effort/current-content；不宣称原子仓库快照 | dirty 模式显式开启；不得与 committed cache 身份混用 |
| <a id="task-1b-6"></a>1B.6 map 生命周期 | 当前进程只构建一次；不迁移 benchmark 私有 cache 为共享服务 | 多 Issue 共用一次 map；无新增缓存服务 |
| <a id="task-1b-7"></a>1B.7 仅 V2 接入 | 新视图通过明确的 V2 内部入口接入，默认 agent-run/investigate-issue/API 保留 V1；G1 才切默认 | PR1B 单独合并不触发迁移和旧入口行为变化 |
| <a id="task-1b-8"></a>1B.8 回归对比 | 对同 revision/scope/representation 比 candidate order、symbols、metrics；路径环境字段单独比较 | clean tracked 等价；decoy 消失记为预期差异 |

说明：这一步允许 I/O 边界调整，但禁止改评分、阈值、reservation、graph 种子或 rank-only prompt。遇到输入表示不同的仓库不能虚报“所有 clean 仓库绝对零变化”。

<a id="pr2a"></a>

## 8. PR2A：迁移准备、唯一 DDL 与测试（零默认副作用）

工作包前置：T0。

主要落点：agent_store_migrations.py（新增）、只读 inspect/dry-run 工具、tests/test_agent_store_migrations.py、真实 synthetic legacy DB fixture。**不修改 AgentStore._initialize() 的默认行为，不停止 legacy 写入，不开放正式 apply 命令。**

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-2a-1"></a>2A.1 数据库识别 | 只读检查 user_version、sqlite_master、关键列/FK/索引，区分空库、legacy 0、已知 V2、未知 | BenchmarkStore/随机库/部分损坏库不被误迁移 |
| <a id="task-2a-2"></a>2A.2 检查和备份 | 提供 inspect/dry-run/SQLite backup 能力；源库只读，destination create-only | 不创建或修改源库；备份能按旧模型读回 |
| <a id="task-2a-3"></a>2A.3 迁移内核测试 | 实现显式 BEGIN/COMMIT/ROLLBACK 内核，但生产入口不调用，只在 disposable fixture 上测试 | 迁移失败完整回滚；默认命令不触发内核 |
| <a id="task-2a-4"></a>2A.4 唯一 DDL 来源 | migration 模块定义七张 V2 表、单表 attempt 约束、终态/请求写保护、FK 和唯一 active 索引；Store 不复制 DDL | 空库与 legacy-copy 升级 schema 相同；不存在独立结果表 |
| <a id="task-2a-5"></a>2A.5 完整性验证 | 全部结构/FK 检查成功后才设置 user_version=2；未知版本 fail closed | 幂等运行与损坏 fixture 的诊断准确 |
| <a id="task-2a-6"></a>2A.6 原路径保活 | 保持现有 AgentStore 构造、save_run、review、snapshot 行为；任何 apply 均不可被默认初始化触发 | PR2A 合并后旧 CLI/API 仍能完整创建、保存、审查 |
| <a id="task-2a-7"></a>2A.7 故障注入 | 在 DDL、索引、版本写入、提交前逐项注入故障 | 源 fixture 完整，目标回滚，无半成功升级 |

PR2A 的迁移实现是供 PR2B 激活的库能力，不是“合并即升级”。Python 3.11 的 executescript 会先提交已有事务，不能直接沿用现有初始化方式声称迁移原子化；显式事务和逐语句 DDL 需要真实测试。

完成条件：迁移准备工具独立可用、旧功能完全可用；用户未作显式迁移时，任何默认命令都不改变数据库版本。

<a id="pr2b"></a>

## 9. PR2B：V2 Store、EvidenceLedger、attempt repositories

工作包前置：PR2A、PR1A。

主要落点：agent_store_v2.py、protocol_v2_models.py、legacy_projection.py（只读）、agent-db 显式工具入口、tests/test_agent_store_v2.py、tests/test_evidence_ledger.py。V2 Store 不继承会默认建 legacy 表的旧构造路径。

公开存储入口随本 PR 开放：inspect、create-v2、migrate --source <legacy> --destination <new-v2-db>。以上均为待实现命令；source 与 destination 必须是不同路径和文件身份，目标存在则拒绝，不覆盖现有 beta 数据库。Store 本身只验证或显式调用 migration 模块，不包含 DDL。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-2b-1"></a>2B.1 Run repositories | 创建不可变 snapshot/config/inputs，保存 selected order；禁止 update 修改输入 | 配置变更必须新 run |
| <a id="task-2b-2"></a>2B.2 Issue repositories | 沿用 (run_id, issue_number) 唯一身份；唯一不可变 report、evidence/selected attempt 引用和 review_version；LLM outcome 从 attempts 派生 | 无重复 analysis/outcome；report 不覆写；review_version 不作执行锁 |
| <a id="task-2b-3"></a>2B.3 Evidence 原子写入 | 一个事务插入 evidence set 和 items 后 seal；ID、ordinal、Issue 绑定唯一 | 部分写失败无可见半集合 |
| <a id="task-2b-4"></a>2B.4 Evidence 元数据 | 保存 candidate rank、selection_kind、requested/actual range、truncation 原因、字符数、collector protocol | metadata 不声称包含实际被截掉行；V2 输出优先完整行截断 |
| <a id="task-2b-5"></a>2B.5 原始输入一致性 | LLM 发送内容直接由 sealed set 读取；不在客户端再截断或重新采样而不记录 | 存储内容与 fake provider 收到内容逐字段一致 |
| <a id="task-2b-6"></a>2B.6 单行 Attempt 存取 | 请求前插入 in_progress；仅一次条件 UPDATE 同行 state/analysis/error/reported/local/finished_at；成功与 selected 指针同事务；请求字段冻结 | affected rows=0 不改指针；非法字段组合/请求修改/终态改写被拒；缺回报 null |
| <a id="task-2b-7"></a>2B.7 EvidenceLookup 适配 | 将 sealed evidence 读取为有序 IDs + Mapping[str, EvidenceSnippet]；legacy 无 evidence 返回 unavailable | PR3 使用内存映射也能运行；normalizer 不导入 sqlite3/Store |
| <a id="task-2b-8"></a>2B.8 小型 trace | 新 trace 仅摘要/引用；map 与 source content 不写 trace | 大 map 不出现在 V2 snapshots/payload |
| <a id="task-2b-9"></a>2B.9 本地数据保护 | 限制数据库/备份/证据目录权限，日志脱敏；禁止自动删除历史记录 | 权限/日志测试；隐私与保留政策文档 |
| <a id="task-2b-10"></a>2B.10 显式迁移激活 | 开放独立新库创建与 source→新 destination 复制升级，导入 legacy 副本只读；旧入口识别 V2 时先拒绝误写 | 原 legacy 源库仍可 V1 运行；V2 target 可 CRUD；无自动迁移、同库双写或 DDL 副本 |

该 PR 不切换默认 Agent 工作流，不开放 Evidence API，不自动读取旧大 snapshot 做数据回填。PR2B 交付的是可用 V2 Store 和显式迁移工具，不冒称尚未合并的 V2 Agent 能运行。

实现顺序：先 Store/schema 校验与全部 CRUD 测试，再开放 2B.10 入口；测试可提前调用 migration 内核，但生产命令不得暴露半成品。原 legacy 源库保持原版本和原写入；只读规则作用于迁入 V2 目标库的历史副本。

<a id="pr3"></a>

## 10. PR3：本地 Analysis V2 与兼容语义

工作包前置：T0。

主要落点：llm_client.py、codex_cli.py、analysis_contract.py（可选新增）、tests/test_llm_client.py、tests/test_codex_cli.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-3-1"></a>3.1 Prompt 约定 | 首个 cited evidence 是 primary，证据按重要性排列；不增加 provider 字段 | 五字段 schema snapshot 不变；prompt_version 增加 |
| <a id="task-3-2"></a>3.2 全量引用校验 | 纯函数接收 input_evidence_ids 和 EvidenceLookup 映射；先检查唯一性、coverage、所有引用存在且属输入，再推导 | [E999,E7] 整体拒绝；不查询 DB、不负责 seal 权限 |
| <a id="task-3-3"></a>3.3 本地 primary | primary=response.hypothesis.evidence_ids[0]；从传入 Mapping 的该对象推导 component 和 validation step | 仅内存 E1/E7 fixture 就能证明 component=E7 |
| <a id="task-3-4"></a>3.4 字段正名 | full-analysis 保存 input_evidence_ids/primary；删除 V2 的伪 rerank 字段 | rank-only EvidenceRerankAnalysis 不变 |
| <a id="task-3-5"></a>3.5 无存储依赖 | API 和 Codex V2 路径复用纯 normalizer；类型只依赖现有 EvidenceSnippet 和标准 Mapping/Sequence | 不安装/初始化 Store 也可完成 PR3 单测；与 PR2B 双向无依赖 |
| <a id="task-3-6"></a>3.6 历史读取 | legacy 原值保留，标旧协议；不事后用新规则篡改旧结果 | 历史 JSON 可读，来源清楚 |
| <a id="task-3-7"></a>3.7 兼容且分版本 | 旧结果只读保留；V1 shape 的必要投影标 legacy_input_order，V2 返回新字段；默认 V1 执行在 G1 前不被隐式改 prompt | V1 数据可读；rank-only 请求和归一化完全不受改动 |
| <a id="task-3-8"></a>3.8 回报 metadata 适配 | API 从响应白名单读取 reported_model/tier/usage/IDs，Codex 仅取可观测事件；默认值不回填 reported | 请求 A、回报 B 时同时保留并诊断；无回报是 null；历史 model 不能迁作 reported |

PR3 定义的 EvidenceLookup 是标准只读 Mapping 类型别名，不需要查询引擎或可插拔仓储框架。当前调用方可由 Sequence 在内存构造；PR2B 独立实现同结构返回值；PR4 保证传入映射确为发送前 seal 的证据。

不要求 V2 的 LLM 输出数值不变，因为 prompt/local semantics 有预期变化；默认 V1 不被此独立 PR 隐式切换。字段校验只能证明引用存在，不能证明原因解释正确。

reported_model 是服务端自报标识，不是经独立证明的底层模型身份。模型别名、代理路由都可能产生名称差异；默认只记录诊断，不自动失败/重试，也不能把假设的“DeepSeek 路由到另一模型”当作已观察的本仓事实。

<a id="pr4"></a>

## 11. PR4：单 Issue 执行与失败隔离

工作包前置：PR1B、PR2B、PR3。

主要落点：issue_execution.py（新增）、agent_workflow.py、agent_store_v2.py、agent_evaluation.py、tests/test_issue_execution.py、tests/test_agent_workflow.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-4-1"></a>4.1 独立执行器 | 将调查/evidence/可选 LLM 从整批 node 拆成 process_issue；共享视图/map，顺序执行 | A/B/C 独立状态，不需要 worker/并发 |
| <a id="task-4-2"></a>4.2 deterministic 即时提交 | report 完成立即提交；只保存 deterministic 字段；保存失败分类但不覆盖成功 sibling | LLM 尚未开始也可查询 deterministic 结果 |
| <a id="task-4-3"></a>4.3 evidence 提交后请求 | sealed evidence set 完整落库后才允许 backend 调用；一次采集，多次重试复用 | fake provider 收到内容与 ledger 一致 |
| <a id="task-4-4"></a>4.4 单 Issue 重试 | 将自动 retry 从批量 LangGraph node 下移到单次 Issue/backend 调用；禁止外层叠加重试 | B 重试不重做 A；预算不被多层重试乘倍 |
| <a id="task-4-5"></a>4.5 单行结果即时提交 | 开始保存请求事实；由统一 finalize 操作一次补全同一 attempt；规范化分析、脱敏错误和观测分开字段，不再插入结果表 | A/B 回报区分；success 有分析；失败不抹成功 sibling；事务失败不留伪 selected |
| <a id="task-4-6"></a>4.6 失败语义 | ProviderError 隔离，明确 no-evidence/disabled/failed；数据库、共享 map、未知程序错误 fatal | 不使用宽泛 except Exception: continue 吞 bug |
| <a id="task-4-7"></a>4.7 仅 V2 停写大 snapshot | V2 编排不复制 state/map/source；未迁移源库的 V1 流程继续正常，历史 snapshot 只读适配在 V2 target 使用 | V2 无完整 map；V1 旧功能在 G0/G1 前不被禁写 |
| <a id="task-4-8"></a>4.8 run/Issue 派生汇总 | 读取 deterministic 阶段、配置、evidence、最新/selected attempt 与 review 得到 summary；不另存 Issue LLM outcome | 全 LLM 失败仍可审查；原始 attempt 与对外摘要一致 |
| <a id="task-4-9"></a>4.9 评估 opt-in 接入 | agent-evaluate --protocol v2 使用新 reader，旧选项仍使用 V1；统计 execution/provider/no-evidence/grounding 各分母 | V1 artifacts 仍可解析；不混合协议均值 |
| <a id="task-4-10"></a>4.10 最小 CLI 与本地传输确认 | 提供 agent-run/agent-show --protocol v2、专用 DB 与前台单写；本 PR 同时实现默认关闭的真实外部传输确认（例如 --allow-external-llm），供后续 CLI retry 复用 | G0 不依赖 PR6/PR7；无当前显式确认不发送真实 evidence；fake adapter 测试不冒充授权 |

Backend 可观测性：HTTP backend 显式记录应用发送或禁用隐藏客户端重试；Codex attempt 代表一次受控 CLI invocation，不等同于已知的全部内部模型请求。请求、回报和本地观测三类字段必须分开。

G0 前台 beta 在运行入口限制同一 V2 数据库仅一个 CLI 写进程（轻量本机进程锁，非长期 SQLite 写事务）；第二个写进程明确拒绝。这个粗粒度临时门仅保护 beta，不对外承诺 PR5 的细粒度并发/resume。PR5A 替换其请求互斥部分，保留必要的 run 编排单执行者约束。

硬场景：A 成功、B 两次 provider 失败、C 成功，A/C 各调用一次，B 有两条 attempt，三者 deterministic 都可查询。全 LLM 失败仍 AWAITING_REVIEW。未知编程异常导致 FAILED，但 A 的已提交结果不丢失。

不包含：跨进程 resume、公开 retry API、成功/已审查结果的原地替换。

<a id="g0"></a>

## 12. G0：opt-in CLI beta 纵向门（PR4 后立即执行）

工作包前置：PR4。

依赖只有 PR4 及其前置；不等待 PR5A/5B、PR6/7、PR8。最小入口在 PR4 内实现，G0 是测试/接受门而非第十三个架构 PR。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-g0-1"></a>G0.1 CLI 贯通 | agent-run --protocol v2 在独立新 V2 DB 跑完捕获→deterministic→seal→fake LLM→持久化→agent-show | CLI 出口可读到 run/Issue 摘要，ledger 由集成测试复核 |
| <a id="task-g0-2"></a>G0.2 demo 范围 | 官方 examples/demo_repository 在 committed 视图执行；同 repo 加不支持/无关范围外输入 | 仅分析子目录，拒绝策略不扩大成全仓阻断 |
| <a id="task-g0-3"></a>G0.3 A/B/C 失败 | A 成功、B 失败并局部重试、C 成功；再测 no-evidence/disabled | A/C 各调用一次，B 历史齐全，三份 deterministic 可读 |
| <a id="task-g0-4"></a>G0.4 E7 与 metadata | fake provider 首引 E7，request A/response B，另测响应缺 metadata；检查同一 attempt 的 analysis/error/telemetry 组合 | component=E7；requested/reported 正确；缺项 null；无独立结果记录 |
| <a id="task-g0-5"></a>G0.5 旧流程不回归 | 合并 PR2A/2B/4 后，默认 V1 对原 legacy DB 仍能 run/show/review | 原库版本/表未自动修改；opt-in 目标库独立 |
| <a id="task-g0-6"></a>G0.6 故障和范围 | seal 失败不发请求；unsupported Git 输入明确拒绝；第二个 beta CLI writer 被拒绝 | 没有半封存发送、重复写进程或 silently skipped 源码 |
| <a id="task-g0-7"></a>G0.7 beta 边界 | 文档注明单写/无 HTTP/无手动恢复/无 per-Issue review；验证 PR4 本地传输确认默认拒绝，真实调用需当次明确许可 | PR8 不阻塞 CLI beta；没有依赖未来 PR6 的 CLI 授权功能 |

G0 通过才启动并发恢复/HTTP 的下一波实现。G0 不是 G1，不允许更改系统默认协议、强制迁移旧 DB 或对外宣称正式生产可用。

<a id="pr5a"></a>

## 13. PR5A：执行权、attempt 终态与并发保护

工作包前置：G0。

主要落点：agent_store_v2.py、execution_claims.py（可选新增）、issue_execution.py、tests/test_execution_claims.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-5a-1"></a>5A.1 最小互斥模型 | 复用 PR2B 单表 attempt_id/state 与唯一 active 索引；不增加 owner/generation，不新增或重复实现 review 逻辑 | 同 Issue 最多一个 in_progress；request/state 只有单表权威 |
| <a id="task-5a-2"></a>5A.2 原子开始 | 短事务核验 report/evidence/冻结配置和 Store 当前调用资格，插入 in_progress；资格状态只由已有数据判断，不调用 HTTP/review 服务 | 两个进程最多一个进入 backend；不存在 PR6/PR7 模块依赖 |
| <a id="task-5a-3"></a>5A.3 请求前落库 | 在请求前提交 attempt start；未创建 attempt 则可证明本协议尚未 dispatch；有 start 无终态一律保守视作可能已发出 | 崩溃窗口有确定的分类规则 |
| <a id="task-5a-4"></a>5A.4 条件终结 | 按 attempt_id AND state=in_progress 更新同一行，检查 affected-row count=1；success 才同事务更新同 Issue selected 指针，其他终态不更改成功引用 | 重复/迟到 finalize 无副作用；pointer 失败全部回滚；不更新 review_version |
| <a id="task-5a-5"></a>5A.5 unknown 保留边界 | 不按 TTL 接管；确认本地执行已终止后方可将遗留 in_progress 终结 unknown；新 attempt 必须显式授权 | 不能证明终止时拒绝 recover；不承诺远端已停或 exactly-once |
| <a id="task-5a-6"></a>5A.6 短事务 | 不在网络调用期间持有 DB 写锁；配置 busy timeout/有限竞争重试 | 慢模型期间其他 Issue/query 仍能操作 |
| <a id="task-5a-7"></a>5A.7 Attempt 并发测试 | 独立进程/连接只测 start/start、finalize/finalize、late-finalize、unknown/finalize 和 rollback；review/start 移至 7B.5 | 不安装 PR6/PR7 也可验收；唯一 active+单次终结保护成立 |

唯一 active-attempt 索引由 migration 模块维护，概念 SQL：

```sql
CREATE UNIQUE INDEX one_active_attempt_per_issue
ON agent_v2_llm_attempts(run_id, issue_number)
WHERE state = 'in_progress';
```

这里不需要 owner token/generation。生命周期字段只允许 in_progress→终态；任何终态后的迟到 finalize 条件不成立，affected-row count 为 0，不得更新选中结果。唯一约束保护“本系统同一时刻的 active attempt”，不是远端任务去重保证。

PR5A 不实现 review_version 更新或 review/start 测试。review_version 仅在 PR7B 追加审查时递增；PR7B 通过版本、目标 IDs 和 active attempt 的组合验证处理审查冲突。启动/终结 attempt 的资格仍由 Store 事务与 attempt.state 判断。

没有终态的请求可能没发出，也可能远端已完成。正常请求超时/断网也可能具有远端结果不确定性，应记录 outcome certainty；配置允许的超时重试须明确可能重复远端成本。进程中断留下的 unknown 一律不自动重发。

<a id="pr5b"></a>

## 14. PR5B：恢复运行与精确 LLM retry

工作包前置：PR5A。

主要落点：agent_resume.py（新增）、cli.py 的新受控命令、tests/test_agent_resume.py、tests/test_agent_retry.py。

计划新增命令只有：

```text
agent-resume <run-id> --protocol v2
agent-retry-llm <run-id> --issue <number> --protocol v2
agent-retry-llm <run-id> --issue <number> --protocol v2 --recover-unknown
```

不新增 agent-recover。第三行只是同一 retry 命令的明确风险授权；它不允许强行接管仍在本地执行的请求，不把 unknown 改写成 failed。PR5B 的前置只有 PR5A：本地传输确认来自 PR4；HTTP token、principal、scope 授权与撤销检查在 PR7B 调用边界实现。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-5b-1"></a>5B.1 请求配置核验 | 比较冻结 requested/client-default、实际客户端/runtime/engine/prompt/budgets；不比较未来 reported model/fingerprint 是否等于历史 | 客户端配置变了先拒绝；回报缺失不伪造成 effective |
| <a id="task-5b-2"></a>5B.2 committed resume | 从 captured revision 重建范围视图、一次 map，读取原 selected ordinals；已提交 deterministic 不重做 | Issue 1 提交后退出，恢复从下一未完成工作继续 |
| <a id="task-5b-3"></a>5B.3 pure LLM retry | 只读原 Issue 和 sealed evidence/config；只允许明确失败且未审查 Issue | 修改/移走当前 checkout 不改变 retry 输入 |
| <a id="task-5b-4"></a>5B.4 worktree 规则 | tracked_worktree deterministic 恢复拒绝；已保存 evidence 可进行同配置 LLM retry | 不用 mtime/size 冒充内容相同证明 |
| <a id="task-5b-5"></a>5B.5 合并 unknown 恢复 | 默认拒绝 unknown 重发；--recover-unknown 明示可能重复远端调用，在本地终止确认后新建 attempt，旧 unknown 不覆盖 | 只有一个恢复命令；无 flag/仍活跃本地执行时不发请求 |
| <a id="task-5b-6"></a>5B.6 新配置派生 | 改 model/prompt/budget 创建 parent_run_id 关联的新运行，不能 update 原 config | 新旧报告/审查互不混淆 |
| <a id="task-5b-7"></a>5B.7 中断 fault injection | 在 deterministic commit、evidence seal、attempt start、远端完成、结果 commit 窗口注入崩溃 | 已提交工作不重做；未知状态不自动发送 |
| <a id="task-5b-8"></a>5B.8 CLI 当前传输确认 | 仅复用 PR4 的 CLI 当次外部传输许可，核对当前请求 provider/endpoint 与冻结配置；默认拒绝发送；不加载 principal/token/HTTP allowlist | 可在 PR6 未合并时独立运行；撤回 CLI 发送许可则不调用 provider；HTTP 撤销测试由 7B.7 负责 |

第一版只承诺应用内部已提交结果去重，不承诺远端 exactly-once。对已成功/已审查 Issue 要重新比较模型时创建新运行。

<a id="pr6"></a>

## 15. PR6：API 安全与外部传输

工作包前置：G0、PR1A。

主要落点：api_security.py（新增）、repository_policy.py（可选新增）、api.py、config.py、cli.py 的 serve、tests/test_api_security.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-6-1"></a>6.1 支持边界 | 受控 serve 仅接受 loopback 绑定；关闭默认代理身份信任；部署文档不承诺远程多租户 | 非支持 bind 拒绝；真实本机启动 smoke test |
| <a id="task-6-2"></a>6.2 本地 token | health 以外敏感接口需认证；token 来自 secret 配置，不记录日志/DB；无 token 时默认禁用敏感 API | 无/错 token 返回 401；不是只测 happy path |
| <a id="task-6-3"></a>6.3 principal | 服务端从认证配置推导 local principal；CLI 记录本机执行身份；禁止 body 指定 reviewer | 伪造 reviewer 字段拒绝；共享 token 明确非多人身份 |
| <a id="task-6-4"></a>6.4 allowlist | 授权 analysis_root 源码范围；Git root 仅提供元数据；拒绝符号链接及路径穿越 | 没配置 allowlist 默认拒绝扫描，不默许全盘 |
| <a id="task-6-5"></a>6.5 浏览器/代理面 | 限制 Host/Origin，不使用 cookie 自动登录，不默认放宽 CORS，不接受伪造 Forwarded 用户 | 非授权浏览器 origin、代理头不能绕过认证 |
| <a id="task-6-6"></a>6.6 endpoint 策略 | 所有 V1/V2 repository/run/evidence/review/retry 都使用相同鉴权依赖 | 旧入口不是绕过安全的新后门 |
| <a id="task-6-7"></a>6.7 HTTP 外部传输策略 | 实现 principal+analysis scope+provider+operation 的当前授权服务；HTTP 不接收任意 base URL；PR7B 接入 retry/recover-unknown，PR5B 继续复用原 CLI 确认 | 本 PR 单测授权决定；最终 HTTP 权限撤销与请求发送测试归 7B.7；无反向依赖 |
| <a id="task-6-8"></a>6.8 数据限制 | 请求 Issue 数/body/evidence 总量、分页大小、单条内容大小和并发 run 上限有显式边界 | 超限在建立大状态/发模型前拒绝 |
| <a id="task-6-9"></a>6.9 脱敏与权限 | secret-bearing remote/base URL/error 不入 DB/JSON/trace；保护 DB/备份/输出目录；重用原有 LLM 无工具隔离 | redaction canary 测试；没有“只写 prompt 即完全安全”承诺 |

认证升级属于有意的安全行为变化。旧 HTTP 调用方需要配置 token；“旧 JSON 可读”不等于“继续允许旧的匿名写接口”。

<a id="pr7a"></a>

## 16. PR7A：查询 API、CLI 与兼容投影

工作包前置：PR6、G0。

主要落点：api.py、cli.py、legacy_projection.py、tests/test_api_v2.py、tests/test_cli.py、tests/test_legacy_projection.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-7a-1"></a>7A.1 run summary | V2 run GET 返回 provenance、配置摘要和聚合状态；不塞完整 evidence/map | 大仓库响应大小不会随完整 map 线性复制 |
| <a id="task-7a-2"></a>7A.2 Issue 查询 | 提供分页 Issue list 和单 Issue execution；selected analysis 由 attempt pointer 投影 | 无两份可能不一致的 LLM 结果 |
| <a id="task-7a-3"></a>7A.3 Evidence 列表 | 认证后返回 ID/rank/file/symbol/range/truncation 等 metadata；默认无源码 | 列表返回体无 content |
| <a id="task-7a-4"></a>7A.4 Evidence 内容 | 单条读取需要认证、scope 和 retention 可用性检查；严格绑定 run/Issue/set | E7 可解析；cross-Issue ID 拒绝 |
| <a id="task-7a-5"></a>7A.5 CLI 展示 | 增加 Issue/evidence 查询与显式源码导出；默认 summary，源码导出需要显式选项 | 命令不会默认把私有源码复制到宽松权限文件 |
| <a id="task-7a-6"></a>7A.6 V1 投影 | 旧运行原样读；V2 按规则合成旧 investigation；不能表示的新状态返回明确升级错误 | mixed review 不投影为 false approved/rejected |
| <a id="task-7a-7"></a>7A.7 评估/历史兼容 | agent-evaluate 与旧 artifacts 用版本 adapter；unknown 字段保留 null/unknown | legacy 无 provenance 不被标为当前版本 |
| <a id="task-7a-8"></a>7A.8 契约文档 | 文档说明 V1/V2 的字段、状态、鉴权和导出差异 | API/CLI 文档与 schema 测试一致 |

建议 V2 读取接口：

```text
GET /v2/agent/runs/{run_id}
GET /v2/agent/runs/{run_id}/issues
GET /v2/agent/runs/{run_id}/issues/{issue_number}
GET /v2/agent/runs/{run_id}/issues/{issue_number}/evidence
GET /v2/agent/runs/{run_id}/issues/{issue_number}/evidence/{evidence_id}
GET /v2/agent/runs/{run_id}/issues/{issue_number}/attempts
```

Evidence set 第一版一个 Issue 一个 sealed 当前集，retry 不更换；未来多轮采集必须显式引入 set/revision 选择，不能复用 E1 覆盖原值。

<a id="pr7b"></a>

## 17. PR7B：per-Issue review、correction、retry 入口

工作包前置：PR5B、PR7A。

主要落点：review_service.py（新增）、api.py、cli.py、agent_store_v2.py、tests/test_review_service.py、tests/test_api_v2.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-7b-1"></a>7B.1 审查服务 | 服务显式接收 principal_id、idempotency_key、expected_review_version 和独立的 decision/notes/corrections；principal 只来自认证 | 同 reviewer 可用新 key 更正；正文不能伪造主体 |
| <a id="task-7b-2"></a>7B.2 不可变目标绑定 | Review 绑定现有 IssueExecution 复合身份 (run_id, issue_number)、evidence_set_id、selected_attempt_id；每 Issue 单一 report，不引入 report revision | 不存在未定义 revision；目标跨 Issue/与当前指针不符时拒绝；允许无 LLM 的 null attempt |
| <a id="task-7b-3"></a>7B.3 correction | 更正文件/symbol 属于分析范围；不存在目标要明确标 proposed-new-location；不改历史模型文本 | 原始结果保持不变，更正可单独导出 |
| <a id="task-7b-4"></a>7B.4 mixed 状态 | approved/rejected/needs_information 为本轮终态；按可审查 Issue 集合聚合 | 全 approve、全 reject、mixed、pending、no-evidence 均正确 |
| <a id="task-7b-5"></a>7B.5 Review/Attempt 原子集成 | 在本 PR 将 review 与 start/retry 资格检查接到同一短事务边界；先鉴权/幂等重放再比较 review_version、目标 IDs 与无 active attempt；补真实 review/start 与 review/retry 竞争测试 | 两个顺序都合法：review 先提交则禁止原地 retry，start 先提交则 review=409；无迟到批准和重复审查 |
| <a id="task-7b-6"></a>7B.6 独立幂等键 | key 作用域为 run+Issue+principal+review 操作；存规范化完整 payload 和原结果。同 key 同 payload 返回旧结果；异 payload=409；新 key+最新版本可追加 | 相同 reviewer 连续两次不同决定可合法写入；并发同 key 只生成一条记录 |
| <a id="task-7b-7"></a>7B.7 HTTP retry 授权集成 | 每次 HTTP retry/recover-unknown 先验证当前 token/principal、scope allowlist 与传输策略，再调用 PR5B 服务；禁止正文改配置或身份；与 7B.5 并发资格检查衔接 | 撤销 token/scope/provider 许可后不发请求；PR5B 无 HTTP 依赖；并发只一项取得执行权 |
| <a id="task-7b-8"></a>7B.8 明确 legacy 范围 | V2 目标库内历史记录只读；G1 前原 legacy 源库仍按 V1 审查；V2 不允许 run-level 隐式批量批准 | 不提前破坏旧流程；默认切换时明确告知兼容范围 |

### 审查幂等的固定处理顺序

```text
认证 principal + 当前资源授权
  → 查询 (run_id, issue_number, principal_id, idempotency_key)
  → 已存在且规范化 payload 相同：返回原 review_record / 原响应
  → 已存在但 payload 不同：409 IDEMPOTENCY_PAYLOAD_MISMATCH
  → 新 key：验证 expected_review_version + issue_execution_id/evidence_set_id/selected_attempt_id + 无 active attempt
  → 一个事务追加 review、保存 key/payload/结果、更新 review_version
```

规范化 payload 包括 decision、notes、corrections、被审查目标引用及 expected_review_version；不包含 token，principal 来自认证上下文。采用明确字段比较或 canonical JSON 比较，不使用内容哈希替代幂等键。新 key 的默认值可由 CLI 随机生成；需要网络重放时必须复用原 key。

示例：alice 用 k1、v3 提交 approved 后得到 v4；重发 k1/v3 同内容仍返回原结果；同 k1 改成 rejected 返回 409；alice 用 k2/v4 提交 rejected 合法追加并得到 v5。即使 run 已 REVIEW_COMPLETED，也允许认证主体以新 key/最新版本追加更正，原 review 不被覆盖。

幂等缓存不能绕过当前权限：token 撤销或资源授权撤销后，同 key 重放也必须被拒。不同 principal 的 key 命名空间隔离，不允许碰到别人的 key 后获取其结果。

建议写接口：

```text
POST /v2/agent/runs
POST /v2/agent/runs/{run_id}/issues/{issue_number}/reviews
POST /v2/agent/runs/{run_id}/issues/{issue_number}/llm-retry
```

第一版仍同步执行；不得在返回 202 后声称存在未实现的后台任务。代码生成、运行测试、提交 PR 均不是 review approve 的隐式副作用。

<a id="pr8"></a>

## 18. PR8：当前结果目录和文档一致性

工作包前置：T0。

主要落点：benchmarks/results/current-results.json（新增）、docs/benchmark-results.md、README.md、tests/test_result_catalog.py。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-8-1"></a>8.1 独立目录 schema | 各评估类型指定 current，记录来源、manifest/index/retrieval、requested 与可用 reported 信息；目录包含 dataset_role 元数据，PR8 不依赖 V2 Store | 目录自包含、不混合实验；可承载前移的 F1.1 |
| <a id="task-f1-1"></a>F1.1 数据集角色（前移 PR8） | 立即在 PR8 目录和当前文档中标记 manifest v20 为 regression/development；保留 main/calibration/generalization 历史 tier 与原计数，不改 ground truth/历史结果 | 现有 README 不再将当前用途暗示为独立 holdout；新 holdout 仍由 F1.2–F1.7 实施 |
| <a id="task-8-2"></a>8.2 21/20 历史修正 | 保留旧 21-miss artifact；在目录标 superseded；新 20-miss summary 作为当前摘要 | 不修改历史文件内容或伪造新 full audit |
| <a id="task-8-3"></a>8.3 完整级别 | 标 full/summary-only、raw/detail 是否可用、replace/supersede 关系 | 缺完整 audit 明确披露，不拿旧细节冒充新细节 |
| <a id="task-8-4"></a>8.4 文档与数据集角色同步 | 当前数字引用同一目录；随 F1.1 修正 README 与结果页的现时用途说明，保留历史 tier 名和值；文件名区分协议 | 可保留历史 172 generalization 的分组计数，但必须说明非当前独立 holdout；不改样本/成绩 |
| <a id="task-8-5"></a>8.5 一致性测试 | 检查文件、计数、协议、受控文档摘要和 dataset_role；检验 F1.1 只在 PR8 实现且新 holdout 建设仍在 F1 | CI 拦截 current 数字/角色漂移；历史分组不被误解释为新泛化结论 |
| <a id="task-8-6"></a>8.6 原子更新 | 新结果先写新文件，验证后一次替换目录引用；从不覆盖旧运行产物 | 中断不会留下 current 指向半写文件 |

F1.1 是原任务编号的归属迁移，已列在本 PR 任务表；不在 F1 再定义一次。执行顺序为 8.1 → F1.1 → 8.4/8.5；8.2/8.3/8.6 依各自产物推进。角色信息放在目录与当前文档中，不为此修改原 manifest 样本或 historical tier。PR8 仍不阻塞 G0，但应在 T0 后独立推进，不等待 G1 才开始更正。

无需为这个 PR 启动付费模型。只有用户另行授权并实际完成新评估，才能将新运行登记为 current。

<a id="g1"></a>

## 19. G1：发布验收、默认切换与回退演练

工作包前置：PR1A、PR1B、PR2A、PR2B、PR3、PR4、G0、PR5A、PR5B、PR6、PR7A、PR7B、PR8。

主要落点：tests/test_protocol_v2_e2e.py、.github/workflows/ci.yml、README.md、docs/architecture.md、版本适配与发布说明。

| 任务 | 具体执行 | 完成证据 |
|---|---|---|
| <a id="task-g1-1"></a>G1.1 多版本测试 | 先维持现有 Python 3.11/3.12 CI 矩阵；至少加 Linux CI 的迁移/并发测试及 macOS Git/symlink smoke | 实际运行报告，不沿用历史 passed 数 |
| <a id="task-g1-2"></a>G1.2 正式迁移演练 | 停止旧运行→source backup→新 destination 显式迁移→历史读取→选定 V2 正式目标；beta DB 不自动合并 | 旧数据保留；只有切换时停止默认 legacy 写入 |
| <a id="task-g1-3"></a>G1.3 端到端失败 | 多 Issue 成功/失败/no-evidence/disabled/fatal 混合，查看 evidence，再 review/retry | 没有已提交数据丢失和无凭据接口 |
| <a id="task-g1-4"></a>G1.4 排名回归 | 对 clean tracked 同输入集合比逐 case candidate order、symbols、metrics；检查 official demo | 实际等价报告；decoy/表示差异单列 |
| <a id="task-g1-5"></a>G1.5 存储规模 | 用固定大型 synthetic map 运行，检查 map 不进每步 DB snapshot；记录 DB size/row counts | 规模报告，不声称未经测量的百分比收益 |
| <a id="task-g1-6"></a>G1.6 请求/回报与终态会计 | 验证 requested/reported/local、unknown usage、single-row finalize 与 selected 投影；HTTP/CLI 可观测性分别测试 | 无重复 outcome；无 effective 或 exactly-once 承诺；坏字段组合不能落库 |
| <a id="task-g1-7"></a>G1.7 安全审查 | token、Host/Origin、allowlist、symlink、userinfo、错误脱敏、export/backup 权限 | 独立 reviewer 给出通过/残余风险 |
| <a id="task-g1-8"></a>G1.8 默认切换 | G0+所有第一阶段 PR（含独立 PR8）通过后切默认 V2；旧源库停止写入并保留只读；不存在同 run 双写 | CLI beta 可早于 PR8；正式发布必须有 current-result 一致性 |
| <a id="task-g1-9"></a>G1.9 回退演练 | 停止写入→导出 V2 新数据→读取备份演练→验证兼容；不自动覆盖正式 DB | 可执行 runbook，明确升级后数据的保护方式 |
| <a id="task-g1-10"></a>G1.10 发布范围 | 更新实际 CLI/HTTP/恢复/审查语义；核验 PR8 已更正 v20 role，再说明 F1 剩余 holdout 工作和 F2→F4 | 不推迟现有角色更正到发布后；文档来源在仓库可定位 |

现有基础检查命令（执行各 PR 时使用，并加入该 PR 的 focused tests）：

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run pytest -q
git diff --check
```

不是每个 PR 都要触发付费 LLM 或重跑全部真实仓库。分层门禁为 focused tests → API/CLI/store/evaluation 集成 → G1 全量确定性回归。真实 provider smoke 单独授权，必须记录配置和输入，不能把启动请求当作完成。

## 20. 统一验收矩阵

| 门槛 | 必须观察到的行为 | 负责 PR |
|---|---|---|
| A01 | legacy schema 0 被识别；PR2A 不自动升级也不禁止原路径写入 | 2A、G0 |
| A02 | 建表/建索引/版本更新过程中失败完整回滚 | 2A |
| A03 | official demo 只分析 examples/demo_repository | 1A、1B |
| A04 | detached、无 origin、多 remote 有确定语义 | 1A |
| A05 | remote/base URL 凭据不进日志、DB、JSON | 1A、6 |
| A06 | captured committed 输入不随原 checkout 修改而改变 | 1B |
| A07 | untracked decoy 默认消失，记为预期行为 | 1B |
| A08 | tracked symlink 不能带入范围外或 untracked 内容 | 1B、6 |
| A09 | E7 primary 对应 E7 component，provider schema 不增加冗余字段 | 3 |
| A10 | E999 在首位时失败，不跳过改取 E7 | 3 |
| A11 | 每个模型引用都能解析到实际发送的 sealed evidence | 2B、4、7A |
| A12 | evidence 部分写失败不会发模型 | 2B、4 |
| A13 | B 的失败不重做 A，也不阻止 C 的普通处理 | 4 |
| A14 | 已提交 Issue 1 后进程中断，恢复不重做 Issue 1 | 5B |
| A15 | 已 dispatch 无终态不自动重发，明确 unknown | 5A、5B |
| A16 | requested/client config 不同则 fail closed；reported 变化只记观察，不假定可强制控制 | 1A、3、5B |
| A17 | 两进程 retry 同 Issue 只有一个取得执行权 | 5A、7B |
| A18 | 已终结 attempt 的迟到 finalizer 不能更新新 attempt 或 selected pointer | 5A |
| A19 | 未认证者不能读取 Evidence metadata 或 content | 6、7A |
| A20 | reviewer 身份来自服务端 principal 而非请求 body | 6、7B |
| A21 | review 与 retry 并发时冲突被明确拒绝 | 7B |
| A22 | 全部 LLM 失败但 deterministic 成功仍可审查 | 4、7B |
| A23 | mixed review 不冒充 run-level approved/rejected | 7B |
| A24 | attempt.state 与同一行 analysis/error 是唯一终态来源；Issue LLM 状态及分析由其投影 | 2A、2B、4、7A |
| A25 | V2 不重复保存完整 repository map | 4、G1 |
| A26 | 历史 JSON/agent-evaluate 可读，缺字段为 unknown | 3、7A、G1 |
| A27 | clean tracked 相同输入的候选顺序和 symbol 选择一致 | 1B、G1 |
| A28 | rank-only hybrid 独立 rerank 路径不被 full-analysis 修复改变 | 3、G1 |
| A29 | 21-miss 旧 audit 与 20-miss 新摘要有唯一明确 current 关系 | 8 |
| A30 | 撤销当前 HTTP token/scope/provider 许可后，retry/recover-unknown 不发出原始 evidence | 6、7B |
| A31 | 代码/输入/配置 unknown 时不伪称严格可恢复 | 1A、5B |
| A32 | no-evidence 是可审查调查状态，不伪称模型判断“仓库无 bug” | 4、7B |

### 渐进发布阻塞验收

| 门槛 | 必须观察到的行为 | 负责 PR/门 |
|---|---|---|
| A33 | PR2A 单独合并后默认 CLI/API 创建、保存、审查成功，源库保持 version 0 | 2A |
| A34 | migration 是全部 V2 DDL 唯一来源；Store/fixture builder 不复制建表逻辑 | 2A、2B |
| A35 | PR2B 只对显式新目标建库/迁移，拒绝源目标同文件/目标已存在；源库不改变 | 2B |
| A36 | PR3 单独在内存 Mapping 完成 E7 与 E999 测试，不导入 Store/sqlite3 | 3 |
| A37 | 请求模型 A、服务端回报 B 两者分别保存；缺 temperature/seed/tier 时 reported 为 null | 1A、3、4、G0 |
| A38 | 同 key 同 payload 在 review_version 已增长后仍重放原结果 | 7B |
| A39 | 同 key 不同 payload=409；同 principal 新 key+最新版本可更正 | 7B |
| A40 | 独立 principal 与 key 作用域隔离；认证撤销后连幂等重放也拒绝 | 6、7B |
| A41 | LFS pointer、submodule/gitlink、冲突、外部 filter 的 scope 内 fixture 首版明确拒绝 | 1B、G0 |
| A42 | G0 在 PR4 后且不依赖 PR5/6/7/8 跑通 opt-in CLI；默认 V1 不变 | G0 |
| A43 | 没有第三个 agent-recover 命令；--recover-unknown 不自动重发或抢占活跃执行 | 5B |
| A44 | 只用 attempt_id/state/唯一 active/条件更新即可通过跨进程测试；review_version 不作 owner | 5A |
| A45 | PR8 缺失不阻塞 CLI beta，但阻塞 G1 默认切换 | G0、8、G1 |
| A46 | 单表 attempt 的非法字段组合、请求变更、终态改写被拒；不存在独立 outcome 副本 | 2A、2B |
| A47 | attempt finalize 与 selected 指针更新中途失败时全部回滚；affected-row count=0 无任何指针副作用 | 2B、5A |
| A48 | PR5A/PR5B 未合并 PR6/PR7 时可独立通过 attempt/CLI 测试；不导入 HTTP 安全或 review 服务 | 5A、5B |
| A49 | PR7B 补齐 review/start、review/retry 和 HTTP 权限撤销端到端测试 | 7B |
| A50 | Review 绑定已有 IssueExecution 复合身份、evidence set、selected attempt，无 report revision | 7B |
| A51 | F1.1 在 PR8 完成角色更正；不改 historical tier、gold 或成绩；F1 后续不重复该实现 | 8、G1 |
| A52 | checklist 仅四列索引，无实施/验收副本；所有任务链接唯一有效；工作包依赖只在 plan 定义 | T0、G1 |
| A53 | 标题层级为 23.1/23.2；依据均为已存在仓库路径或已嵌入的本版决策，无聊天附件依赖 | T0 |

### 排名等价与有意变化的边界

- 等价门：相同 commit、相同文件范围、相同字节表示、相同检索配置的 candidate files、symbols、ranking metrics。
- 有意变化门：tracked-only 移除 untracked 文件；固定视图不受用户工作目录后续变化影响；metadata 与协议版本增加。
- 分析契约门：component 修正、full-analysis 字段正名、证据完整行截断等属于显式协议变化，必须版本化，不要求模型输出值完全相同。
- 安全门：认证/allowlist 会使旧匿名调用被拒绝，这是安全升级，不称为完全 HTTP 行为兼容。

## 21. 第二阶段：未纳入 v2 发布门的完整后续任务

第一阶段 PR8 先完成 F1.1 的现有数据集角色更正。第二阶段固定依赖为 **F1.2–F1.7 评测边界/新 holdout → F2 拒答/计划真实性 → F3 检索实验 → F4 校准**。F1 工作包共六个剩余任务；后续算法比较遵守新评测协议，不把已用于调参的 v20 当新 holdout。

F5（priority/duplicate/context）和 F6（远程部署、生命周期、共享 cache、调查循环）是按实际需求启动的可选工作流，不是第二阶段默认串行门，也不阻塞 Protocol v2 发布。保留具体任务避免 backlog 消失，但没有自动授权启动。

<a id="f1"></a>

### F1：新的评测边界（F1.1 已前移 PR8，本阶段从 F1.2 开始）

工作包前置：G1。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f1-2"></a>F1.2 时间证据 | 新采集记录 issue first-seen/captured/as-of、正文/labels/comment 来源时间；不能仅凭 updated_at 推断泄漏 | 缺历史内容标 temporal-unverified |
| <a id="task-f1-3"></a>F1.3 独立测试集 | 建 repository/time-disjoint holdout；结果访问与规则开发隔离；新一轮看过结果后归档为已使用 | 数据流和允许访问者有记录；不保证模型训练污染为零 |
| <a id="task-f1-4"></a>F1.4 任务类型与 gold | 新增 negative/upstream/configuration/needs-info 样本；保留原 patch-surface gold，新增审查过的定位角色/可接受替代 | 不为提高 recall 删除难例；role 由独立审查确定 |
| <a id="task-f1-5"></a>F1.5 指标 | 同时报告 case-macro/target-micro、Hit@K、完整 patch recall、inspection budget、region visibility、failure-as-zero | 数学定义/分母固定，可用小例子手算验证 |
| <a id="task-f1-6"></a>F1.6 稳定性 | 预先固定重复计划，不由“是否报 schema error”决定；按仓库聚类报告不确定性 | 所有预定有效运行包括负结果；协议稳定与排名稳定分开 |
| <a id="task-f1-7"></a>F1.7 记忆污染诊断 | 公共/私有或受控新样本分开；重命名反事实只作诊断，不当无污染证明 | 风险描述不升级成已发生泄漏结论 |

<a id="f2"></a>

### F2：拒答与复现计划真实性

工作包前置：F1。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f2-1"></a>F2.1 拒答语义 | 在独立分析协议中区分 localized/needs_information/no_applicable_evidence/upstream 等；允许没有具体根因假说 | 没有证据时不强行制造 H1；负样本误报率可测 |
| <a id="task-f2-2"></a>F2.2 Provider 变更门 | 若拒答确需改变 provider schema，单独版本和协议成功率实验；与 v2 primary 修复无关 | 不能借拒答任务偷偷扩大 v2 provider 字段 |
| <a id="task-f2-3"></a>F2.3 计划来源 | 从目标子项目的配置/CI/现有测试文档提取 runtime、package manager、命令与来源；不执行配置 | Rust/TSX 不因仓库有 Python fixture 而默认 pytest |
| <a id="task-f2-4"></a>F2.4 不确定计划 | 无可靠依据时 command=null、列缺少信息；不得把通用建议包装成已验证命令 | 不存在固定 uv/pytest“安全占位”冒充可执行复现 |
| <a id="task-f2-5"></a>F2.5 可验证验证步 | 关联具体假说、可观察失败和现有测试候选；在未来显式授权后才能执行 | approve 仍不自动运行命令 |

<a id="f3"></a>

### F3：检索核心重构与新增召回通道

工作包前置：F2。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f3-1"></a>F3.1 typed EvidenceKind/Vote | 把 evidence 字符串前缀控制流改为类型，展示文案与算法作用分离 | 相同输入逐 case 完全等价；先不改权重 |
| <a id="task-f3-2"></a>F3.2 通道消融 | 对 path/symbol/content/static/history/protected policies 单独开关，记录每个 target 来源 | 能独立解释一条改动，不混合 benchmark 扩容 |
| <a id="task-f3-3"></a>F3.3 简单 IR baseline | 实现确定性 BM25/TF-IDF 基线，采用同输入和同预算；记录 tokenizer/字段权重 | 与旧规则匹配比较，不预先宣称优于现状 |
| <a id="task-f3-4"></a>F3.4 候选组合实验 | 多通道配额/融合作为单独实验，检查召回、精度、审查成本、噪声挤出 | 同一 holdout 协议下报告收益与回归 |
| <a id="task-f3-5"></a>F3.5 region evidence | 对正确文件内的多片段采样、完整行截断和符号上下文做独立优化 | region-visible recall 与 token budget 同时报告 |
| <a id="task-f3-6"></a>F3.6 Rust 关系 | parser-backed declaration/use/re-export，再做有限调用关系；不把模糊调用当精确 edge | 专门 parser fixtures + 冻结真实样本 |
| <a id="task-f3-7"></a>F3.7 TS/TSX 关系 | import/export、JSX/component/hook、路径别名支持；与 Rust 分 PR | frontend issue 的关系可审计，歧义保留 |
| <a id="task-f3-8"></a>F3.8 native/Python dispatch | Python→native binding 和 backend/plugin dispatch 分别做 bounded 通道 | 每类边有 provenance，不能用同名猜测冒充精确关系 |

<a id="f4"></a>

### F4：分数与置信度

工作包前置：F3。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f4-1"></a>F4.1 字段正名 | V2+ 暴露 raw ranking_score/score_kind；旧 confidence 作为明确非概率兼容值 | UI 不再把 0.98 显示为 98% 正确率 |
| <a id="task-f4-2"></a>F4.2 校准集 | 在独立校准样本中分别处理 candidate score 与 LLM 自报 confidence | 不在测试集上选校准参数 |
| <a id="task-f4-3"></a>F4.3 校准评估 | calibration curve、Brier/ECE 与 risk-coverage；语言/仓库规模切片 | 数据不足保持 uncalibrated，不强行给概率 |

<a id="f5"></a>

### F5：优先级、重复聚类与 Issue 上下文

工作包前置：G1；按明确需求开启，不自动排入执行。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f5-1"></a>F5.1 Priority 标注 | 独立维护者 priority/milestone/SLA ground truth 与仓库策略配置 | 文件定位 recall 不再替代 priority 有效性 |
| <a id="task-f5-2"></a>F5.2 文本语义 | 否定、引用、模板区分；comments 与 recency 仅作为显式特征，避免重复计算同一信号 | “not a security issue” 不因词面自动 high |
| <a id="task-f5-3"></a>F5.3 Duplicate cluster | 建 canonical cluster、汇总影响后按 cluster 选择；控制相似图链式误合并 | 五个同问题不占满 Top-K；簇质量独立评测 |
| <a id="task-f5-4"></a>F5.4 Candidate blocking | 数据规模需要时再替换 O(n²) 全量比较，保留小集合 baseline | 性能收益不能牺牲未报告的 duplicate recall |
| <a id="task-f5-5"></a>F5.5 上下文采集 | comments/linked issues/versions/CI 带时间和来源，按 as-of 截止及权限采集 | 不混入修复后答案；metadata-only 不伪称读过内容 |

<a id="f6"></a>

### F6：后续部署与数据生命周期

工作包前置：G1；按明确需求开启，不自动排入执行。

| 任务 | 执行内容 | 验收 |
|---|---|---|
| <a id="task-f6-1"></a>F6.1 数据保留 | 明确 evidence/attempt/review/backup 保存期限；用户显式导出和清理；清理后保留 unavailable/tombstone 语义 | 不默认删除有价值历史，不从当前源码重造已清理 evidence |
| <a id="task-f6-2"></a>F6.2 远程身份 | 真要远程部署时另做认证主体、逐仓库授权、TLS、审计和 tenant 隔离 | loopback+共享 token 不能冒充团队身份系统 |
| <a id="task-f6-3"></a>F6.3 增量/共享 cache | 只有在 profile 证明收益后从 benchmark 私有 cache 抽取共享服务 | scope/config/representation 匹配；dirty 输入不串用 |
| <a id="task-f6-4"></a>F6.4 有界调查循环 | 新证据请求、竞争假说和只读扩展，受预算和权限约束；不是默认多 agent | 未授权不运行项目代码；结束条件/拒答可测试 |

## 22. 覆盖范围与现有模块

下表按仓库功能定位，不要求读者取得某份聊天评审来解释编号。源码行为以第 25 节固定基线为准；“待改进”不是已经发生事故或已通过测试的声明。

| 范围与仓库定位 | 第一阶段交付 | 后续任务 |
|---|---|---|
| benchmark 角色与当前结果：README.md、docs/benchmark-results.md | PR8 明确 v20 当前 regression/development 用途和 current-result 来源 | F1.2–F1.7 新 holdout、时间来源、指标及稳定性 |
| 分析/evidence：llm_client.py、evidence.py、models.py | 纯 normalizer、E7 component、封存证据、可查询 ID 和截断元数据 | F2 拒答与 F3.5 区域采样实验 |
| 仓库输入：repository_index.py、agent_workflow.py | analysis scope、固定源码视图、requested 配置与运行输入 | F6.3 共享 cache 仅按需 |
| 运行与存储：agent_store.py、agent_workflow.py | 迁移、单 Issue 提交、单行 attempt、失败隔离与受控恢复 | F6.1 数据生命周期按需 |
| 本地 API/review：api.py、models.py | loopback/token、scope 授权、传输确认、逐 Issue 幂等审查 | F6.2 真正远程身份按需 |
| 复现计划与 confidence：investigator.py | 不在本阶段改其算法，不声称已校准或已验证命令 | F2、F4 |
| priority/duplicate/context：scoring.py、duplicates.py、github_client.py | 冻结现有输入和选择，未证明其整体质量 | F5 按需 |
| 检索单体：investigator.py | 同输入行为保持，不增加召回 heuristic | F3.1–F3.8 |

潜在时间泄漏或模型记忆污染只能作为待评估风险；当前计划没有声称观察到真实泄漏。字段引用校验也不等于根因解释正确。

## 23. 分工、工作树与每项任务的交付规则

每个独立实现 PR 创建新的 DevSpace worktree，并复用对应 workspaceId。基线取已验收依赖合并后的 commit；不要在同一个 checkout 混写多个独立 PR。继续同一任务时不反复 open_workspace。

并行规则：T0 后 PR1A/PR2A/PR3/PR8 可并行；PR1B 与 PR2B 依据已合并依赖推进；PR4 汇合后先过 G0。G0 后 PR5A→PR5B 与 PR6→PR7A 可并行，PR7B 最后汇合；PR8 不占用 beta 关键路径。

models.py、cli.py、api.py、agent_store.py 等共享区域一次只给一个修改负责人。PR3 主要新增 analysis_contract.py 或纯函数，PR1A 管输入模型，PR2A 唯一管理 DDL，PR2B 管 Store。跨 PR 不通过复制 schema 或数据库 adapter 解耦。

不要求拆出复杂多 agent 系统。一次 PR 至少包含实现负责人和独立验证/审查角色。迁移、执行权、安全 PR 应独立复核 failure path。

每个任务的 Definition of Done：

1. 关联 task ID、目标、前置依赖、排除范围和“该 PR 单独合并时默认功能仍可用”的证据。
2. 最小复现测试先存在，包含预期失败/边界场景。
3. 代码实现只修改归属文件；接口变化同步直接相关调用方。
4. focused tests、必要集成测试、ruff、git diff --check 实际执行。
5. 差异中没有用户无关修改、真实凭据、原始私有 evidence、dataset/checkpoint 或无授权内容哈希。
6. 更新 RFC 状态、直接文档与兼容说明，不以“命令已启动”代替结果。
7. 审查通过后按仓库既有 PR 流程提交；报告 commit、测试与未验证项。
8. G0 只开放 opt-in CLI，G1 才启用默认 V2；任何必要门未过则停止相应发布，不伪造通过。

任务看板建议字段：task_id、PR、owner、depends_on、status、changed_files、test_commands、test_result、review_result、commit、remaining_risks。status 仅允许 planned/in_progress/blocked/verified/merged；本计划所有实现项初始为 planned。

### 23.1 关键接口与功能归属

以下是设计接口，不是要求照抄的新公共 API；T0 固定名称后，后续 PR 不各自发明第二套实现。

| 服务接口 | 责任 | 对应任务 |
|---|---|---|
| capture_repository_context(root, mode) | 验证 Git/analysis 范围并返回 snapshot + scoped file manifest | 1A.1–1A.4 |
| capture_requested_run_configuration(client, engine, budgets) | 导出请求意图、客户端默认来源与 omitted 字段；不代表服务端实际采用 | 1A.5–1A.7 |
| prepare_repository_view(snapshot) | 生成固定 revision/scope 的运行读取视图；负责生命周期 | 1B.1–1B.6 |
| inspect_agent_database(connection) | 区分空库、legacy 0、V2 和不支持结构 | 2A.1 |
| inspect/plan/apply_agent_migration | 唯一 DDL；PR2A 仅备份/预检及临时库测试；PR2B 对显式 destination 开放 apply | 2A.1–2A.7、2B.10 |
| create_run(snapshot, configuration, inputs, selection) | 一次性保存不可变运行上下文 | 2B.1 |
| save_deterministic_result(run_id, issue, report) | 顺序编排下保存唯一 deterministic 结果；按阶段条件提交，不拿 review_version 作 owner | 2B.2、4.2 |
| seal_evidence_set(run_id, issue, items, collection_context) | 原子插入完整 evidence 集合后封存 | 2B.3–2B.5 |
| start_attempt(issue, evidence_set, requested_configuration) | 原子插入 in_progress attempt，唯一约束取得请求执行权 | 2B.6、4.5、5A.1–5A.3 |
| finalize_attempt(attempt_id, terminal_fields) | 条件更新同一行并检查 affected rows=1；仅成功时同事务更新 selected 指针，无第二个 outcome 记录 | 2B.6、3.8、5A.4 |
| normalize_analysis_v2(response, input_ids, evidence_lookup) | 纯函数验证引用并派生 primary；对 SQLite、seal、认证无依赖 | 3.2–3.5 |
| process_issue(run_context, issue_id) | 编排单 Issue，不隐藏 fatal 错误 | 4.1–4.8 |
| extract_attempt_observation(response_or_cli_events) | 只记录实际回报与本地量，unknown 不用请求值回填 | 3.8、4.5 |
| resume_agent_run(run_id) | 严格恢复 committed 未完成工作 | 5B.1–5B.2 |
| retry_issue_llm(run_id, issue_id, recover_unknown=False) | 受信任应用服务；固定请求和 evidence；CLI 的当前发送确认来自 PR4；HTTP 权限由调用边界提供 | 5B.3–5B.8 |
| require_cli_external_transfer(current_options, provider, scope) | CLI 当次许可的最小检查，不依赖 HTTP principal/allowlist；默认拒绝真实传输 | 4.10、5B.8 |
| authorize_repository_operation(principal, scope, operation) | HTTP 当前主体/范围/操作授权服务；在 PR7B retry/recover 边界调用，不逆向耦合 PR5B | 6.1–6.9、7B.7 |
| read_evidence_metadata / read_evidence_content | 分离目录与源码读取，均执行鉴权 | 7A.3–7A.4 |
| submit_issue_review(issue_id, payload, principal, idempotency_key, expected_review_version) | 认证→幂等重放→目标 IDs/版本/active 验证→原子追加；review/start 并发集成归此层 | 7B.1–7B.6 |
| project_legacy_run(run_or_legacy_record) | 明确版本的只读兼容投影，不另存权威副本 | 3.6–3.7、7A.6 |
| validate_current_result_catalog(catalog) | 校验产物、协议、计数和文档当前区 | 8.1–8.6 |

包内按表格编号推进：先模型/输入，再持久化或纯函数，再调用方，最后故障测试。可并行的纯实现必须不争用文件，并等待所依赖的接口冻结；不能先把默认路径切换，再补迁移或安全门槛。

### 23.2 单任务执行包模板

```text
任务：<例如 2A.3 显式事务迁移>
基线：<已验收依赖的 commit>
workspaceId：<本任务 worktree 的 ID>
允许修改：<唯一文件所有权范围>
前置条件：<已通过的 task/PR>
输入/输出契约：<对应 RFC 节>
必须复现：<失败样例>
实施：<最小实现与直接调用方>
验证：<命令、预期输出、fault injection>
禁止：<本任务不涉及的评分/API/provider 改动>
交付：<diff、测试结果、迁移/回退说明、commit、未验证项>
```

### 23.3 Checklist 的唯一用途

checklist 只含 `ID / dependency / status / plan link` 四列。dependency 链接本文所属 PR/工作包的前置定义；plan link 指向单一任务锚点。不复制实施内容、验收内容、状态机或来源说明。

功能与验收只修改本文；任务执行状态只在 checklist 更新。更新本文任务归属时只调整清单的定位链接。文档验收检查 ID 一一对应、链接存在、工作包依赖无环和 Markdown 结构；不把一次人工文本相同检查当作永久同步机制，也不引入专门的计划生成框架。

任务按所属工作包前置开始；工作包内以表中具体约定确定顺序，尤其 PR8 中 8.1 → F1.1 → 8.4/8.5。checklist 不单独发明第二套依赖。

## 24. 最先启动的执行队列

第一批：T0，将本版决策落入 RFC，创建 synthetic legacy fixture；重点冻结单行 attempt 字段/转换保护、CLI/HTTP 分层、Mapping、幂等与源码拒绝策略，不在评审前提前冻结旧两表 DDL。

第二批：并行 PR1A、PR2A、PR3、PR8。PR2A 无默认副作用，PR3 不依赖数据库；PR8 立即交付 F1.1 的角色更正，不等待 G0 或 G1。

第三批：PR1B 和 PR2B；两者准备完且 PR3 已验收后合入 PR4 的单 Issue 编排与最小 opt-in CLI。

第四批：**立即 G0**。使用专用 V2 DB 证明纵向运行、E7、A/B/C、requested/reported 和旧 V1 保活。G0 不等 PR8、不等 HTTP、不等完整恢复。

第五批：G0 通过后并行推进 PR5A→PR5B 和 PR6→PR7A；前者只管 attempt/CLI，后者提供 HTTP 安全/读取。PR7B 汇合后补 review/start 与撤销权限后的 HTTP retry 测试。

最后：PR8（含 F1.1）及其余第一阶段 PR 全部通过后 G1 正式发布，才更改默认协议和停止默认 legacy 源库写入。之后从 F1.2–F1.7 建设新评测边界，再 F2→F3→F4；F5/F6 按明确需求开启。

## 25. 仓库内依据、实施定位与文档核验范围

参照提交：`2a7282cd4b1e9118df2864b8e595aa4d09081dcc`。以下路径在该提交可定位；后续实现使行号变化时，以符号名和提交为准。新文件在任务表标注“新增”，不当成已存在证据。

| 仓库路径 | 定位符号/内容 | 说明 |
|---|---|---|
| `src/repo_issue_intelligence/agent_store.py` | `AgentStore._initialize/save_run/review` | legacy 表初始化、运行写入和 run-level review 的基线 |
| `src/repo_issue_intelligence/models.py` | `InvestigationReport/AgentRun/EvidenceSnippet` | 现有结果嵌套和证据字段 |
| `src/repo_issue_intelligence/agent_workflow.py` | `_traced_node/_llm_analyze_node/run_agent` | 整节点重试、批量模型调用与最终结果组装 |
| `src/repo_issue_intelligence/llm_client.py` | `_normalize_analysis/_validate_evidence_references/analyze` | component 派生、引用验证和客户端 model 来源 |
| `src/repo_issue_intelligence/codex_cli.py` | `_run_structured` | 受控 invocation 与可观察事件边界 |
| `src/repo_issue_intelligence/repository_index.py` | `_repository_files/build_repository_map` | 文件扫描与输入范围 |
| `src/repo_issue_intelligence/api.py` | repository/run/review endpoints | 现有本地 API 接口 |
| `src/repo_issue_intelligence/__init__.py` 与 `pyproject.toml` | package version | 模块与包版本来源需要统一 |
| `README.md` | `Evaluation`、官方 demo | 基线保留 17/11/172 historical tier 计数；PR8 增加当前用途说明 |
| `docs/benchmark-results.md` | 当前结果、限制、后续实验 | 当前摘要与实验沿革的仓库内记录 |
| `docs/architecture.md` | Agent runtime / boundaries | 现有同步 MVP 范围 |
| `docs/hypothesis-quality-evaluation.md` | Frozen evaluation slice / metrics | 已披露的评测性质与能力边界 |
| `benchmarks/results/candidate-pool-miss-audit-index-v25.json` | pool audit | PR8 保留并标记历史详细产物 |
| `benchmarks/results/structured-issue-components-pool40-manifest-v20-summary.json` | candidate_pool_audit | PR8 当前摘要来源之一；不假设完整 raw audit 已收录 |

本次修订只交付计划与精简清单，没有修改业务代码、迁移数据库、重跑项目测试或发起真实模型请求。工作树核验针对独立 review worktree；不能据此声称用户原 checkout 没有未提交文件。文档计数与链接检查也不能替代 PR 验收或测试。

技术实施参考（不替代本项目的功能要求）：

```text
SQLite UPDATE：WHERE 不匹配时影响零行，调用方必须检查更新结果
https://www.sqlite.org/lang_update.html
SQLite unique partial indexes：对 in_progress 行限定唯一性
https://www.sqlite.org/partialindex.html
SQLite transactions：事务内提交终态与 selected 指针
https://www.sqlite.org/lang_transaction.html
Python 3.11 sqlite3：显式事务、executescript 与 backup
https://docs.python.org/3.11/library/sqlite3.html
Git cat-file：原始对象与转换后内容的区别
https://git-scm.com/docs/git-cat-file
```

本文已有充分的任务定义和仓库定位；不依赖聊天附件或未提交旧版本才能实施。提交文档时按仓库正常 PR 流程审查，不在本次交付中宣称这些文件已进入主分支。
