# OceanX 协作方式重设计计划

日期：2026-09-11

状态：P1–P4 已接入代码并通过离线回归；P5 的真实模型实验尚未启动。实现说明与验证结果见第 15 节。

目标：把 Coordinator 从“事前规定步骤、事后验收清单”转为研究负责人，让 Expert 在明确的问题和证据边界内自主选择、调整检验路径。

## 1. 核心决定

**Coordinator 每次收到 Expert 结果，都应该想：这个结果让我们知道了什么，又暴露了什么值得进一步分析的问题？**

这不是要求每次都继续，也不是要求每次产生新假设。它要求研究方向由新证据驱动，而不是由旧清单是否打完勾决定。

职责边界：

| 对象 | 负责人 | 权力与边界 |
| --- | --- | --- |
| 研究问题、允许的目标范围、证据标准 | Coordinator | 定义要回答什么、回答到什么程度；不得用方法清单代替证据标准 |
| 方法选择与问题内的检验 | Expert | 可自主新增、替换分析路径；不必每换方法就请求批准 |
| 新机制假设或实质目标扩展 | Coordinator | 阅读线索后批准、暂存或不追；超出用户授权时先询问用户 |
| 科学解释、局限与证据充分性 | Expert 提供分析，Coordinator 综合负责 | 不是后台可自动裁决的问题；复核者可质疑解释，但不替 Coordinator 写树状态 |
| 假设状态、最终研究判断 | Coordinator | 先判断证据，再写状态；数值复核、目标数量、专家数量都不自动构成确立 |
| 权限、字段、引用、预算、幂等与生命周期 | 后台 | 执行预先约定的结构不变量，不根据关键词或数字个数判断科学正确性 |
| 文件保存、图表发布、notebook、下载链接 | 原交付链路 | 独立保持，不纳入本次架构重写 |

必须同时成立：

1. 放开路径，不自动放开目标和结论强度。
2. 有新线索不代表本轮未完成；交付不能被可选探索一直拖住。
3. 回答完成不等于某个假设被证实；否定性结果也可以充分回答问题。
4. 重要判断在现有对话/事件中留有依据，不强制另交审查表才能继续或结束。
5. 简单问题允许直接回答、少量内部检查，不强制建立假设树、创建备择、产出 leads。

### 1.1 实施约束：不要把研究流程写成后台规则

按最新确认，后台只做必要支撑：

| 保留在后台 | 交给 Coordinator / Expert 的提示词与 Skill |
| --- | --- |
| 角色与工具权限、基本数据格式、已有任务/文件归属检查 | 方法是否合理、证据是否独立、局限是否关键 |
| 原有预算、取消、恢复、幂等与会话身份 | 是否值得继续、是否应追线索、是否已充分回答 |
| 记录模型的决定和证据、传递报告与输出 | 结论层次是否恰当、正文有无越界、何时修正状态 |

此前已经约定并存在的 `established` 多目标结构门槛保留，本轮不顺带扩展其他科学门槛。**不新增 level/max_level 硬拦截、必填审查 ID、审查 revision 锁、逐个 Test 预登记关卡、自动退回或独立提交验收流程。** 字段用于传递信息，不用于让后台替模型判断研究。

下文“应说明”“先审查再更新”等科学工作要求默认属于提示词/Skill；除明确列出的权限和基础接口要求外，不翻译成拒绝执行的后台校验器。

## 2. 实施前基线与要改变的地方

以下记录实施前读取的仓库基线；已落地的变化见第 15 节，不根据过去讨论推测：

| 现状 | 主要位置 | 改进含义 |
| --- | --- | --- |
| WorkOrder 同时有 task_goal、constraints、outcome_intents、done_when | `src/oceanx/team/models.py`、`src/oceanx/tools.py` | 区分问题、必要结果、可选方法；不能再把方法猜测塞进 done_when |
| ExpertResult 有执行状态、expert_decision、confidence、自由文本和输出 | `src/oceanx/team/models.py` | 执行状态保留；增加明确证据报告，不把专家自评当科学验收 |
| coordinator_payload 当前仅传 text/outputs；WorkOrder 提示与执行清单采用显式字段投影 | `team/models.py`、`team/orchestrator.py`、`expert_execution.py` | 新增模型字段不会自动进入科学交接，必须端到端传递并测试 |
| 同一 Expert 实例已有 job_key、expert_key、session_round | `src/oceanx/team/orchestrator.py`、`src/oceanx/backend/router.py` | 复用已有同会话续做，不从零另建会话管理器 |
| Expert 历史已有压缩与恢复逻辑 | `src/oceanx/expert_context.py`、`src/oceanx/deep_runtime.py` | 新报告要进入紧凑上下文，不把全部原始轨迹重复塞回去 |
| Test 已预先声明 targets/discriminates/effects，record 会应用效果 | `src/oceanx/exploration.py` | 必须拆开“保存检验结果”和“科学判断后改变假设状态” |
| 当前正式 Tree 工具由 Coordinator 使用，research_test_ids 在委派前被剥离 | `src/oceanx/tools.py`、`src/oceanx/runtime.py` | Expert 的受限 Test 登记需要真正的权限设计，不是只修改提示词 |
| supported/contested 非终态；根节点全终结后才允许完成 | `src/oceanx/exploration.py` | 要解耦问题可回答性与假设终态，消除为了结束而凑 established 的压力 |
| 已有 unable_to_answer，以及 CoordinatorResult 的 insufficient_evidence 等决定 | `src/oceanx/exploration.py`、`src/oceanx/team/models.py` | 修正路由与语义映射，而不是再平行创建一套“无法回答”协议 |
| 普通最终文字目前默认生成 ANSWERED 回执；回执还能用于重启恢复 | `backend/router.py`、`backend/store.py` | 结束一次请求与科学问题已充分回答必须区分；普通结束和恢复不能绕开 Coordinator 的实际判断 |

### 2.1 不在本次重设计范围内

- 不替换 ExpertOutput、结果文件引用、哈希校验、代码执行沙箱、图表 renderer、notebook 保存或前端下载链路。
- 不增加固定 Agent 数量，不把所有任务改成专家并行，不新增固定复盘 Agent。
- 不改变技能由 Agent 自己选择的原则，不重做 Skill Curator/self-evolving。
- 不通过强制更多检验、更多节点、更深树来定义研究质量。
- 不混入模型更换、数据窗口更换、query 改写或 token 预算放宽来证明委派方式有效。
- 不改动已有实验原始记录，不迁移或重跑用户的历史任务来制造“新协议成功”。

交付不重写不等于不测试：必须验证新协议最终仍能调用原交付路径。

## 3. 三个语义先分开

### 3.1 证据方向、结论层次、回答充分性

这三者不能压成一个 strength 或一个树状态：

- `direction`：supports / opposes / mixed / undetermined。不是有序等级。
- `level`：description / association / mechanism_consistent / causal_attribution。用于声明推断层次和本轮授权上限，不代表置信度。
- `sufficiency`：当前证据是否已经在用户要求的层次上回答了问题，由 Coordinator 给出有依据的判断。

“无显著关系”可能回答了描述/关联问题，也可能只是检验功效不足；由证据和局限决定，不能按反对或无法判定字段自动结束。

同样，字段写 mechanism_consistent 不等于已经拥有机制层证据。level/max_level 是模型理解证据边界的语言，Coordinator 对照正文和证据判断；本版不新增字段等级比较的后台拦截。

### 3.2 问题完成与假设状态

保留 untested / supported / contested / refuted / established / unverifiable 的科学状态含义，但不再用“所有根假设终结”作为唯一完成条件。

允许：

- 关联问题已得到足够回答，目标假设保持 supported，研究仍可 answered。
- 充分反驳一个命题后 answered；不能要求至少一个假设成立才算回答。
- 现有证据不足、关键缺口不可弥补时 insufficient_evidence，并映射 Tree 的 unable_to_answer 展示。
- 预算、取消、服务异常导致 incomplete/interrupted；不能写成科学上的 unverifiable 或 refuted。
- 可选线索仍未测试，但必需问题已回答；允许结束并列出未来方向。

描述性数字不强制建 Hypothesis。普通任务没有 Tree 时，以 WorkOrder 的稳定问题标识和 evidence refs 保存判断。

### 3.3 targets、Test 数量、独立性

- targets 是被检验的假设节点，不是专家数量、检验次数或数据源数量。
- 保留既有直接 established 的 `distinct targets >= 2` 结构门槛；它只是否定门槛，不能作为自动升级条件。
- 单目标问题不需要为了任务结束添加第二个假设；可以在 supported 或 refuted 等适当状态结束问题。
- 是否独立，检查是否重复使用同一信号、是否共享关键假设/误差；不能仅按数据源名称或方法名称判断。
- 成立条件核查和数值复核可以增强对相应计算/条件的信任，但其通过不能独自把机制命题提升为 established。

本轮不重新定义“targets>=2 是否应长期保留”；先防止它与任务完成相绑定。如果后续需要调整，应独立评估，不能混入委派方式实验。

## 4. WorkOrder：问题式委派

以下是业务结构示意；正式输入以 `OceanTodoInput` 为准，内部结构以 `WorkOrder` 为准。外部 `question` 映射到内部 `task_goal`，`mode / question_ref / session_round` 由后台绑定，不要求 Coordinator 重复填写。保留现有身份、权限、数据引用、预算等传输字段；业务字段只有一个权威来源。

```yaml
work_order_id: backend-generated
task_id: backend-derived
question_ref: backend-bound-stable-question-id
expert_key: stable-instance-key
session_round: backend-derived
mode: new | continue

question: One bounded scientific question
target_node: existing-node-id | null
alternative_nodes: []       # 已存在并获授权的节点；可以为空

answer_standard:
  sufficient_level: association
  sufficient_if: >-
    State what the evidence must resolve, including the limitations that
    would change this answer. Do not prescribe a mandatory analysis sequence.
  max_level: mechanism_consistent
  required_discrimination: []  # 必要的备择区分；不是所有能想到的解释
  not_allowed: []              # 本轮禁止的实质声明，不是关键词过滤器

required_outputs:
  - id: required-1
    requirement: A user-requested calculation, answer or file
    origin: user_request | necessary_to_answer
    reason: Why this is essential, if not explicitly requested by the user

suggested_path: []             # 可选、可替换，不是验收条件
context:
  data_refs: []
  prior_evidence_refs: []
  known_conflicts: []
  known_unverifiable: []
hints: []                      # 可选疑点，不是隐藏工作清单
budget: existing-WorkBudget

continuation: null             # continue 时引用 gap/lead/test/required-output ID
```

### 4.1 委派要求

- Coordinator 先决定问题和所需证据，不需要在未看数据时写出完整分析步骤。
- sufficient_if 应描述“要分清什么”“什么证据会改变回答”，不能只写“充分回答”。
- 不固定要求每道题两个独立方向、一个不确定度、全部敏感性检验。数量和要求须由具体问题说明理由。
- max_level 是授权天花板，sufficient_level 不是必须证明成立的目标，更不能驱使 Expert 追逐肯定结论。
- required_outputs 默认忠实对应用户请求；必要的内部诊断可以说明理由，但不能把所有建议升级成用户要求。
- Coordinator 可以建议具体方法；Expert 可选择替代并说明理由。验收依据是证据是否解决问题，不是是否照做。
- 对已定位的代码错误或用户指定方法，允许明确要求相应修正/方法，并标明其必要性。
- 研究中如需改问题、授权节点或证据标准，保存新版本及理由；不能看到结果后悄悄降低标准让本轮过关。
- Coordinator 确保 sufficient_level、max_level 与所需备择区分不自相矛盾；后台只做已有的引用/权限检查，不再添加科学标准验收器。

## 5. ExpertReport：证据、缺口和新线索

扩展现有 ExpertResult 的科学报告部分，不另建一套输出文件协议。保留 `status` 作为执行生命周期；它不表示科学结论成立。

以下是信息组织方式，不是每份报告都要填满的表单。简单任务只需答案、必要证据和输出；tests、局限、leads等按实际内容提供，不因缺少某类科学段落而阻塞交接。

```yaml
report:
  answer:
    statement: Direct answer, with uncertainty made explicit
    direction: supports | opposes | mixed | undetermined
    level: description | association | mechanism_consistent | causal_attribution
    evidence_refs: []
    open_gaps: []

  tests: []                   # 引用持久化 Test，不重复嵌入完整日志和数组
  limitations:
    - id: limitation-1
      statement: What could affect the answer
      would_change: yes | no | uncertain
      disposition: checked | unverifiable | no_effect
      evidence_refs: []       # checked 的检验结果或 no_effect 的量级依据
      missing_and_implication: null
      rationale: Short reasoning tied to this claim

  leads:                      # 0–3 条，允许空；同一个线索续轮保留 ID
    - id: lead-1
      observation: What was actually observed
      evidence_refs: []
      proposed_question: What investigating it would ask
      would_become: Candidate new hypothesis, not an accepted conclusion
      test_available: true | false | unknown
      rough_cost: Estimated incremental effort

  path_deviations: []
  open_conflicts: []
  required_outputs_status:
    - id: required-1
      disposition: complete | partial | blocked | not_attempted
      existing_output_refs: []
      reason: null

outputs: existing-ExpertOutput-list
evidence_refs: existing-EvidenceRef-list
status: existing-execution-status
usage: backend-measured
```

### 5.1 避免新清单

- `leads: []`、`limitations: []`、`path_deviations: []` 都合法。不得为满足指标而编造局限或发现。
- disposition 中的 checked 只表示做过检查；检查结果是否足够需看证据，不自动表示“已排除”。
- 尚可检验但未处理的疑点放在 open_gaps/open_conflicts；不得为了填三分类将它虚报为 unverifiable。
- would_change 为 yes/uncertain 不自动退回：Coordinator 可以继续、收窄主张后接受，或明确无法回答。
- Expert 可以提出有边界的科学解释，不是只负责算数；新的未授权机制不能作为已确认答案，可作为 lead 或局限讨论。
- 不要求 Expert 返回树状态。逐步移除 expert_decision/confidence 作为运行判定的输入；已有输出引用和执行恢复不依赖其科学自评。
- 详细数组、代码、完整文献笔记放原有证据位置，报告只保留关键量、理由与引用。过长内容按已有上下文机制引用完整记录，不新增“报告不够合格就重写”的后台循环。

### 5.2 科学报告必须真正进入交接

本方案明确修改科学报告契约，但不修改文件产物契约：

- 新协议以 report 为权威科学内容；`coordinator_payload()` 显式携带该内容和原 outputs，不再只传旧 text 而丢掉新字段。
- 如界面仍需 Markdown，由同一 report 投影生成；不要让 Expert 另写一份可能矛盾的结论，也不要把 report 和等价长正文重复送给 Coordinator。
- 执行失败、恢复来源、部分结果的技术提示仍由后台保留，不能因报告存在就冒充成功完成。
- 同步 `OceanTodoInput -> WorkOrder -> _participant_spec() -> inputs.json`，以及 `ExpertResult -> coordinator_payload -> 持久化/恢复/续轮 capsule`。逐跳断言关键标准、未决局限、证据引用和线索没有被丢掉。
- 当前“普通最终文字自动标 expert_decision=ACCEPTED”只能表示执行答复完成，不参与科学判断；新科学契约不能继续借用它作支持或确立的依据。

## 6. Expert 自主 Test 与权限

### 6.1 自主分析不需要 Coordinator 批准每一步

在原问题、已授权节点、数据权限和预算内，Expert 可以增加滞后、季节分层、空间对照、替代估计、关键局限核查。无需为每个方法开一个新 Expert 或新 Hypothesis。

Test 建议结构：

```yaml
test_id: backend-generated
work_order_id: backend-derived
question_ref: backend-bound-question-id
targets: authorized-node-ids      # Tree任务引用授权节点；无Tree时为空，以question_ref限定范围
purpose: exploratory | discrimination | condition_check | numerical_audit
method: Brief plan
discriminates: Expected observations and what they would mean
condition_checked: null
evidence_refs: []
result: null
registered_at: backend-derived
executed_at: backend-derived
```

### 6.2 后台保存事实，Coordinator 更新科学状态

- Expert 仅有受限的登记计划、追加自身 Test 结果权限；不能新增/修改 Hypothesis，不能写效果映射或假设状态。
- 不直接把当前全权限 ocean_exploration 暴露给 Expert。复用其存储和观察日志，通过角色受限入口操作；不做第二棵树。
- 不能原样调用 `_add_tests` 或 `plan_test`：前者会把 unverifiable 节点重新打开，后者触发 `_synchronize`。受限登记必须没有这些假设状态副作用；是否重新打开节点由 Coordinator 决定。
- 正式判别检验应在运行前说明预期区分，留在正常计划/代码/对话记录中即可；不要求每次先调用登记API才允许运行。Test摘要可批量随报告提交，后台不伪造其事前登记时间。
- 执行中偶然发现无需假装事前预测。标为 exploratory，再决定是否值得进一步检验。
- 小型读取、绘图格式调整、重试不强制各建一个科学 Test；一个 Test 可以包含多个工具执行。
- Expert 追加 Test 时，检查 targets 在当前授权集合内、引用在任务范围内、消耗计入现有预算；后台不评价方法是否聪明。
- 复用已有 WorkOrder、执行和observation的关联记录，不新增一套逐Test证据资格检查。共享证据是否相关、是否独立，由Coordinator阅读判断；跨任务权限仍按原机制保护。
- purpose 是 Expert 对检验用途的声明，不是后台已经确认其科学作用；Coordinator 仍需判断它是否真正区分备择。
- 报告里的 tests 引用事实记录；Coordinator判断后决定是否更新状态。拆开当前record自动应用效果的副作用，但不新增“先提交review_id才可写状态”的门槛。
- 复用原有幂等与并发保护；不为科学审查另建revision锁或重复累计证据的分数体系。

### 6.3 预算不能锁死自主路径

保留 token、单轮资源、总任务时限、取消和无响应保护；不再添加累计API调用时间限制。

现有“每次 Test 消耗一次研究执行计数”不能因细分 Test 记录而人为耗尽：实施时明确同一次执行支持多个 Test 的关联方式，实际执行成本不重复收费；新的真实执行仍受已有预算约束。此处是记账语义调整，不是无限额放行。

## 7. Coordinator 的证据驱动循环

### 7.1 后台只保障基础接口

沿用身份、基本字段类型、角色/节点权限、任务和文件归属、预算及幂等处理。不是每收到一份报告就运行一个新的研究验收器。

真正的接口或权限错误返回精确信息，已有文件照常保留；不要把预算停止称为API失联。

以下全部交给模型判断：局限是否关键、证据是否独立、机制是否区分充分、某lead是否值得追、回答是否达标、正文因果措辞是否合理。后台不因limitations、leads或审查段落的形状自动退回任务。

### 7.2 每次结果到来后的核心思考

Coordinator 在正常推理回合中考虑，不强制另开一次LLM复盘调用：

1. 新结果增加或改变了什么认识？
2. 是否出现值得解释的现象、新线索，或与其他结果的冲突？
3. 当前主张真正排除了什么、仍未排除什么？哪些局限会改变它？
4. 下一步能新增哪一种认识，其成本是否值得？
5. 原问题是否已经回答；如果没有，是继续能解决，还是应收窄/承认无法回答？

这五问是思考范围，不是每轮五段必填表格。不适用项省略；未变化判断引用之前的记录。

### 7.3 在现有过程记录中保留判断

Coordinator在正常回应、续做消息、状态更新理由或最终回答中，简短说明新增判断和依据。后台自动保留现有事件，不要求额外交一张审查表，也不因缺少review字段拒绝继续或结束。

例如：“区域关系在去季节后明显减弱，原结论主要反映共同季节变化。现有证据已能回答关联问题；混合机制仍值得研究，但缺少相关数据，本轮不继续。”

重要判断应能定位到报告/证据，但不规定每次独立性、备择、局限各占一个必填字段。离线评估检查实际理由是否成立，不检查模板是否填满。

### 7.4 先判断，后写状态，再决定下一步

| 决定 | 适用情况 | 实际行为 |
| --- | --- | --- |
| continue | 原问题内存在值得补的证据、关键纠错或有价值的路径扩展 | 同一实例、同会话新一轮，指定问题/缺口，不强制方法 |
| delegate | 需要另一专业能力或独立复核 | 复用现有角色池，必要时不同 expert_key；不自动添加固定Agent |
| pursue_lead | 线索值得追、数据可用、符合用户目标与预算 | 若是新Hypothesis，由Coordinator先建立/授权节点；若只是原问题新Test，不建新Hypothesis |
| answer | 已有充分、恰当层次的回答 | 综合证据与局限，进入既有交付；可选线索留作未来方向 |
| unable_to_answer | 关键可答性障碍不能用合理的后续工作解决 | 明确到不了哪一层、缺什么；不制造空分支 |
| pause | 预算、用户取消、运行异常或等待授权 | 保留部分认识与可恢复状态；不升级科学结论 |

每次都考虑值得分析什么，不等于每次都追加。已达到所需标准后继续，必须说明相对于当前回答的新增价值和预算；不能只因尚未达到max_level而继续。

### 7.5 三种不能混淆的后续

- **补证据**：为了原问题能回答，属于必要继续。
- **探索线索**：为了产生新认识，可能有价值但并非当前回答的阻塞项。
- **修交付**：文件/引用/渲染问题，沿现有修复与发布路径处理，不自动重开科学检验。

## 8. 同会话续做与并发

复用当前 expert_key -> job_key -> checkpoint/session_round 链路。`continue` 是现有新一轮委派的明确语义，不新建平行会话系统。

Coordinator 可提交简短增量：

```yaml
expert_key: existing-instance
mode: continue
continuation:
  source_report_ref: existing-report
  approved_lead_id: lead-1
  gap_refs: []
  question_delta: Resolve whether the observed pattern also holds beyond the seasonal cycle
target_node: newly-authorized-or-existing-node
budget: bounded-incremental-budget
```

后台负责把原问题、授权、标准和增量组成完整WorkOrder，不能真的只给Expert一句“追lead #2”而丢失约束。

实际调用仍使用 `ocean_assign` 的 `plan_goal / todos / dispatch` 包装；增量放在对应 todo 的 `continuation` 中，外层不新增独立 `continue` 工具，也不接收 `mode`。`source_report_ref` 使用上一轮返回的 `work_order_id`，不是全文读取路径。

- 保留当前代码位置、已完成输出、关键发现、未完成事项和相关审查；完整历史保留可查，不全量重发。
- 同实例正在运行时，后续排队/串行化；不得并发修改同一会话状态。
- 同type多个专家继续通过不同expert_key独立存在；不按profile把所有实例合并。
- retry沿用同一WorkOrder和累计用量；Coordinator明确的follow-up是新轮，但仍计入团队/任务总预算。
- 不承诺上下文逐token完全不变；承诺逻辑实例稳定、关键证据不丢、原始记录可恢复。
- 当前 capsule 存在最近三轮和字符截断约束；改为保留未决关键事项、已完成检查和证据定位的结构化紧凑投影，不从序列化对象中部截断。受限长度内引用完整记录，避免关键局限恰好被截掉。
- 独立复核仍复用已有 review 专家实例与 Coordinator API/model 配置，不因本次协议修改重新切换模型或增加固定复核次数。

## 9. Tree 与最终回答

### 9.1 引入问题级解决记录，复用已有完成对象

用CoordinatorResult及任务/Tree摘要扩展承载 question-level resolution：所需证据标准、接受的报告、实际可支持层次、关键局限、决定与理由。不新建另一套生命周期状态机。

复用现有结果决定和相关证据引用，不新增独立的审查回执。新结果到来后是否改变判断，由Coordinator考虑；不由后台按revision变化自动要求再审一轮。

修改 `_recommend`、`_all_terminal`/完成路径对“全根终态”的硬依赖：

- 后台保存Coordinator给出的结果决定，沿用已有引用和执行生命周期处理，不检查理由长度或必填审查项。
- 是否回答充分由Coordinator判断，后台不通过节点数量、状态排序或检验数量推导。
- 不能在仍有重要任务运行时假称已经全部完成；需要等待、明确取消，或声明部分收尾并保留执行状态。
- 支持的命题不因问题answered而自动变为established，未检验的未来方向不阻塞主问题结束。
- 现有OR/AND父子汇总保留为结构摘要；科学父状态是否提升交给Coordinator判断，不再增加“父状态必须绑定独立审查记录”的后台门槛。
- `unverifiable` 可由Coordinator以关键缺失条件和证据理由直接记录，不需要先分解两层。旧计数兜底只保留作异常恢复提示，不作为科学判断来源。

### 9.2 终稿与证据的关系

提示词要求重要研究主张能追溯到问题、相关证据和关键局限。描述性数字直接引用报告和结果文件，不强制节点化；不做逐句引用匹配的后台验收器。

树约束结论，不能替代证据：若节点状态与未解决的关键局限冲突，Coordinator先修订判断/状态，再写终稿，而不是只把措辞偷偷降级、留下自相矛盾的树。

最终报告明确区分：观察到了什么、与哪些机制一致、真正区分了什么、仍不能判定什么、哪些线索值得后续研究。不是每个答案都必须有五个章节。

### 9.3 普通结束与恢复不重新裁决科学结果

普通最终文字、`record_coordinator_result`与`recover_incomplete`需要保留Coordinator给出的结果语义，而不是把“消息交付成功”自动解释为“科学假设成立”。这是修正状态映射，不是新增统一提交关卡。

- 判断在现有Coordinator回合和结果对象中保存；不增加专门的“提交/再综合”模型调用，不要求review_id或研究revision匹配才允许最终输出。
- 最终消息成功交付可以完成请求，同时明确科学结果是answered、insufficient_evidence或部分结论。未明确标注的科学结果保持未标注，不额外调用模型补标签，也不阻止文字交付。
- 普通聊天、简单无Tree任务不新增树或提交动作；预算耗尽和取消仍走既有部分保存/中断路径。
- 发布和文件恢复仍调用原来的实现；本节只改变科学完成依据的传递，不重写图表交付。

## 10. 典型行为样例

### 简单任务：画一年表层温度图并给一个结论

所需图和结论保持不变；Expert自主处理坐标、缺测与合理平均，必要时检查单位。没有新线索时直接返回。Coordinator接受有证据的描述，不强制Tree、备择、独立复核或额外稳健性研究。

### Q20：叶绿素变率与风速关系

Coordinator委派问题和关联证据标准，建议初步区域关联但允许替换。Expert发现季节混杂后自主去季节、分季节或做滞后/空间诊断；这些是同一问题的不同Test，不必申请新Hypothesis。若出现某个新物理机制，才作为lead返回。结果已足够回答关联问题时可结束。

### Q21：沿岸与外海叶绿素变率差异

Expert发现采样覆盖、有效像元、季节分布等可能改变区域对比时，将其作为关键局限处理或明确未决。Coordinator判断不一致来源，而不是只确认两条均值曲线已经生成。不要求把所有可能的卫星反演问题全部研究一遍。

### Q22：夏季叶绿素与季风关系

同期关联不能直接变成Ekman抽吸主导。原问题内可自主分析时间/空间关系；新增Ekman机制结论须经授权，且核查现有风产品是否支持所需量。即使获授权，证据不能区分替代解释时也只能保持合适的机制一致性表述。拒绝把level字段合规等同于正文合规。

### Campeche回归：数值复核不等于机制确立

MLD和N²重算一致，能够支持分层诊断正确；并不独立测量混合/夹卷热通量。Coordinator必须指出“分层与暖异常同现”和“分层主导维持”之间的证据缺口。不得只复用这条审查，添加多个targets后就升级机制。

## 11. 实施阶段与代码落点

| 阶段 | 工作 | 主要代码/文档 | 完成标准 |
| --- | --- | --- | --- |
| P0：锁定语义与协议 | 固化本文决策、输入输出示例、状态/完成区分；冻结实验query与基线 | 本文、`docs/adr/0021-optional-idea-exploration.md`、协议测试fixture | 不再存在“supported足够回答却必须凑established”的矛盾 |
| P1：问题式委派与返回 | WorkOrder拆分、ExpertResult科学report、权限与引用检查；不重写outputs | `team/models.py`、`tools.py`、`team/orchestrator.py`、`expert_execution.py`、`team/profiles.py`、`runtime.py`、`backend/router.py` | 专家知道建议可替换；字段端到端到达；简单题、部分结果和原输出接口工作正常 |
| P2：研究判断与完成语义 | 提示词要求每轮考虑值得分析什么；修正现有完成映射，取消全根终态依赖 | `runtime.py`、`exploration.py`、`backend/store.py`、`backend/router.py` | 正反答案均可达标；未决机制不被自动升级；无新增审查门槛 |
| P3：Expert自主Test记录 | 受限结果记录、可批量关联Test、去掉自动状态副作用；沿用原记账 | `tools.py`、`exploration.py`、`backend/store.py`、`team/orchestrator.py` | 可增路径、不可越权改假设；不要求每次登记后才可执行 |
| P4：线索批准与增量续做 | 稳定lead/gap ID、批准/暂存/不追、复用同会话 | `team/orchestrator.py`、`backend/router.py`、`expert_context.py`、`deep_runtime.py` | 同实例续做不丢关键上下文，不重算已完成工作；同type多实例不串线 |
| P5：真实评估与收敛 | WorkOrder单因素比较，再评完整研究循环；只修被证据暴露的问题 | `benchmarking/server/`、`benchmarking/tests/`、协议/前端集成测试 | 有可审计结果，而非只看到字段合规或一次好回答 |

P1–P4是一个最终协议的分阶段落地，不保留两个长期生产协作模式。阶段尚未闭环时，不宣称已完成自主研究重设计；尤其不能只启用开放路径却遗漏节点授权和状态权限。

### 11.1 提示词与Skill同步

需要同步检查/修改：

- `OCEAN_RESEARCH_PARTNER_SYSTEM_PROMPT`、`OCEAN_EXPLORATION_POLICY`：研究负责人角色、证据判断、每轮发现新问题、无需五段复盘、充分回答即可结束。
- `OCEAN_EXPERT_WORKSTREAM_POLICY` 与 `team/profiles.py`：方法自由、结论层次与节点权限、部分结果/线索返回；允许科学解释但不自写全局状态。
- `team/orchestrator.py` 的参与者提示：当前只允许为执行错误、已分配复核或必要纠错追加代码调用，需要明确增加“授权问题内有价值的判别分析”，否则 profiles 放开路径后仍被这里限制。可选美化和无目的重复计算不因此成为必做项。
- `src/oceanx/resources/skills/core/research-trajectory-planning/SKILL.md`：证据驱动继续、关键局限与未来线索分开、停止不是清单完成。
- 分析设计、物理一致性复核、claim-grounded-writing 等相关Skill：方法建议不应被误当必做流程，数值复核与归因区分。

只修改直接相关的Skill；保留现有name、description、metadata、roles等head，不把本例特定数值或答案写进通用Skill。技能选择权仍归Agent自身。

### 11.2 协议切换

- 实施前检索done_when、expert_decision、effects、全根终态等调用点，统一改动，避免新字段只是旧清单外再包一层。
- 新生产路径只有一套权威业务字段；旧字段不作为隐形兜底继续控制专家。
- 历史记录保留可读，不改科学结论；新协议在新任务验证，旧活跃会话不静默换prompt/协议。
- 如需版本切换，采用明确schema/runtime版本和可回退提交，不为旧实验长期维持另一套活跃调度逻辑。
- 权限与完成契约变化时更新 `OCEAN_RUNTIME_PROFILE_VERSION`；切换不自动改写已在运行的任务。

## 12. 验证计划

### 12.1 第一层：无付费的单元与集成测试

新增针对本次协作语义的测试，并复用现有：`test_current_team_contract.py`、`test_exploration.py`、`test_expert_resume_context.py`、`test_expert_context.py`、`test_coordinator_retry.py`、`test_deep_agent_runtime.py`、`test_expert_file_handoff.py`。

必须覆盖：

1. required与suggested分开，换方法不因未执行建议而被拒收。
2. 空leads/偏离/局限合法；partial输出不自动导致无限重派。
3. Expert只能引用授权节点；不能伪造新节点、写effects/状态、越任务读写证据。
4. 计划/执行的原始先后记录保留，不伪造预注册；没有独立登记API调用仍可执行分析。
5. 重复事件、并发到达、stale revision不重复应用结果或用量。
6. Expert记录结果不自动改变假设状态；Coordinator更新状态不要求新增review_id。
7. supported但回答充分可结束；充分反对也可answered；optional lead不阻塞。
8. 浅层关键不可检验可走unable_to_answer，不需要空分解；预算停止不改科学状态。
9. 同会话继续保留身份、证据、代码与增量约束；同type不同实例互不混淆。
10. 预算耗尽为可识别非API错误，不反复请求“补回答”；手动取消和工具无响应恢复仍生效。
11. 原文件、图表、notebook路径与引用不变；桌面普通输入、无Tree、benchmark分别过原交付回归。
12. 新科学字段穿过提示、执行清单、Coordinator交接与长capsule后仍完整；技术恢复提示不会被当成科学结论。
13. Expert记录计划/结果不能重开unverifiable节点或触发父状态传播；已有跨任务权限测试保留。
14. 普通最终答复、回执和恢复保留Coordinator结果语义；没有独立审查记录不阻塞交付，无Tree简单问题不被迫使用树协议。

测试只证明协议行为，不能证明科学判断正确。

### 12.2 第二层：委派方式的单因素实验

主实验Q20/Q21/Q22，每题A/B各3次，共18次。A为清单式委派，B为问题/证据标准式委派。两组使用相同ExpertReport契约、模型、技能、数据、工具、预算、角色与会话策略；唯一处理变量是委派语义。

第一轮限定为单轮委派，不启用lead自动续做、不追加新continue机制，避免把“返回格式、Agent数量、迭代次数、Tree改动”同时当作处理变量。两组公共返回契约不是旧系统原样快照；需在报告中准确命名这一控制实验。

特别注意仓库 `benchmarking/GENERAL_QUERIES.md` 对原batch与general query的区分：比较委派策略必须固定**同一实际query JSONL**。不能A用历史具体问法、B用新general问法；若要测general query，另建两组同文对照。冻结文本、数据hash/窗口、模型标识、prompt/Skill源码、预算和runner版本。

同时冻结runner追加的Research Tree指令，保存真实 `submitted_prompt.txt`，不只比较task_info里的query。使用隔离任务工作区，固定并发，保留所有attempt；首轮结果与重试分别报告。先做Q21一对冒烟运行确认链路，再决定是否开展18次pilot。

事前定义评判：

| 观察项 | 判定方式 | 不允许的替代指标 |
| --- | --- | --- |
| Q20路径自主 | 是否出现针对同一问题、合理且实际执行的非强制检验；如滞后、季节、空间之一 | 不按更多图、更多代码、更多Test直接加分；建议中本来就有的不算新增 |
| Q21关键局限 | 是否识别并合理处理/限定会改变区域对比的覆盖或方法差异 | 不按limitations条数或字数评分 |
| Q22结论强度 | 正文和证据是否一致，有无把相关直接变成Ekman主导 | 不只检查level字段，必须看实际分析与措辞 |
| 新线索质量 | leads有观察依据、目标清楚、可检验性与成本合理 | 非空率不是合格门槛；允许没有值得追的线索 |
| Coordinator判断 | 是否具体说明尚未区分什么、为什么继续或停止 | 不以命名一个备择或五段齐全代替质量 |
| 收敛 | 回答充分后是否仍无理由追到max_level | 不以越早停止越好，必须确认回答确实充分 |

Q20/Q21/Q22的科学证据合理性继续参考既有题目rubric；评估者参考资料不得泄漏给被测Agent。当前checklist仍有draft、数据fingerprint未锁定等状态，正式评分前先核对实际数据、校准检查项与参考证据，不把草案视为已经验证的ground truth。

三次重复只能做pilot，报告逐次表现、范围和失败案例，不作显著优越性结论。如果B扩展了有效路径但越级也上升，应检查委派、Expert解释与Coordinator审查共同链路，不预先把责任全归profiles。

### 12.3 第三层：完整循环与反例

P2–P4集成后再评，不与上一组单因素结论混算：

- **上限放宽、证据不变**：Coordinator不能因为max_level更高就无理由升级。
- **新增真正有区分力的证据**：应愿意更新判断，不能永远停在谨慎措辞。
- **简单画图题**：A/B各3次，检查必要输出、无谓委派/额外诊断、耗时与token，不引入必填线索压力。
- **部分结果＋关键未完成项**：在同会话续做，不清空已有结果。
- **有趣但无关或不可检验的lead**：保留未来方向或不追，不能自动扩目标。
- **Campeche固定证据回放**：用原复核和报告作为只读输入，检查“数值一致”是否仍被上推为“维持机制确立”。复用证据，不先重跑重型数据分析。
- **缺数据/预算退出/取消**：科学结论不被强行推终态，部分工作能按原链路保留。

评审优先盲化A/B标签，采用具体证据定位；LLM辅助评审与人工抽查并用。关键科学争议不由一个无依据的LLM总分自动裁决。

### 12.4 运行指标与成本

从现有轨迹收集回答、Coordinator判断、计划/执行顺序、lead处置、会话身份、实际工具执行、耗时、API错误、预算退出、token及缓存口径。不要求Agent为测评额外填过程表。真实调用前确认总实验预算，不在写计划时自动启动18次或更多运行。

离线跨运行统计path_deviations、lead处置、同节点续做次数等只提示人工检查，永远不放进Agent提示词作为考核目标。批量实验不修改桌面默认模型/预算。

## 13. 主要风险与应对

| 风险 | 应对 |
| --- | --- |
| 新字段成为新清单 | 可选建议、空数组合法、只记录增量判断，验收看证据而不是篇幅 |
| Coordinator盖章 | 证据不变/max放宽反例；审查需指出真正区分了什么，评正文而非字段 |
| Coordinator过度保守 | 提供可区分证据的正向案例，允许反对性回答完成 |
| Expert扩目标 | 授权节点和工具权限硬检查；新机制作为lead，目标扩展需批准 |
| Test登记增加token和调用 | 随报告批量记录、一个Test关联多次执行；无逐步预登记关卡 |
| 会话延续重复历史、重复计算 | 复用现有上下文压缩，增量WorkOrder引用已完成结果；全历史按需读取 |
| 旧树终态继续驱动过度确立 | 分开question resolution与hypothesis state；父节点汇总不自动升级科学解释 |
| 比较混入query/预算/model改动 | 固定manifest与源码，分开委派单因素和完整系统比较 |
| 业务协议变更影响交付 | 输出类型、路径和发布实现不改，保留端到端交付回归 |

## 14. 交付清单与完成定义

实施后应交付：

- 一套统一的WorkOrder/ExpertReport业务契约及正反样例。
- 受限Expert Test记录与Coordinator独占科学状态修改的权限测试。
- 正常对话/事件中的研究判断与既有结果决定，能回溯重要继续/停止/升级理由，不新增审查回执系统。
- 复用原会话的lead/gap续做及预算、取消、恢复验证。
- 同步后的Coordinator/Expert提示词与相关Skill，head保持原约定。
- 无付费协议测试、简单任务回归、同文Q20/Q21/Q22 pilot及反例评估报告。
- 未改动的交付链路和可使用的历史记录；真实实验的query、数据、源码、预算、用量可复核。

验收不是“新增字段都填满”，而是以下三个结果同时出现：

1. Expert能在同一问题内作有价值的方法调整，而不是仅完成Coordinator清单。
2. Coordinator每次阅读结果都考虑下一步的研究价值，并能说明为何追、为何不追、为何现在足够。
3. 目标、结论强度和预算没有随探索失控；简单任务仍直接完成，科学不确定性不被状态机制掩盖。

**最终目标：把事前规划从“规定怎么做”退回“明确要回答什么”，把后续推进交给证据，而不是清单或必须达成的树终态。**

## 15. 本次实现记录（2026-09-11）

### 15.1 已接入的生产路径

- **委派**：`OceanTodoInput.question` 进入唯一内部问题字段 `WorkOrder.task_goal`；新增 `answer_standard`、`required_outputs`、`suggested_path`、授权节点与增量续做信息。同一 `scientific_assignment()` 投影进入 Expert 提示与 `inputs.json`，不再维护两份字段清单。新调用不接受 `done_when`；旧日志只在读取时转换，不改历史数据。
- **报告**：`ExpertResult.report` 携带答案、可选的检验引用、局限、线索、偏离和部分交付说明；普通 Markdown 仍可直接返回，不增加格式重写或总结模型调用。Coordinator 收到一份科学内容及原 `outputs`，后台执行引用单独保留，不伪装成专家逐项声明的结论支持。技术中断说明与科学结论分开。
- **研究判断**：Coordinator 提示与相关四个 Skill 已同步“每次结果后考虑还有什么值得分析”，允许充分的正面或负面回答结束；不要求每轮五段审查或继续。Skill 的 `name / description / metadata / roles` 等 head 保留。
- **Test 与状态**：Expert 新增受限的 `ocean_expert_tests`，只读授权节点、登记/记录自身 Test；可事后记录，没有逐步预注册门槛。`record` 只保存事实，`adjudicate` 由 Coordinator 显式改变假设状态。保留直接 `established` 的既有多目标必要条件，不新增证据等级比较、审查回执或科学评分器。
- **完成语义**：不再根据父子关系自动推导科学状态，也不要求全部根分支终结。Tree 的显式 `answered / unable_to_answer` 保留在当前请求的 `CoordinatorResult.research_outcome` 中；未标注则保持未标注，旧轮决定不串到新轮。原请求完成、取消及文件恢复路径独立保留。
- **同会话续做**：复用 `expert_key / job_key / session_round`。显式 `continuation.source_report_ref` 指向同一实例已返回的 `work_order_id`，继承该轮问题、标准、授权、约束和数据来源；明确替换的字段以新轮为准。明确 follow-up 不再被后台恢复分支吞成旧 WorkOrder 重试；隐式失败重试仍沿用旧身份和累计用量。
- **上下文**：紧凑 capsule 按完整字段打包，给出省略计数和全文引用，避免截断 ID、路径或 JSON。通过原 `ocean_read_file` 按页读取 `expert-report:<work_order_id>`，限制在同一任务/Expert 实例；不重复塞入全部历史。
- **记账**：可选的 `research_test_ids` 派发在实际 WorkOrder 身份绑定后记账，一个 Expert 轮次对应多个 Test 不重复收费，多 Expert 分别计数，重放不重扣。未预声明 Test 的分析继续受原 WorkBudget/团队 token 预算管理，不因此新增所有读取都计数的关卡。

### 15.2 保持不变的部分

- 不重写 `ExpertOutput`、结果保存、图表发布、notebook 和下载路径；前端没有因本次协议改变而增加新的操作步骤。
- 不修改默认模型、实验数据、历史测试结果或既有 benchmark runner；工作区里原先已有的 benchmarking 改动保留。
- 不增加固定 Agent、固定复核轮次、单独复盘调用或后台“值得继续”判断器。
- 运行时标识更新为 `oceanx_runtime/v31-evidence-driven-research`。新行为应在更新后端后用新任务验证；本次未启动或重跑用户的历史任务。

### 15.3 验证边界

离线测试覆盖委派字段传递、普通/结构化报告、部分结果、授权和状态写入、完成语义、同实例与多实例、恢复/重放、预算及原交付链路。最终冻结源码后的结果：

| 检查 | 结果 |
| --- | --- |
| `MPLBACKEND=Agg .venv/bin/python -m pytest tests/test_oceanx -q` | **457 passed**；4 条现有科学依赖警告，无失败 |
| `frontend/ocean-desktop` 下 `npm test -- --reporter=dot` | **184 passed / 36 test files** |
| `frontend/ocean-desktop` 下 `npm run check` | TypeScript 检查通过 |
| `git diff --check` 与本次生产文件的 Ruff 关键错误检查 | 通过 |

后端执行集成测试需要允许本机子进程检查和 localhost 测试端口；使用无界面绘图后端，未修改 OceanX 沙箱策略来适配测试。前端验证为单元测试及类型检查，不冒充真实桌面端到端操作测试。

**P5 尚未完成**：没有启动 Q20/Q21/Q22 付费调用，也没有声称科学正确率、探索质量或耗时已经改善。后续按第 12 节先冻结同文 query、数据、模型与预算，再做冒烟及对照实验。
