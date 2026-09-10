# AutoDiscovery 与 OceanX Research Tree 忠实度审查

> 历史审查：下述“当前实现”指改动前的代码。之后的 PW/UCT 与采样奖励调整见 [ADR 0021](../adr/0021-optional-idea-exploration.md)。

日期：2026-09-10。结论：**OceanX 没有完全遵从 AutoDiscovery；它是受其启发的、Coordinator 驱动的假设跟踪和受限树搜索，不是原论文方法的复现。** 本次只审查，未修改产品实现、未重跑模型研究实验。

## 审查基准和边界

1. [AutoDiscovery 论文 v2，§3、§4、附录](https://arxiv.org/html/2507.00310v2)：主方法为 surprisal-driven MCTS + progressive widening。
2. [官方 NeurIPS 仓库固定版本 66aa2d513cafdb23bf38313ebef522ade93f8cd9](https://github.com/allenai/autodiscovery-neurips/tree/66aa2d513cafdb23bf38313ebef522ade93f8cd9)：下载到 /tmp/autodiscovery-audit，审查 run.py、mcts.py、mcts_utils.py、beliefs.py、args.py、agents.py、transitions.py。在线 main 页面可能存在缓存，以下代码结论以固定快照为准。
3. OceanX 当前工作区 src/oceanx/exploration.py、runtime.py、tools.py 和 ADR 0021（包含尚未提交的既有改动）。
4. 本次不把 Asta 产品版或 continual-beliefs 后续工作混作原论文。官方 README 也将 Asta 另列。

## 原方法到底怎样运转

论文搜索的是数据中的可验证发现，不以完成一个用户问题为唯一目标。每轮从根重新选择扩展位置，利用路径中的旧结果产生候选，执行实验，估计信念变化奖励，并将该次访问/奖励回传至祖先。PW 根据访问量放宽分支容量，UCT 在未扩展时选择后续路径。论文实验使用固定预算比较发现数量，而非一条假设得到支持即终止。

数学上的“先验/后验”指是否向信念模型提供证据。官方 run.py 在实验之后调用评分程序；beliefs.py 分别用 evidence=None 与实验信息构造两类调用。因此不能把它误说成必须在时间上先执行先验采样、再执行实验。结果评分包含独立模型采样开销。

代码执行流程为 programmer → executor → code analyst → reviewer（必要时修订）→ generator，由状态机推进。每个实验节点保存结果和待试候选，后续调度消费这些候选；生成器不是只在模型临时想起来时才运行。[run.py](https://github.com/allenai/autodiscovery-neurips/blob/66aa2d513cafdb23bf38313ebef522ade93f8cd9/src/run.py#L280)、[transitions.py](https://github.com/allenai/autodiscovery-neurips/blob/66aa2d513cafdb23bf38313ebef522ade93f8cd9/src/transitions.py#L46)

## 逐项对照

| 项目 | AutoDiscovery 基准 | OceanX 现状 | 忠实度 / 含义 |
|---|---|---|---|
| 目标 | 论文为开放发现；代码可用 user_query 限定生成 | 回答明确研究问题 | 有意适配；合理但不能直接迁移原论文收益 |
| 根/节点 | 代码 level 0 为虚根，level 1 为数据加载，后续为实验假设 | 根为用户问题；其他节点可为待验证想法或概括机制 | 非同构；深度数字不能直接比较 |
| 候选生成 | generator 根据旧实验提出独立、自足的新实验，允许间接关联，鼓励组合变量及跨数据集关系 | Coordinator propose；提示重点为当前分支的判别/证据缺口 | 文档都说 inspiration，但实际生成策略更收束 |
| 调度权 | 外层代码实际选择节点并启动实验流程 | Tree 给建议，Coordinator 决定是否/怎样执行，无强绑定 | 重大偏离；画出的树可能是事后整理而非实际控制器 |
| UCT 选路 | 每轮从根选择，无 active_branch_id 锁 | active_branch_id 可用时不比较其他主分支 | 重大偏离；主分支层面探索项不能发挥作用 |
| 扩宽 | 论文/PW实现：children 数小于 k·N^alpha 时可扩展，其他时候向子节点搜索 | max(2, ceil(sqrt(反馈节点数 + 可用子数 + 1)))；主分支存在后根不自动扩展 | 公式及根特例均不相同 |
| 奖励 | 论文基于有/无证据的信念变化；代码提供多种估计和评分 | information_gain 为 Coordinator 手工给的 0–1 数 | 核心机制未复现；不能称 Bayesian surprise |
| 回传 | 每个新实验节点完成一次访问/奖励回传 | 每个有当前反馈的节点算一次；更正替换旧值 | 有祖先聚合形式，但计数语义不同 |
| 关闭节点 | 实验已完成仍可作为后续想法来源；success 表示流程有效，不等于假设为真 | solved/exhausted/blocked 排除整个该子树 | 改变搜索空间；一个被否定但启发性强的方向可能被过早关闭 |
| 上下文 | 论文讲路径历史；固定代码实际按 k_parents 截断，并加数据加载上下文 | read 默认最多3个路径节点，另8条任务证据；Expert 不接树，靠Coordinator委派上下文 | 不等价，但“我们只给3层而原版无限上下文”也不准确 |
| 串行/并行 | 原版外层逐个实验执行 | 每波一个科学目标，多个Expert可协作 | 串行本身不是违背原版；锁分支与生成约束才是主要差别 |
| 去重 | 论文运行后语义聚类；代码保存时可去重 | propose 时精确规范化文本查重，语义由Coordinator判断 | 不同，不保证等效抑制语义重复 |
| 停止 | 固定迭代/实验预算，已完成实验不意味着全局停止 | 原问题完成说明、无可用分支、预算或用户停止 | 有意改编；最新改动仅解耦节点与全局完成 |

本地依据：[搜索和统计](../../src/oceanx/exploration.py)、[Coordinator策略](../../src/oceanx/runtime.py)、[工具接线](../../src/oceanx/tools.py)。

## 最需要正视的实现细节

### 1. UCB 公式存在，不代表原版搜索仍然存在

OceanX score() 保留均值加探索项，系数相当于固定 C=1；但 _recommend() 在 active_branch_id 可用时只调用 search(active)。其他主分支得分再高也没有机会参加本轮比较。数学形式相似不能抵消选择范围被收窄。

### 2. PW 不是同一个公式

本地分母/访问基数为有反馈的节点数，可用子数排除关闭节点，并设置至少2个分支容量。原版PW采用 visits 与全部 children 数。关闭已有子节点会让本地再次产生空位，因此其广深行为不能按原论文PW解释。根已有主分支时，本地还特判禁止自动扩根；Coordinator仍可手动propose，不是技术上最多4个主分支。

### 3. 共享证据可以被重复计作多次发现

_statistics() 不按 evidence_ids 或 work_order 去重。一个实验同时支撑父、子或兄弟节点，只要分别record，祖先就收到多个样本和多个reward。替换同一节点反馈不会重复计数是好的，但不足以保证“每次实验只算一次”。复测中同一层化工作流关闭多个节点就是需留意的场景，不应把这些节点视为独立实验。

### 4. solved=已回答 与 solved必须supported 相冲突

本地输入说明将 solved 定义为节点问题已回答，但验证器仍要求 outcome=supported。复测中否定结论用solved被拒后改exhausted，已实证此语义冲突。available() 对非open直接返回false，即便该节点下还有open子节点；仅expandable=false而status仍open时才保留后代可达。该关闭机制不是原方法。

### 5. Expert 不持有整棵树，不是问题本身

原版也将搜索控制放在外层。关键在于下一节点得到足够的路径结果，以及节点选择—实验—评分—更新的对应关系。完全可以由Coordinator拥有树而不让Expert操作树；这项架构选择不妨碍忠实实现搜索策略。

## 官方代码默认参数并不等同于论文算法

固定版本 args.py 默认：mcts_selection=ucb1_recursive（不是pw）、n_warmstart=8、k_experiments=8、allow_generate_experiments=true、k_parents=3、n_belief_samples=30、use_binary_reward=false、reward_mode=kl、kl_scale=5、evidence_weight=2。

因此应区分：
- 论文：§3 的方向性信念转换指标与 MCTS/PW；
- 官方实现能力：多个选择器和多种奖励模式；
- CLI默认运行：递归UCB选择 + 连续归一化KL奖励及warmstart。

不能说“直接运行官方默认命令就是精确复现论文”，也不能把全部官方模式都说成二值信念翻转奖励。k_experiments=8是生成批量，生成可按需补充，不是整棵树8个节点的硬上限。虚根自身只放数据加载实验；广度通常发生在数据加载节点及后续假设节点，之前笼统说“根无限生成假设”不精确。[args.py](https://github.com/allenai/autodiscovery-neurips/blob/66aa2d513cafdb23bf38313ebef522ade93f8cd9/src/args.py)、[reward函数](https://github.com/allenai/autodiscovery-neurips/blob/66aa2d513cafdb23bf38313ebef522ade93f8cd9/src/mcts_utils.py#L308)

## 无模型最小行为核对

从固定版 mcts.py 抽取原始 progressive_widening/ucb1 函数，与本地 _recommend/_statistics 对照；不改函数。给定等价局部状态，PW参数显式设 k=1, alpha=.5, C=1（不假称是CLI默认）：

1. A已试一次，奖励0.1；B未试。官方PW选择B作为下一扩展位置；OceanX active=A时返回 expand A。行为差异通过assert验证。
2. 参考PW节点访问量到9、已有2个子节点时，允许回到该位置增加新子节点；OceanX根已有主分支时此位置被特判排除。
3. 本地两个节点引用同一个observation，root统计为2次。通过assert验证。

这些是调度语义检查，不是研究效果benchmark。可重现脚本/输出见同目录的 autodiscovery_fidelity_probe.py 和 autodiscovery_fidelity_probe.json；运行需将官方固定源码解压至/tmp/autodiscovery-audit，使用PYTHONPATH=src。

## 对前述解释的纠正与反思

- 将串行一项实验当成我们独有的主要限制，归因不准确；原版也是串行外循环。
- 将官方代码奖励一概说成信念翻转的二值奖励，混淆了论文与代码配置。
- 将路径上下文一概说成全部历史，忽略代码的截断配置。
- 删除“主分支solved自动终止”只修正停止条件，不会恢复根部重新选择、标准PW、信念奖励或自动实验循环。
- 先前“一条走通才换”的需求可以解释锁定策略的来源，但它仍是主动改编，不能借原论文的效果为其背书。
- 当前ADR已明确写 adaptation / not reproduction；此说法是准确的，口头说明也应保持这个边界。

建议先决定要实现“问题导向的树辅助研究”还是“原AutoDiscovery开放发现”。前者可保留用户目标和Coordinator归属，同时恢复跨分支选择及明确的实验计数；若不实现信念采样奖励，应明确标为启发式改编。若声称严格复现，必须固定论文配置，补齐奖励、循环和评测条件，不能仅调整提示词。任何选择都不自动保证优于Claude Code；现有单次运行不能建立该因果结论。
