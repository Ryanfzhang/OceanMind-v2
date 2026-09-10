# OceanX：自动研究循环与可审查的经验学习

准备日期：2026-09-10。面向有科研背景、对 Agent 架构了解不一的组会听众。建议 28 分钟讲述、2 分钟讨论，15 张主讲页。当前交付为内容规划及第一轮资料索引，不含 PPTX。

## 汇报主张

OceanX 的设计围绕两个问题展开：如何让 Agent 持续补齐当前研究问题的证据，以及如何把有依据的经验留给下一次研究。

把任务内迭代和跨任务学习分开讲。前者改变当前的研究行动，后者改变未来可加载的 Skill。二者都需要明确的权限、来源和停止边界。

本轮代码依据为本地工作区，HEAD 为 558981b，可能包含未提交改动。ADR 说明设计意图，源码提供实现证据，测试与真实科研效果需要另外区分。下列设计理由是结合 ADR 的讲述组织，不代表每项取舍已经通过对照实验证明最优。

## 逐页安排

| 页 | 标题 | 分钟 | 核心内容与讲述证据 |
|---|---|---:|---|
| 1 | OceanX 的研究自动化设计 | 1 | 给出范围：自动研究、跨任务经验学习、架构取舍 |
| 2 | 一个海洋研究问题需要哪些证据 | 2 | 用“2023 年东海异常增暖的机制是什么”作贯穿示例。展示数据、热收支、替代机制与不确定性需求。明确这是演示问题，不展示虚构结果 |
| 3 | 自动研究与自进化的区别 | 2 | 研究循环改变下一步实验。经验学习改变未来行为。自修改代码属于另一层，模型权重更新又是另一层 |
| 4 | autoresearch 的最小实验循环 | 2 | 用 Karpathy 项目讲小规模、固定预算、明确指标、保留或丢弃修改。解释这种实验环境为什么容易构造反馈 [R1] |
| 5 | 科学发现中的实验搜索 | 2 | AI Scientist-v2 的实验管理与树搜索，AutoDiscovery 的 Bayesian surprise 与 MCTS/PW。二者的搜索对象及反馈不能混为一谈 [R2–R3] |
| 6 | Agent 可以进化什么 | 2 | EvoScientist 的研究/实验记忆与 DGM 的 Agent 代码修改并列。OceanX 本次主讲的是可版本化 Skills，不声称在线训练基础模型 [R4–R5] |
| 7 | 海洋研究的反馈条件 | 2 | 多源数据、尺度与单位、缺测、昂贵实验、机制解释。EarthLink 作领域参照 [R6]，承接 OceanX 为何需要证据管理 |
| 8 | Coordinator 与持续 Expert Session | 2 | 主 Agent 负责用户问题和最终交付，按需发 WorkOrder。Expert 在持续会话中执行，返回结果。普通问题可以直接回答 [L1] |
| 9 | 结果返回与研究停止 | 2 | Expert 一轮结束不等于研究完成。讲结果验收、缺口追问、阻塞和取消。区分运行状态合法与科学结论充分 [L1–L2] |
| 10 | 证据缺口驱动的研究树 | 2 | 矛盾优先，其次关键缺口，再到补充支持；成本参与同级排序。树是可选的。支持某分支后仍需检查后续问题与替代解释 [L3] |
| 11 | 研究树调度的设计变化 | 2 | 解释为何放弃此前 PW/UCT 与采样奖励路径：本地记录显示成本和推进问题。只把它作为工程动机，不把一次试验变成普遍算法结论 [L3–L4] |
| 12 | 经验如何成为 Skill | 2 | 显式经验笔记进入 inbox，Curator 按需读取相关证据与现有 Skill，决定忽略、延后、报告缺陷、创建或更新 [L5] |
| 13 | 学习的权限与恢复边界 | 2 | 当前任务运行中不发布 Skill 更新，版本冲突保护，历史可追溯，人工回滚。LLM 审阅仍可能接受错误经验 [L5] |
| 14 | 一个案例的完整走查 | 1 | 展示已有任务中的 WorkOrder、执行记录、结果、缺口及后续动作。没有完整记录时用明确标注的示意走查 |
| 15 | 当前证据与下一步评估 | 2 | 分开列实现机制、已有运行记录、待验证收益。提出跨任务迁移和同预算消融，结束于可检验的研究问题 |
| — | 讨论 | 2 | 谁判断证据充分？怎样证明 Skill 更新对新任务有效？ |

合计 30 分钟。若 30 分钟全部用于演讲，可把案例扩展到 3 分钟并另留讨论时间。

## OceanX 设计取舍的讲法

| 设计选择 | 所处理的问题 | 需要承认的代价或边界 |
|---|---|---|
| 一个 Coordinator 持有完成与发布权 | 子任务结果需要汇总到同一个用户问题 | 主 Agent 可能形成判断瓶颈，统一控制不能消除错误 |
| 按需委派、有界 WorkOrder | 避免所有问题都支付完整多 Agent 流程成本 | 拆解质量依赖 Coordinator，不能声称更多或更少 Agent 必然更好 |
| 持续 Expert Session 与 checkpoint | 后续问题复用上下文和工作文件，减少中断后的重复工作 | 旧上下文可能携带错误，需要读取和纠正来源 |
| 文件化交接与持久结果 | 截断日志仍可读取，已算出的结果可以复用 | 文件存在不意味着已验收，也不意味着已经向用户发布 |
| 可选、确定性的证据缺口调度 | 在明确用户目标下优先推进关键实验，减少额外评分调用 | 优先级仍由模型表达，未被记录的缺口无法由调度器发现 |
| 显式经验 inbox 与 Curator | 让经验带来源、适用条件与修订历史进入跨任务记忆 | Agent 不保存的经验不会进入审阅，Curator 不是独立科学真值判官 |
| Skill 版本化与人工回滚 | 控制误学、并发覆盖与追责困难 | 版本机制提供可恢复性，本身不能保证学习有效 |

停止条件的准确讲法：Expert 每轮正常提交有效结果，或因实质阻塞、取消而结束。Coordinator 据用户目标决定继续、交付或说明无法继续。研究树对已登记的可测试关键缺口设置完成保护，预算暂停允许保留未解决状态。协议校验不能证明物理机制正确。

## 第一轮外部资料

筛选以论文和作者官方项目为主。以下均已核对摘要或官方项目说明，尚未完成每篇全文、公式与实验表的精读。主讲无需逐篇介绍，把资料嵌入对应设计问题。

### R1. Karpathy autoresearch，2026

- 官方仓库：https://github.com/karpathy/autoresearch
- 取材：README 的最小循环和 program.md 的实验约束，讲固定时长训练与保留/丢弃修改。
- 用途：第 4 页，建立自动研究闭环的直观认识。
- 边界：这是具体项目名，广义自动科研不能等同于该项目。训练代码优化也不自动等于 Agent 自身架构的进化。

### R2. The AI Scientist-v2，2025

- 论文：https://arxiv.org/abs/2504.08066
- 全名：The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search。
- 取材：实验管理与 agentic tree search 的方法概述。后续制作时核对原文工作流图及实验设置。
- 用途：第 5 页，说明自动研究可以包括实验设计、执行和写作。
- 边界：论文报告的 workshop 结果不能泛化为任意领域的独立科研能力，也不是 OceanX 的效果证据。

### R3. AutoDiscovery，2025，后续修订至 2026

- 论文：https://arxiv.org/abs/2507.00310
- 官方代码快照：https://github.com/allenai/autodiscovery-neurips/tree/66aa2d513cafdb23bf38313ebef522ade93f8cd9
- 方法：以证据前后的模型信念变化作为探索反馈，用 MCTS 与 progressive widening 探索嵌套假设。
- 用途：第 5、10、11 页，讨论谁决定下一个实验，以及评分开销与研究目标的匹配。
- 边界：本地历史审查对照 v2，当前 arXiv 为 v3。正式比较要统一版本。OceanX 当前调度是自己的适配，不能称 AutoDiscovery 复现；模型惊讶度也不等于科学真值。

### R4. EvoScientist，2026

- 论文：https://arxiv.org/abs/2603.08127
- 官方项目：https://github.com/EvoScientist/EvoScientist
- 全名：EvoScientist: Towards Multi-Agent Evolving AI Scientists for End-to-End Scientific Discovery。
- 取材：Researcher、Engineer、Evolution Manager 的论文角色，以及 ideation memory / experimentation memory。
- 用途：第 6、12 页，解释研究经验与实验经验如何影响未来任务。
- 边界：论文三角色与当前开源产品的具体子 Agent 列表不能直接画等号。讲论文机制与讲代码版本必须分别标注。

### R5. Darwin Gödel Machine，2025，后续修订至 2026

- 论文：https://arxiv.org/abs/2505.22954
- 全名：Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents。
- 方法：修改 Agent 自身代码，通过编码基准经验评估，维护 Agent 候选档案并探索不同改进路径。
- 用途：第 6 页，对照 Skill/记忆更新与 Agent 代码更新。
- 边界：编码基准改进不等于海洋科学推理提高。OceanX 当前介绍的学习路径不是 DGM 式自主改写运行时。

### R6. EarthLink，2025

- 论文：https://arxiv.org/abs/2507.17311
- 全名：A Self-Evolving AI Agent System for Climate Science。
- 取材：气候研究中的规划、代码执行、数据分析和物理推理集成，以及 Atlantic Niño 案例。
- 用途：第 7 页，把通用 AI Scientist 问题落到地球科学。
- 边界：当前仅核对摘要层面信息，不据此推断它的自进化实现细节或声称 OceanX 优于该系统。

## OceanX 本地证据索引

- L1：[Coordinator / Expert / ResultBundle 决策](adr/0019-coordinator-expert-session-result-bundle.md)；[委派实现](../src/oceanx/team/orchestrator.py#L237)；[WorkOrder](../src/oceanx/team/models.py#L506)。
- L2：[统一 AutoResearch 与学习边界](adr/0020-unified-autoresearch-learning-and-search.md)；[文件交接](adr/0022-expert-file-handoff.md)；[继续执行与发布](adr/0023-coordinator-continuation-delivery.md)。
- L3：[当前研究树 ADR](adr/0021-optional-idea-exploration.md)；[调度函数](../src/oceanx/exploration.py#L165)；[完成保护](../src/oceanx/exploration.py#L332)。
- L4：[历史忠实度审查](reviews/2026-09-10-autodiscovery-fidelity-audit.md)。此文开头明确为改动前的历史审查，不能将表格里的旧实现当成今天的代码。
- L5：[Evidence-aware Skill Curator](curator-evidence-review.md)；[Curator 实现](../src/oceanx/skill_curator.py#L132)；[Skill 版本历史](../src/oceanx/skill_history.py)。
- 基础 Agent 运行层：[deep_runtime.py](../src/oceanx/deep_runtime.py#L378) 的图事件流与 create_deep_agent 构建。讲研究层循环时不要将它简化成唯一一个 Python while 循环。

## 案例与素材准备

优先使用同一个任务的真实记录串起汇报：用户问题、最初数据清单、一条 WorkOrder、一个成功或失败实验、读取已有结果后的追问、最终交付。若还没有经验笔记到 Skill 版本变化的完整记录，则单独标注那一部分为机制示意。

近期 MODIS/OISST/风场/ERA5 数据整理可用于说明“数据到齐”和“科学任务完成”的区别，但那段操作来自本次外部助手协助，不能作为 OceanX 自主完成研究的演示证据。此前 12/15 仅指输入覆盖，不是 12 道科研问题已经解答。

制作阶段准备：一张双循环概览、一张 Coordinator/Expert 交接图、一张小型研究树、一组真实结果与 Skill 历史截图。外部方法图先核对原文编号、来源与使用许可，再决定引用或明确标注的重绘。当前未下载或选定论文图片。

## 评估设计与答疑备忘

建议后续对照：相同模型、输入数据与预算下，比较直接 Agent、OceanX 无经验学习、加载冻结 Skills、Curator 更新后 Skills。另设有/无研究树调度的消融，避免把架构和学习效果混在一起。

使用未参与经验提炼的新任务。记录证据支持度、科学错误、无依据结论、重复执行、完成率、总时间与 token 成本。预先冻结评分标准，尽可能引入领域专家盲评。Skill 数量增长和单元测试通过都不能作为科学能力提升的替代指标。

常见追问：

- 为什么不用单 Agent？解释任务拆分与权限边界的作用，同时承认收益需同预算对照。
- 为什么不用 MCTS？说明当前目标与反馈成本，避免宣称 MCTS 普遍不适合科学研究。
- 谁判断研究结束？Coordinator 的科学判断与后端对已登记状态的约束共同作用，未表达的缺口仍可能漏掉。
- 如何避免学错？来源审阅、适用性与不确定性、版本保护和人工回滚提供防线，但仍需迁移评测。
- 是否已实现持续变强？已实现经验到 Skill 的学习通路，持续性能收益需要实验验证。

下一轮确认听众背景和展示场景后，再扩展逐页讲稿与制作 PPTX。默认中文讲述，保留核心术语的英文名称。
