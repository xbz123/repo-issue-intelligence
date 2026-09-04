# Protocol v2 R5 导入与可行性复核

日期：2026-09-05。源码参照：`2a7282cd4b1e9118df2864b8e595aa4d09081dcc`。

结论：R5 可作为 T0 的输入，未发现阻止开始 T0 的架构问题；这不是对尚未实现的
V2 Store、恢复或安全接口的验收。全部 153 项任务仍为 planned，G0/G1 未执行。

## 本次文档更新

- 导入 [R5 完整计划](repo_issue_intelligence_protocol_v2_execution_plan_r5.md) 与
  [R5 任务索引](repo_issue_intelligence_protocol_v2_task_checklist_r5.md)，保留下载内容不变。
- README 指向 R5 作为当前提案；本地既有 R4 草案保持原样，不纳入此次提交。
- [结构核验记录](repo_issue_intelligence_protocol_v2_r5_document_validation.json) 是本次实际检查结果，
  不复用外部文档声称的验证成绩。

## 可行性依据

- 单表 attempt 的请求不可变、一次条件终结、终态只读及 selected 指针原子更新定义清楚。
  SQLite、现有 Pydantic 与短事务即可实现，无需新增 ORM 或事件溯源框架。
- PR2A 不改变旧写路径，PR2B 对新目标开放迁移，PR4 才提供 opt-in 运行；每步有独立验收门。
- PR3 使用标准 Mapping，不依赖 SQLite；PR4 提供 CLI 传输确认，PR5B 无需等待 HTTP 鉴权。
- PR7B 在恢复链和安全链汇合后测试 review/start、权限撤销和幂等更正；审查目标无需 report revision。
- PR8 承接 F1.1，既保留历史 tier，也更正当前开发/回归用途；清单不再复制任务定义。

严重度校准：两表存储本身并不构成 P1；正确的事务和约束也能维护两表一致性。
上一轮将结构选择本身定为 P1 偏重。R5 的单表方案是较易维护的取舍，而不是唯一安全实现。

## T0 内应具体化的已有验收项

这些是现有工作包的实施边界，不要求再新增 PR 或重写 R5：

| 位置 | 要具体化的测试 | 通过条件 |
|---|---|---|
| T0.3、5B.2、5B.7 | deterministic 已提交但 evidence 未 seal；已 seal 但无 attempt；attempt 无终态 | 按阶段继续同一 Issue，不因已有 report 跳过其后续工作；unknown 不自动重发 |
| 4.10、5A.5、5B.7 | resume/resume、resume/retry、父进程退出但 CLI 子进程仍活跃 | run 编排仍有单执行者；不能仅凭唯一 active 索引、TTL 或 PID 不存在认定执行已停止 |
| T0.7、1B.1、5B.2 | 初次 committed 捕获要求 clean；之后原 checkout 已变更或出现新文件 | resume 从原 commit/scope 重建，不重选 HEAD，也不把当前 checkout 字节混入原运行 |

单表约束、受影响行数检查、指针回滚、跨 Issue 外键、请求字段写保护与幂等竞争仍须由
PR2A/2B/5A/7B 的真实 SQL 和跨进程测试证明。应用检查不能替代数据库约束，反之亦然。
这些检查不需要调用真实模型。

## 本次验证范围

- 153 个任务 ID 唯一，完整保留 R4 ID，计划与索引一一对应，全部 planned。
- 第一阶段 122 项、第二阶段 31 项；F1.1 仅归 PR8。
- 21 个工作包/门节点，声明依赖无环；清单 307 个本地文件/显式锚点链接有效。
- 15 个源码/文档/产物路径在参照提交中存在；两份导入文件与下载文件逐字节相同。
- Markdown fence、尾随空白及 Git diff 检查通过；没有新增内容哈希校验。
- 未运行 V2 功能测试、数据库迁移、真实模型请求或 200-case benchmark；此次不修改业务代码。

下一步是 T0：冻结接口、阶段状态和合成验收样例。T0 验收后再按 R5 的现有依赖推进，
先达到 G0 的可运行 CLI，再实现完整恢复与 HTTP；不把导入计划计作实施任务完成。
