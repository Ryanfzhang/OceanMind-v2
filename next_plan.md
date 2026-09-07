# OceanMind-v2 完整重构计划：Coordinator-Owned Expert Runtime

> 状态：生产链重构已完成；历史 AnalysisRun 数据仅保留只读兼容边界  
> 日期：2026-08-14  
> 本文档取代此前 `next_plan.md` 的 Adaptive Team Runtime 方案。旧方案中继续保留的
> `ExecutorTodo / AnalysisRun / FigureSpec / AnalysisPlan / required-output state machine`
> 不再是目标架构，也不得继续进入新的生产请求。

## 0. 当前实施状态（2026-08-13）

已落地：

- 生产请求统一进入 `Coordinator -> Expert -> optional Scientific Discussion Partner`，前后端均不再传递单/多智能体模式；
- 5 个领域 Expert 直接读取数据、编写并运行代码、检查输出并发布交互视图/报告；
- Coordinator 不再拥有 `AnalysisRun`、`FigureSpec`、`AnalysisPlan`、底层 publish 或数据探针工具；
- 旧 proposal-only multi-agent 协议、worker runtime 和生产路由已删除；
- 简单任务不调用 Scientific Discussion Partner，多 Expert 结果也不再强制 challenge/synthesis 仪式；
- 任何失败都先返回 Coordinator；后端不隐式重试。Coordinator 可明确继续同一 Expert；
- Canvas 只显示实际激活的角色，并以 `work_order_id` 合并同一 Expert 的续派/重试；
- 任务级代码与交付结果写入 task 工作目录，原始输入保持只读，可来自任意本地路径。
- Expert 发布的结果先是候选项；只有 Coordinator 在最终结果中明确引用的 deliverables 才进入用户回答，
  因而可同时稳定交付多个 `interactive_view` 和一个 `analysis_report`；
- 已完成、失败或信息不足的 assignment 再次普通委派只返回已有结果，不会偷偷启动新 attempt；
  Coordinator 带具体反馈继续时，同一个 Expert 只获得一次新的修订机会；
- 旧 SpatialLayer/AnalysisRun 脚本 Agent E2E 已删除，防止测试反向要求恢复旧生产工具。

历史兼容边界：旧 `AnalysisRun`、`MapScene`、`SpatialLayer` 和 proposal 表暂时只用于打开旧任务与迁移，
不在新任务的模型工具、协议或完成判断中出现。待一次独立数据迁移版本验证后再删除物理表。

验证记录（2026-08-13）：

- 非浏览器 Python 回归：`971 passed, 2 skipped`；其中旧 AnalysisRun Agent E2E 随后已删除；
- 新架构行为测试：重复委派无隐式 attempt、明确修订复用同一 Expert、多个 interactive views + report
  由 Coordinator 接受后进入回答，均通过；
- Desktop：TypeScript 检查通过，`36` 个测试文件、`186` 项测试通过；
- Plot Studio：桌面/移动空间图层、5 万点曲线、剖面、断面和 Hovmöller 视觉 E2E 通过。

## 1. 结论

上一次改造没有真正替换旧架构，而是在旧的 AnalysisRun/Executor 交付链上增加了
Coordinator 和 Expert，形成了两套重叠的控制系统：

```text
Coordinator
  -> Expert
      -> ExecutorTodo
          -> AnalysisRun
              -> execute / verify / publish / completion evaluator
                  -> Expert review
                      -> Coordinator review
```

这导致简单任务被不断扩张、同一目标被重复规划和验证、模型错误触发整链重试，并让
后端的机械状态机替代 Coordinator 判断“用户问题是否已经解决”。

本次重构采用唯一架构：

```text
用户
  -> Coordinator
      -> 0..N 个领域 Expert
          -> Expert 直接读取数据、写代码、运行代码、检查输出
      -> 可选 Scientific Discussion Partner
      -> Coordinator 判断是否满足用户目标并完成回答
```

没有专门的 Code Executor Agent。代码沙箱仍然存在，但它只是 Expert 调用的一项基础工具，
不会规划任务、不会成为 Canvas 节点、不会独立重试，也不会决定科研任务是否完成。

本次抛弃的是旧的**控制架构**，不是删除安全沙箱、任务隔离、证据存储和可追溯性等有用基础设施。

## 2. 不可妥协的架构原则

1. **只有一条运行链**：生产请求只能进入 Coordinator–Expert–Scientific Discussion，不保留新旧双轨。
2. **Expert 自己完成工作**：读数据、写代码、执行、查看错误、修改和自检均发生在同一个 Expert session。
3. **Coordinator 拥有语义验收权**：后端只能验证引用存在、文件可打开、权限安全等机械事实，不能决定科研问题是否得到解决。
4. **Scientific Discussion Partner 是可选讨论者**：只用于机制、解释、备选假设与研究策略讨论，不是验收者或完成门槛。
5. **同一 Agent 不因 retry 复制**：代码修复、补充证据和 Coordinator 退回修改都复用同一个 Expert session 和 Canvas 节点。
6. **结果要求使用能力语义**：例如 `interactive_view`、`analysis_report`、`dataset`、`notebook`，不要求 Agent 依次创建 MapScene、SpatialLayer、FigureSpec 等内部包装。
7. **任务复杂度由目标决定**：简单问题不进入完整科研工作流；复杂问题允许 Coordinator 多轮派发、收集、讨论和再派发。
8. **本地数据优先、只读输入**：数据可以位于 task 目录之外，进入任务时以只读路径或快照挂载；代码和输出写入 task 工作目录。

## 3. 新的角色模型

系统保留 5 个领域 Expert 和 1 个 Scientific Discussion Partner，但只激活当前任务真正需要的角色。

### 3.1 Coordinator

职责：

- 理解用户目标、现有任务上下文和数据；
- 能直接完成目录清单、已有结果汇总、状态查询等不需要代码的工作；
- 将需要专业判断或代码的子问题交给最合适的 Expert；
- 可并行派发相互独立的任务，也可在收到结果后继续派发下一轮；
- 对 Expert 结果给出接受、退回修改、补充另一专家、询问用户或终止的决定；
- 必要时与 Scientific Discussion Partner 讨论候选解释、替代机制和区分证据；
- 组合最终自然语言回答，并在正文适当位置插入交付结果链接；
- 只有 Coordinator 能把整个用户任务标记为完成。

Coordinator 不负责：

- 为普通任务预建 AnalysisPlan、FigureSpec 或 AnalysisRun；
- 替 Expert 猜测代码结果；
- 因一次失败重新创建同角色 Agent；
- 用后台机械检查代替目标判断。

### 3.2 Data & Reproducibility Expert

负责实际数据内容、格式、变量、维度、坐标、单位、时间和空间覆盖、缺测、网格、版本、
路径、读取方法与复现信息。需要探测时自己写并运行代码。默认不做全目录 SHA-256；只有用户、
复现目标或数据身份冲突确实需要时才计算内容哈希。

### 3.3 Ocean Process & Mechanism Expert

负责物理问题、尺度、诊断量、机制假设、方程、符号、单位、边界条件和物理一致性。
需要计算预算、梯度、输运或诊断量时自己编写并运行代码。

### 3.4 Statistical Inference Expert

负责估计量、比较、权重、依赖结构、基线、异常、效应量、不确定性、稳健性和统计解释边界。
需要计算或模拟时自己编写并运行代码。

### 3.5 Literature & Reproduction Expert

负责检索文献、提取可复现方法、数据来源、参数、目标图表和作者声明，区分原文证据与系统推断。
复现需要代码时可直接执行；不再把代码交给另一个 Executor。

### 3.6 Visualization & Communication Expert

负责静态图、可交互图、地图范围、投影、图层、色标、单位、悬停数值、联动选择和结果叙事。
它直接生成可打开的交互视图和报告，不需要先发布 MapScene/SpatialLayer/Figure 三套用户可见对象。

### 3.7 Scientific Discussion Partner

读取用户目标、Coordinator 候选解释、冻结的 Expert 结果和证据，挑战假设、提出替代机制或方法、
保留分歧并提出能够区分不同解释的新证据。它不验收 Expert，不管理代码执行，不启动 Agent，
也不修改产物。发送方响应是否完整由接收方 Contract 校验；最终决定由 Coordinator 作出。

## 4. 唯一任务协议

### 4.1 Coordinator 给 Expert 的 Assignment

模型可见协议使用语义字段；任务 ID、版本、路径、预算和能力权限由后端补齐，不允许 Coordinator
在 assignment 中手工拼接：

```text
ExpertAssignment
  expert_profile
  question                  # 需要该 Expert 解决的明确问题
  context_summary           # Coordinator 已知事实，不复制整个会话
  source_handles[]          # Task Source 语义句柄；单来源时后端自动选择
  outcome_intents[]         # 希望得到的能力，可为空
  constraints[]             # 只读、时间范围、用户指定方法等
  done_when                 # 自然语言验收说明
```

`outcome_intents` 是能力要求，不是底层 artifact 类型。例如：

```text
answer
dataset_summary
interactive_view
analysis_report
derived_dataset
notebook
source_file
```

示例：可视化任务可声明至少一个 `interactive_view` 和一个 `analysis_report`；数据清单问题
只需要 `answer` 或 `dataset_summary`。后端不从关键词硬编码推断这些要求，由 Coordinator 根据
用户目标明确声明。

### 4.2 Expert 返回给 Coordinator 的结果

```text
ExpertResult
  status                    # completed / incomplete
  decision                  # accepted / needs_revision / insufficient_evidence / blocked
  answer                    # 领域结论
  evidence_refs[]
  deliverable_refs[]
  method_summary
  checks_performed[]
  limitations[]
  unresolved_questions[]
  suggested_next_step
```

Expert 的 `done` 表示“我认为我的 assignment 已完成”，不代表整个用户任务完成。Coordinator 可以：

- `accept`：纳入综合；
- `revise`：把具体反馈发回同一个 Expert session；
- `extend`：追加新的子问题；
- `add_expert`：调用另一领域角色；
- `ask_user`：缺少必要输入；
- `stop`：确认无法继续。

接收方先验证响应字段是否齐全。若缺字段，只允许发送方重生成一次响应，复用既有代码、证据和产物；
不得重新计算。第二次仍缺字段则作为不完整结果返回 Coordinator。语义证据不足不是 Contract 缺字段，
由 Coordinator 决定是否显式继续同一 Expert。

### 4.3 Coordinator 完成任务

Coordinator 最后调用一个简单的结果边界：

```text
CoordinatorResult
  decision                  # answered / needs_user / blocked / failed
  answer_basis              # workspace_catalog / expert_evidence / general_knowledge
  answer_markdown
  evidence_refs[]
  deliverable_refs[]
  limitations[]
  confidence
```

后端只检查：引用是否属于当前 task、文件是否存在、interactive view 是否能加载、是否违反权限。
它不通过 output slot、AnalysisRun 状态或验证条目推翻 Coordinator 的科研判断。

## 5. Expert 直接执行代码

### 5.1 Expert 工具集

每个 Expert 根据权限获得同一组基础能力的适当子集：

- 查询 task catalog 和已挂载数据；
- 浏览目录、读取小样本和元数据；
- 在自己的 task worktree 中创建和修改代码或 notebook；
- 调用 `run_code` 在隔离环境执行；
- 查看 stdout、stderr、退出码、资源使用和输出文件；
- 再次修改并执行；
- 把有意义的结果发布为 deliverable；
- 查询 manuals、references 和允许的 web search。

`run_code` 是普通工具，不是 Agent：

```text
Expert session
  -> write analysis.py
  -> run_code
  <- stdout / stderr / files / resource usage
  -> inspect result
  -> fix code if needed
  -> run_code again
  -> ExpertResult
```

### 5.2 CodeExecution 记录

用简单的 `CodeExecution` 审计记录替代 AnalysisRun：

```text
CodeExecution
  execution_id
  task_id
  assignment_id
  expert_session_id
  code_snapshot
  command
  environment_fingerprint
  input_refs[]
  stdout_ref / stderr_ref
  output_files[]
  exit_code
  resource_usage
  started_at / ended_at
```

它只用于安全、复现和排错，不参与 Agent 调度，不要求 FigureSpec/AnalysisPlan，不拥有“科研完成”状态，
也不会生成独立 Canvas 节点。

### 5.3 文件布局

```text
<task-root>/
  task.json
  inputs/                  # 外部文件的只读挂载信息或不可变快照引用
  work/
    <expert-session-id>/
      analysis.py
      notebook.ipynb
      logs/
  outputs/
    views/
    reports/
    data/
    files/
```

原始数据可以在任意本地路径；系统记录并只读挂载，不强制复制到 task 根目录。Expert 的代码、日志、
notebook 和结果始终放在当前 task 下，避免所有 task 共用一个不可见工作区。

## 6. 用户级结果与内部证据

### 6.1 用户级 deliverable

分析/可视化任务主要使用两种用户级结果：

1. `interactive_view`：点击后直接在右侧 Workbench 打开，可缩放、悬停查看数值和切换图层；
2. `analysis_report`：包含结论、静态图或预览、方法、数据来源、局限、代码/notebook 路径和证据。

只有任务需要时才生成它们。普通问答、数据清单或概念解释可以没有任何 deliverable。

通用的 `dataset`、`notebook`、`source_file` 等作为附件或证据存在，不强制显示为主结果卡。

### 6.2 多个交互结果

一次分析产生多个不同视图时，Coordinator 在最终回答对应段落中插入每个
`interactive_view` 的精确引用。用户点击正文链接即可在右侧打开；回答末尾保留一个
`analysis_report`。不要求用户先打开 delivery group，也不把多个独立视图错误合并。

### 6.3 删除重复 artifact 语义

新的生产链不再把以下对象作为用户级交付或执行通行证：

- MapScene；
- SpatialLayer；
- LinkedPlot；
- FigureRequest；
- FigureSpec；
- AnalysisPlan；
- AnalysisRun。

渲染器内部仍可使用图层、场景、静态图片或表格结构，但它们属于一个
`interactive_view` 的实现细节。前端和 Coordinator 只处理能力级 deliverable。

## 7. 调度流程

### 7.1 简单目录/数据清单

```text
用户问“我有什么数据”
  -> Coordinator 查询 task catalog
  -> 已有元数据足够：直接回答
  -> 需要读取真实内容：派一个 Data Expert
  -> Data Expert 有界探测并返回
  -> Coordinator 回答
```

禁止自动扩张为全量哈希、三份数据逐文件比对、AnalysisRun 或 Scientific Discussion。只有用户目标确实要求
数据一致性或可复现身份时才做完整哈希。

### 7.2 单领域分析

```text
Coordinator
  -> 一个最合适的 Expert
  -> Expert 自己写代码、运行、自检
  -> Coordinator 接受或退回同一 Expert
  -> 最终回答
```

### 7.3 复杂科研分析

```text
Coordinator
  -> Data Expert：确认数据事实
  -> Ocean Expert + Statistical Expert：可并行分析
  -> Coordinator 收集后发现缺口
  -> 继续原 Expert 或加入 Visualization/Literature Expert
  -> Scientific Discussion Partner 讨论候选解释（仅在有必要时）
  -> Coordinator 定向退回责任 Expert
  -> Coordinator 综合并完成
```

这不是一次生成固定 DAG 后机械执行到底。Coordinator 每收到一批结果都可以重新推理并决定下一步，
与 AgentCore 风格的动态委派一致。

### 7.4 论文复现

Literature Expert 固定方法与目标，Data Expert 核对输入，相关 Ocean/Statistical Expert 各自直接执行
需要的代码，Visualization Expert 生成结果视图；需要讨论解释或复现差异时再调用 Scientific Discussion Partner。Coordinator 判定完全复现、
部分复现、无法复现或目标发生偏差。

## 8. 错误、修改与重试

### 8.1 不属于 Agent retry 的情况

- Python 报错；
- reader 不支持当前格式；
- 参数或单位错误；
- 输出缺一项；
- Coordinator 根据已收集结果要求补充检查；
- Coordinator 对结论不满意。

这些都由同一个 Expert session 接收具体反馈后继续工作。Canvas 仍显示同一个节点；数据库记录新的
agent turn 或 CodeExecution，而不是新建 Agent。

### 8.2 Transport / provider 故障

provider timeout、rate limit、连接中断等故障也不触发后端隐式重试：

- 当前证据和 typed failure 立即返回 Coordinator；
- Coordinator 若明确继续，则使用相同 `expert_session_id` 和 assignment；
- 从最近 checkpoint 继续，不重新运行已成功的代码或下载已有输入；
- Canvas 不增加节点；
- Coordinator 也可以等待、换方法、继续其他任务或告知用户。

### 8.3 时间与预算

- 模型调用、代码执行、下载和等待分别计时，不共享一个 900 秒总闸门；
- 每次模型调用有独立 provider timeout；
- `run_code` 有 CPU、内存、命令 wall time 和输出大小限制；
- task 可以在后台持续，切换前端 task 不取消运行；
- budget 使用 `quick / standard / deep` 三档，并允许 Coordinator 显式升级；
- token 上限可以保持充足，但 UI 和日志必须显示当前是在模型推理、运行代码、读数据还是等待网络；
- 上下文采用结果摘要和引用，不在每轮复制完整工具输出、文件清单和历史 transcript。

## 9. Memory 与上下文

### 9.1 分层但不重复

- Task memory：用户目标、已接受事实、deliverables、未解决问题；
- Coordinator memory：assignment 状态、Expert 结论摘要、讨论中的替代解释；
- Expert memory：自己的 assignment、代码、执行结果和 Coordinator 反馈；
- CodeExecution：不可变技术记录，不作为自然语言上下文全文回灌。

Coordinator 只向 Expert 发送该 assignment 所需上下文和精确 refs。Expert 返回压缩后的结果；大文件清单、
日志和数组保留为引用。这样避免一次任务反复累积数十万输入 token。

### 9.2 Session 身份

每个 assignment 绑定一个稳定的 `expert_session_id`。修改、补证、网络恢复都继续该 session；只有新的
专业子目标或 Coordinator 明确重新分配角色时才创建新 session。

## 10. 前端与 Canvas

- 初始只显示 Coordinator；
- 实际调用一个 Expert 时动态增加一个节点；
- 同一 Expert 的代码执行和修改不生成新节点；
- 调用 Scientific Discussion Partner 时才增加该节点；
- 当前交互显示流动连线，历史交互显示普通线；
- 点击节点查看职责、本次 assignment、已执行的动作摘要、代码运行、证据、限制和当前状态；
- 代码运行属于 Expert 详情中的 activity，不显示 Executor；
- Working 下半部展示可读进度，最终回答出现后隐藏；团队拓扑可保留；
- provider 等待、代码运行、数据读取、用户输入等待必须是不同状态，不能统一显示
  `Preparing the analysis workspace...`；
- task 切换只改变可见视图，不取消后台任务。

## 11. 需要从生产路径删除的旧结构

### 11.1 Agent 与协议

- `lightweight_executor` / `Scientific Code Executor` profile；
- `ChildAuthority.EXECUTOR` 的生产调度含义；
- `ExecutorTodo`、`ExecutionSpec`、`ExecutionInputRef`；
- `ExpertExecutorReview`；
- Expert 创建/审查 Executor 的工具；
- Executor lease、Executor WorkOrder 和 retry instance；
- `build_ocean_executor_runtime`。

### 11.2 执行控制

- `analysis_run_create`；
- `analysis_run_execution_contract`；
- `analysis_run_execute`；
- `analysis_run_verify`；
- `analysis_attempt_accept/reject`；
- AnalysisPlan/FigureSpec 作为运行或发布前置条件；
- checks-passed/draft 状态决定 Agent 是否可提交结果；
- required output slot Completion Evaluator 决定科研任务完成；
- AnalysisRun 驱动 artifact publication 的强绑定。

### 11.3 重复展示

- MapScene/SpatialLayer/Figure/LinkedPlot 的用户级并列卡片；
- delivery family 排名和为了修补重复卡片而存在的复杂归并逻辑；
- 从旧 artifact 类型反推用户交付能力的主要运行路径。

## 12. 保留并重用的基础设施

以下代码可以重用实现，但不得继续拥有旧的控制语义：

- Deep Agents + LangGraph 模型循环、流式事件、取消和 checkpoint；
- 本地代码 sandbox 与资源限制；
- task 隔离、只读数据挂载和 task 根目录输出；
- 数据 catalog、不可变数据身份和 provenance；
- artifact/file store 的底层文件与版本能力；
- permission、下载确认和人类批准；
- background task、切换恢复和 durable event stream；
- Workbench 的交互渲染能力；
- manuals、references 和 web search。

重用意味着抽取底层库，不允许给旧 AnalysisRun/Executor 路径再加适配器并继续运行。

## 13. 数据库与迁移

建立新表或等价存储：

```text
agent_sessions
expert_assignments
expert_results
agent_events
code_executions
deliverables
task_reviews
coordinator_results
```

迁移原则：

1. 新代码先实现新 schema 和新 runtime；
2. 切换时所有新请求只写新表；
3. 对历史完成任务执行一次离线迁移：把可打开地图转成 `interactive_view`，把结论、静态图、代码和证据转成 `analysis_report`；
4. 历史 AnalysisRun/attempt 只作为只读 provenance 归档，不参与 continuation；
5. 切换时仍在运行的旧请求标记为 `legacy_incomplete`，保留文件和证据，但不自动从旧状态机恢复；
6. 迁移完成后删除运行时 legacy adapter；旧表可在一个版本周期后归档或删除；
7. 不允许根据数据库中是否存在旧记录选择旧执行路径。

这是一次数据迁移，不是长期双轨兼容。

## 14. 实施顺序

### Phase 0：冻结旧架构

- 停止继续修补 Executor/AnalysisRun 流程；
- 为当前行为保存只读诊断 fixture，作为“不得回归”的反例；
- 写 ADR，明确本文件的单一运行链和删除清单；
- 暂停更新依赖旧流程的 frozen eval 文案。

完成标准：任何新设计评审不再以“如何让 ExecutorTodo 更稳定”为问题前提。

### Phase 1：新领域模型

- 新增 `ExpertAssignment`、`ExpertResult`、`AgentSession`、`CodeExecution`、`Deliverable`、`TaskReview`、`TaskFinish`；
- 建立数据库迁移和 repository；
- 定义简短 JSON schema；
- 实现 task-scoped 文件路径和外部本地数据只读挂载。

完成标准：模型层不依赖 AnalysisRun、FigureSpec、AnalysisPlan 或 ExecutorTodo。

### Phase 2：Expert 直接运行代码

- 给 Advisor runtime 增加 task worktree 文件编辑与 `run_code`；
- 将 sandbox 的执行结果直接返回当前 Expert；
- 保存 CodeExecution 审计记录；
- 实现同一 Expert session 的代码修复循环；
- 为不同 Expert 配置职责 prompt 与最小工具权限。

完成标准：Data Expert 可以在没有 Executor Agent、AnalysisRun 和 AnalysisPlan 的情况下读取本地 Zarr，写探测代码并把结果返回 Coordinator。

### Phase 3：动态 Coordinator

- 用统一的 `ocean_assign(start/continue)` 和 Coordinator result boundary 替换复杂 team tools；
- Coordinator 每收到结果后重新推理，可继续原 Expert、加新 Expert、调用 Scientific Discussion Partner 或结束；
- 实现 Coordinator 语义验收；
- 实现 typed transport failure 与 Coordinator 显式同-session continuation。

完成标准：代码错误不会新建 Agent；网络恢复不会重新执行已成功步骤；没有 backend completion evaluator 替 Coordinator 做科研判断。

### Phase 4：交付物简化

- 建立 `interactive_view` 和 `analysis_report` 发布接口；
- interactive view 直接绑定右侧 Workbench；
- report 汇集静态结果、来源、方法、代码、证据与局限；
- 支持最终回答中任意位置嵌入多个 view 链接；
- 移除新任务对 MapScene/SpatialLayer/LinkedPlot/FigureSpec 的依赖。

完成标准：需要可视化的任务可返回多个可点击交互视图和一个报告；普通问题不生成空卡片。

### Phase 5：前端状态与 Canvas

- Canvas 只展示实际 Coordinator、Expert 和 Scientific Discussion Partner；
- 删除 Executor 展示和 retry 复制；
- 以 agent event 驱动 activity、连线和详情；
- 区分模型等待、工具运行、代码执行、数据 I/O、用户输入和网络恢复；
- 保证 task 切换不取消后台任务。

完成标准：用户能准确看到是谁在做什么；不会长时间只看到假的 `Preparing workspace`。

### Phase 6：单次切换与旧代码删除

- 将桌面端和 backend router 原子切换到新 runtime；
- 从 tool registry 移除全部 analysis_run/executor 工具；
- 删除 Executor profile、orchestrator 分支、lease 和 completion evaluator；
- 解除 artifact publishing 对 AnalysisRun 的强绑定；
- 重写旧测试和 eval，删除只验证旧合同的测试；
- 执行历史任务离线迁移；
- 确认生产代码不存在旧路径 fallback。

完成标准：`src/oceanx` 的生产 runtime 不再引用 `ExecutorTodo`、`lightweight_executor`、`build_ocean_executor_runtime` 或 `analysis_run_*` 工具。

### Phase 7：真实任务验收

使用真实模型和真实本地数据执行下列端到端测试，并记录总时间、模型轮次、token、工具调用、代码执行和失败恢复：

1. “我有什么数据”；
2. 查看一个 1.9GB 本地 Zarr 的变量、坐标和范围；
3. 对一个已有数据做平均并生成交互地图与报告；
4. 一次分析产生多个 interactive views；
5. 代码首次报错后同一 Expert 修复；
6. provider 连接中断后同一 session 恢复；
7. 海洋机制 + 统计不确定性的多 Expert 分析；
8. 论文结果复现和 Scientific Discussion Partner 对替代解释的质询；
9. task 切换、应用重启和后台恢复；
10. 缺少关键输入时向用户提问而不是盲目执行。

## 15. 性能与行为验收指标

### 简单数据清单

- catalog 足够时不启动 Expert；
- 不启动代码沙箱；
- 不计算全量文件哈希；
- 不生成 AnalysisPlan、FigureSpec、AnalysisRun 或 deliverable；
- 一次 Coordinator 回合内完成。

### 数据内容探测

- 最多激活一个 Data Expert，除非发现明确的跨领域问题；
- Expert 直接运行有界探测代码；
- 默认只读 metadata、必要的小样本和目录结构；
- 代码错误在同一 session 修复；
- Canvas 始终只有一个 Data Expert 节点。

### 可视化分析

- Coordinator 明确要求时，至少返回可打开的 `interactive_view` 和 `analysis_report`；
- backend 检查链接和文件真实可用；
- Coordinator 判断科学内容是否满意；
- 不需要发布三份等价 artifact 才能完成。

### 复杂任务

- Coordinator 可多轮动态派发；
- Expert 之间通过 Coordinator 共享已接受结论和 refs，不复制完整上下文；
- Scientific Discussion Partner 提出替代解释；是否补证由 Coordinator 决定；
- 单个 Expert 失败不清空其他已完成结果；
- 不因同一代码错误累计多个同名 Agent。

### 可观测性

- 后台和 UI 都能看到当前 phase、active agent、model call、tool call、code execution、I/O 和 provider wait；
- 每个 assignment 记录耗时、token、工具次数和代码运行次数；
- 超过预算软阈值时 Coordinator 收到摘要并决定继续或停止；
- 不再出现几十万 token 和几十次工具调用后仍无法说明正在做什么的情况。

## 16. 必须通过的架构测试

1. Expert runtime 的 tool registry 中有 `run_code`，没有 `ocean_expert_executor_todo` 和 `analysis_run_*`；
2. Coordinator 无法创建 Executor Agent；
3. 同一 assignment 的 revise、代码报错和 network retry 保持同一 `expert_session_id`；
4. backend 不会因为缺少 FigureSpec/AnalysisPlan 拒绝代码执行；
5. backend 不会因为 CodeExecution 未进入某个科研状态拒绝 ExpertResult；
6. Coordinator 可以接受无 deliverable 的普通回答；
7. 当 assignment 明确要求 interactive view/report 时，缺失引用会作为机械交付缺口返回 Coordinator，而不是重启 Expert；
8. Scientific Discussion Partner 只能读冻结证据并提交讨论结果，不能修改代码或验收结果；
9. 历史 artifact 迁移后可查看，但不能触发旧 runtime；
10. `rg` 和 tool-registry 测试证明生产路径没有 Executor/AnalysisRun fallback。

## 17. 明确不做的事情

- 不为“查看数据”“SST 地图”或某种文件格式写专用 Agent 流程；
- 不通过增加 prompt 让旧状态机勉强工作；
- 不保留 Standard/Team 两种前端模式；
- 不让后端根据固定 artifact 数量判断科研是否满意；
- 不把代码沙箱包装成另一个会规划和重试的 Agent；
- 不在生产环境长期维护新旧两套 runtime；
- 不默认对大型目录做完整哈希或逐文件深度解析；
- 不让切换 task、关闭右侧视图或隐藏 Canvas 取消后台工作。

## 18. 最终完成定义

只有同时满足以下条件，才算本次架构重构完成：

- 所有新请求只走 Coordinator–Expert–Scientific Discussion；
- Expert 能直接写、运行、检查和修复代码；
- 生产路径不存在 Executor Agent 和 AnalysisRun 控制链；
- Coordinator 对任务是否满足用户目标拥有最终语义决定权；
- backend 只负责安全、存在性、可打开性、任务归属和审计；
- 简单数据查询不会触发科研执行链；
- 复杂任务可以多轮、多 Expert、可审查地完成；
- 可视化按需要输出正文内的 interactive views 和末尾 analysis report；
- retry 不复制 Agent、不重跑已完成工作；
- 历史任务经过一次迁移可继续查看，但不能唤醒旧 runtime；
- 上述真实 E2E 场景全部通过，且性能记录可解释每一分钟和每次工具调用。
