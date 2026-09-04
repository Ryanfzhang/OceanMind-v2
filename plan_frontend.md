# Ocean Research Partner Desktop App 计划

> **2026-08-09 统一计划说明：** Desktop shell、Task-first 布局和 Spatial Workbench 继续以本文为基线；Agent 调度和 Working 交互已由 `next_plan.md` 统一更新。前端不再提供 Standard/Team 或单/多智能体模式切换，所有请求进入 Adaptive Team Runtime；Working 临时框固定包含由真实后端事件驱动的 Agent 协作 Canvas，最终结构化 Artifact 在右侧 Workbench 使用通用 renderer 打开。本文后续与这些决定冲突的旧 multi-agent panel 或列表式 activity 描述仅保留为历史实现记录。
>
> 状态：产品与工程路线 v2.4，2026-07-16。F0 的后端合同、迁移、Protocol v2、任务级 checkpoint、TUI 最小 task 入口与离线测试骨架已实现；Desktop shell、受限 artifact broker、MapLibre workbench 与 F2--F4 的首个完整工作流切片均已实现并有离线验证。F5 的跨平台可信执行与 F6 的签名发布仍在实施中。
>
> 本版重新定义了 Desktop App 的主界面。它借鉴用户提供的 Codex App 截图所体现的 task-first 交互，也吸收 [Open Science Desktop README](https://github.com/ai4s-research/open-science#readme) 中“连续研究会话、真实本地产物、运行记录与 provenance”的优点，但不会照搬其通用科学工作台或 skill pipeline。
>
> 与其他计划的关系：`plan.md` 继续规定 AnalysisRun、Artifact Store、科研规范 skills、verification 和发布可信度；本文规定 Desktop 产品、TUI/后端协议扩展、常驻 Spatial Workbench、桌面进程、安全、双平台和前端验收；Agent 调度、完成语义、continuation 与实时团队 Canvas 统一由 `next_plan.md` 规定。

## 0. 本次重新设计的结论

Ocean Research Partner 的主界面应当是：

```text
Codex 式 Research Task 工作区
              +
Ocean 专属的常驻 Spatial Workbench
              +
Open Science 式真实产物、运行与 provenance
```

它不是一个把 Chat、Artifacts、Papers、Runs、Map、Plot、Code 全部平铺出来的功能仪表盘，也不是一个外层聊天窗口加临时图片预览器。

旧版计划的主要问题与修正如下：

| 旧思路 | 问题 | 新决定 |
| --- | --- | --- |
| 左侧列出 Projects、Sessions、Artifacts、Papers、Runs | 导航按后端对象分类，研究者必须先理解系统内部结构 | 左侧以 Project 和 Research Task 为主；artifact/run 在任务上下文中按需展开 |
| 中间 Chat，右侧通用 Research Canvas tabs | 像功能集合，右侧内容随 tab 替换，地图失去连续空间上下文 | 右侧固定为 Spatial Workbench；地图持续存在，其他图由地图对象打开 |
| 底部始终显示 Run Drawer | 长期占用空间，即使当前只是读论文或讨论思路 | tool/run/review 默认内联摘要，详细信息按需打开 |
| 所有输出都是同级 tab | spatial field、时间序列、论文和代码之间没有领域关系 | spatial field 是地图图层；时间序列、T-S、剖面和断面图通过 Point/Transect/Region 关联 |
| Session 只是协议连接概念 | 无法形成 Codex 式可恢复任务历史 | 增加 durable `ResearchTask`，一个 task 持有对话、请求、空间上下文和产物索引 |

### 0.1 从参考产品吸收什么

从 Codex App 的界面吸收：

- 新建任务、项目分组和任务历史是第一层导航。
- 当前任务的对话是视觉中心，composer 固定在任务底部。
- agent activity 保持安静、可折叠，界面不强迫用户盯着内部日志。
- 输出是当前任务产生的对象，不是另一个独立产品。
- 大量留白服务于长时间阅读，不使用 dashboard 卡片墙。

从 Open Science Desktop 吸收：

- 一个研究任务应串联 conversation、files、code、run、figure、report 和 review。
- agent 的结果必须落成可检查的真实 artifact，不能只存在于聊天回复。
- artifact 应回溯到代码、输入、环境、运行和对话。
- session、数据、notebook、provenance 和 run record 默认保存在本地 workspace。
- 模型、tools、connectors 和 skills 可以扩展，但 UI 不应被某个模型供应商绑定。

明确不照搬：

- 不把 explore -> survey -> experiment -> paper 固化成所有任务必须经过的 skill 流水线。
- 不把 anomaly、trend、transport 等海洋算法写进 skills 作为固定 kernel。
- 不复制 Codex 中 worktree、pull request、site 等代码产品导航；只保留适合科研的 Project、Task、Sources、Skills/Connections 和 Settings。
- 不因 Open Science Desktop 使用 Tauri 就切换现有技术决策；产品结构与桌面技术栈分开评估。

## 1. 产品原则与已确定决策

### 1.1 产品原则

1. **Task-first**：研究者打开的是一个持续任务，不是一组后端资源列表。
2. **Conversation-led**：用户直接描述问题；系统自行判断是否需要检索、阅读、coding、运行或绘图。
3. **Spatial context is persistent**：地图是海洋研究的持续空间上下文，不是一次性 artifact preview。
4. **Artifacts over chat claims**：重要数据、图、代码、报告和判断必须有 exact artifact ref。
5. **Progressive disclosure**：默认只显示结论、关键活动和地图；日志、checks、code 和 provenance 按需展开。
6. **Local-first and recoverable**：任务、地图状态、artifacts、runs 和 provenance 保存在 project-local state，重启后恢复。
7. **Coding when useful, not coding-first**：论文分析和研究讨论可以不写代码；需要数据计算时才进入 AnalysisRun。
8. **Skills are research norms**：skills 规定海洋研究检查、证据、方法选择和汇报规范，不承载固定分析实现。
9. **Exact references, no hidden context**：地图、composer、agent 和报告都通过精确版本引用连接，不能靠标题或当前 tab 猜测。
10. **Desktop is primary, TUI remains valid**：Desktop 是研究者主界面；TUI 保留为开发、自动化、无图形环境和恢复入口。

### 1.2 技术与平台决策

| 主题 | 决策 |
| --- | --- |
| 主产品形态 | Electron + React + TypeScript Desktop App |
| 支持平台 | Desktop v1：macOS 13+ arm64/x64、Windows 11 x64 |
| 主布局 | 左侧 Project/Task sidebar，中间 Conversation，右侧常驻 Spatial Workbench |
| Workspace 模型 | v1 单实例、单窗口、一个 active project/workspace、一个 workspace-bound Python sidecar |
| Backend | Python `ocean_partner` 继续拥有 agent、task、workspace、artifact、run、review、permission 和 durable state |
| Renderer | 无 Node、filesystem、process 或 credential 权限，只发送 typed intent 和显示 bounded resources |
| 领域协议 | Protocol v2 是唯一领域协议；先增加 Task 与 Spatial Context contract，再实现 UI |
| Host 协议 | 独立 DesktopHost Protocol 仅处理 picker、窗口、通知、更新和受限 artifact resource |
| 地图 | MapLibre 组件嵌入 App；Plot Studio 的能力迁移后不再为 Desktop 打开外部浏览器 |
| 图表 | uPlot/专用 renderer；LinkedPlot 通过 MapScene feature 与 Selection 精确关联 |
| 科研执行 | 模型生成代码只在 backend 管理的 AnalysisRun sandbox 中执行 |
| 打包 | App、preload、renderer 和 Python sidecar 原子发布；用户不需要预装 Python/Node |
| 隐私 | 默认无遥测；provider disclosure、诊断导出和 secret redaction 沿用 backend 规则 |

Electron v1 决策保持不变。Open Science Desktop 证明 Tauri + sidecar 也可行，但当前项目已经拥有 React/MapLibre 前端、Node client 和 Python stdio backend；此时切换 Rust bridge 会把产品重构与平台重构绑在一起。只有实际 package size、内存或安全维护数据超过预算时，才重新打开 ADR。

## 2. 核心领域模型

### 2.1 关系图

```text
Project / Workspace
  |
  +-- ResearchTask A
  |     |-- display transcript
  |     |-- stable ConversationCheckpoint
  |     |-- live QueryEngine only while active
  |     |-- AgentRequest 1..n
  |     |-- pinned context refs
  |     |-- active MapScene exact ref
  |     |-- TaskMapState
  |     +-- output/provenance index
  |
  +-- ResearchTask B
  |
  +-- immutable Artifacts
  |     |-- Dataset / Paper / Claim / Figure / Report
  |     |-- Selection
  |     |-- SpatialLayer
  |     |-- LinkedPlot
  |     +-- MapScene
  |
  +-- AnalysisRuns / Attempts / Checks
  +-- Reviews / Decisions
  +-- Skills / References / Connections
```

### 2.2 名词与权威来源

| 名词 | 含义 | 权威来源 |
| --- | --- | --- |
| Project | 一个本地科研 workspace，拥有数据、artifacts、tasks 和 runs | project-local backend state |
| ResearchTask | 可命名、可恢复、可归档的研究线程 | backend TaskStore |
| ClientConnection | renderer/TUI 与 backend 的临时连接 | 运行时，不等于 ResearchTask |
| AgentRequest | task 中一次用户提交及其完整终态 | RequestStore |
| DisplayTranscriptItem | UI 可分页显示的用户、assistant、tool 或 system 记录 | TaskStore append-only transcript |
| ConversationCheckpoint | backend 恢复模型记忆所需的完整 `ConversationMessage[]` 稳定快照 | TaskStore，永不发送给 renderer |
| LiveAgentRuntime | 当前活动 task 的临时 `QueryEngine`、provider client 与 tools | backend memory，可由 checkpoint 重建 |
| Artifact | 有版本、不可变、可引用的研究对象 | Artifact Store |
| MapScene | 只通过 exact refs 组合图层、selection 和 linked plots 的空间场景 | MapSceneArtifact |
| TaskMapState | 当前 task 打开哪个 MapScene、视角、图层显隐/透明度和活动对象 | TaskStore 中的非 evidence view projection |
| SpatialLayer | 可直接叠加到地图的检查后空间场 | SpatialLayerArtifact |
| Spatial Anchor | 地图上的 Point、LineString/Transect 或 Polygon/Region | `SelectionArtifact + MapScene feature` 的 UI projection，不新增重复 artifact type |
| LinkedPlot | anchor 关联的时间序列、T-S、profile、section、Hovmoller 或 scatter | LinkedPlotArtifact |
| AnalysisRun | 代码、输入、环境、attempt、checks 和 outputs 的可复现执行记录 | AnalysisRun store |

### 2.3 ResearchTask 合同

`ResearchTask` 至少包含：

```text
task_id
project_id
title
status: active | completed | archived
created_at / updated_at
active_request_id?
last_terminal_state?
pinned_context_refs[]
active_map_scene_ref?
output_refs[]
transcript_cursor
stable_checkpoint_id?
conversation_generation
task_revision
```

规则：

- 一个 task 可以包含多轮请求和多次 AnalysisRun，但同一时刻最多一个 foreground agent request。
- task title 可以由模型建议，但最终是普通 typed mutation，不从 assistant 文本自动解析。
- task 归档不删除 artifacts、runs、provenance 或 transcript。
- task 列表只加载摘要；打开后才请求 transcript、map state 和 output projection。
- artifact 可以被多个 tasks 引用；task output index 是关系，不复制 artifact。
- `task_id` 写入 RequestRecord、AnalysisRun provenance 和新 artifact origin，保证从产物回到任务。
- 切换 task 不取消另一个 task 的历史；v1 若有活动请求，必须让用户保持当前任务、取消后切换，不能静默并行。
- task snapshot 可以返回 display transcript，但不得把内部 `ConversationMessage[]`、system prompt 或 compaction memory 发给 renderer。
- v1 backend 同时只保留一个 live foreground `QueryEngine`；打开另一个 task 时关闭空闲 runtime，并从目标 task 的稳定 checkpoint 重建。

### 2.4 Display transcript、模型记忆与 live runtime 分层

这三层必须分开，否则“App 看起来恢复了聊天”并不等于 agent 真正恢复了上下文：

1. **Display transcript**：面向用户，append-only、可分页，记录 user/assistant/tool/system activity、request ID、turn ID、tool call ID 和 interrupted 标记。
2. **ConversationCheckpoint**：只面向 backend，保存 `QueryEngine.messages` 序列化结果，包括 tool-use/tool-result blocks、compaction 后的 synthetic memory 和消息顺序。
3. **LiveAgentRuntime**：临时 provider client、`QueryEngine`、tool registry 和当前 execution handle；backend/app 重启后不可信，必须从 checkpoint重建。

`ConversationCheckpoint` 至少包含：

```text
checkpoint_id
task_id
conversation_generation
terminal_request_id
message_schema_version
messages_json
message_count
provider_id
model_id
runtime_profile_fingerprint
system_prompt_fingerprint
compaction_generation
usage_summary
payload_sha256
created_at
```

持久化规则：

- 不单独保存 API key、bootstrap token、file grant、artifact resource token 或原始环境变量。
- system prompt 在恢复时由当前已签名 runtime profile 和 policy-approved workspace context重新生成；checkpoint只保存 fingerprint，不保存可泄露的隐藏 prompt全文。
- `messages_json` 使用 LangGraph checkpoint 的带版本 message schema，不退化为纯文本 transcript。
- checkpoint 有硬字节数和消息数上限；超过上限必须先经过现有 compaction，不能无限增长 SQLite blob。
- 只有 request 成功完成且 `QueryEngine.has_pending_continuation()` 为 false 时，才产生新的稳定 checkpoint。
- request terminal event、checkpoint、task `stable_checkpoint_id`、conversation generation 和 active-request clear 必须在同一个 SQLite transaction提交。
- request failed/cancelled/interrupted 时不保存部分模型历史；继续使用请求前的稳定 checkpoint。已完成的 artifact/run mutation仍由各自 store保留，下一次请求通过 workspace context看到它们。
- display transcript 可以保留失败请求的 user、assistant、tool activity，但标记 terminal state；这些部分记录不得自动进入下次模型上下文。
- compaction 发生后，最终 checkpoint必须保存压缩后的真实 message list和 compaction generation，恢复时不能重新拼接被压缩掉的旧消息。

恢复规则：

1. `task.open`读取 TaskRecord、稳定 checkpoint metadata 和 display transcript cursor。
2. backend根据当前 provider/disclosure policy构建新的 Ocean runtime。
3. 校验 message schema、payload hash和 runtime compatibility。
4. 使用现有 `QueryEngine.load_messages()`装入 checkpoint messages。
5. 重新生成 system prompt和最新 policy-approved workspace context；不把旧隐藏 prompt当作用户消息。
6. schema/hash 不兼容时返回 typed `task_checkpoint_incompatible`，保留只读 transcript和 artifacts，不静默清空记忆。
7. provider/model变化可以在用户确认 disclosure后继续，但必须创建新的 conversation generation并在 task中记录环境变化。

### 2.5 地图状态与科学证据分离

`MapSceneArtifact` 是不可变、可引用的科学场景；`TaskMapState` 是可恢复的界面投影。二者不能混为一体：

- MapScene 决定有哪些 exact `SpatialLayer`、`Selection` 和 `LinkedPlot` refs。
- TaskMapState 决定当前 viewport、图层显隐、opacity、order、活动时间/深度索引和选中 anchor。
- 改变 opacity 或缩放不创建新科学 artifact，也不改写旧 MapScene。
- 将新 layer/anchor 正式加入可引用场景时，backend 发布新的 MapScene version，再更新 task 的 active ref。
- TaskMapState 必须绑定 MapScene exact ref；scene 版本变化后，backend/renderer执行明确迁移，不能把旧 layer control 套到同名新 layer。
- 新 task 默认只继承 project base map configuration；不会暗中继承另一个 task 的分析图层。

## 3. Desktop 信息架构

### 3.1 主界面

宽屏默认布局：

```text
+----------------------+--------------------------------+----------------------------------+
| Global / Project     | Research Task                  | Spatial Workbench                |
|                      |                                |                                  |
| + New task           | Task title     status   ...    | Map toolbar   Layers   Inspect   |
| Tasks                |--------------------------------|----------------------------------|
| Projects             |                                |                                  |
| Sources              | Conversation                   | Persistent Map                   |
| Skills/Connections   | User / Assistant               | SpatialLayer overlays            |
|                      | Tool activity / permissions    | Point / Transect / Region        |
| Project A            |                                |                                  |
|   Task A1            |                                |----------------------------------|
|   Task A2            |                                | Linked Visualization Dock        |
| Project B            |--------------------------------| Time series / T-S / section ...  |
|                      | Composer + exact refs          |                                  |
+----------------------+--------------------------------+----------------------------------+
```

这仍然是三个视觉区域，但语义与旧版不同：左边只有工作组织，中间只有当前 agent task，右边始终是海洋空间上下文。Artifacts、Runs、Evidence 和 Code 是任务/地图对象的 inspector，不再占据永久一级导航和固定底栏。

### 3.2 左侧 Sidebar

固定全局入口：

- New Task。
- Tasks：跨 project 的最近、运行中、需要处理和已归档任务。
- Projects：打开、固定和切换本地 workspace。
- Sources：本地 datasets、papers 和已配置数据连接，只作为查找入口。
- Skills & Connections：研究规范 skills、references、MCP/data connectors 和 capability 状态。
- Settings：model profile、privacy、appearance、updates 和 diagnostics。

Project 分组下直接显示最近 tasks，类似 Codex 的项目/任务列表：

- task 行显示标题、running/attention/failed 状态和最后更新时间。
- 运行中只使用小型状态指示，不用动画占据整行。
- rename、archive、reveal project 进入 context menu。
- “Scheduled tasks”只有在 backend 存在真实 scheduler、durable job 和权限模型后才加入；v1 不做空壳入口。
- 不在 sidebar 长期列出 Artifacts/Papers/Runs；这些对象从任务 output index 或项目搜索打开。

### 3.3 中间 Conversation Workspace

- 顶部：task title、project breadcrumb、request state、more menu。
- 主区：居中的可虚拟化 transcript；阅读宽度约 720--820 px，外层列可更宽。
- assistant 默认显示结论与阶段摘要；tool calls、代码修改、run attempts 和 checks 折叠在对应 turn 下。
- permission、question、disclosure、review 和 proposal 使用 inline decision row 或 modal。
- composer 固定在底部，支持自然语言、文件 grant、paper/dataset、artifact ref 和 map selection chips。
- `Cancel` 在有活动请求时始终可见，并区分 agent request 与 active run attempt。
- `assistant.turn.completed` 不清除 busy；只有 request terminal event 才清除。
- compaction 只显示“上下文已整理”的 marker 和可审计摘要，不伪装成用户对话。

Conversation 不要求用户先选“论文模式”“绘图模式”或“coding 模式”：

- “比较两篇论文的机制解释”可以只检索/阅读/推理。
- “检查这个数据集变量和坐标”可以只做 metadata inspection。
- “计算 2010 年中国海 SST 并画图”才进入 planning -> code -> AnalysisRun -> checks -> artifacts。
- “沿这条 transect 画温盐断面”可以直接使用地图 selection chip 作为精确上下文。

### 3.4 右侧常驻 Spatial Workbench

地图是 Desktop 的领域核心，不是可被 Figure/Paper/Code tab 替换的普通 preview：

- 在支持的宽屏窗口中，地图默认持续可见。
- 切换 task 时恢复该 task 的 MapScene 和 TaskMapState。
- 对话流式输出、tool activity 展开和 composer 高度变化不能重建 MapLibre instance 或丢失 viewport。
- 没有空间 artifact 的论文任务仍显示 project base map/空场景，保持低干扰状态。
- 用户可以显式进入 Conversation Focus 或 Map Focus；退出后地图状态原样恢复。
- PDF、code、figure 或 evidence inspector 打开时，地图仍保留可见区域；只有用户主动全屏查看 artifact 时才暂时占满主区。

右侧由两个稳定区域组成：

1. **Persistent Map**：常驻，显示 spatial fields 和 spatial anchors。
2. **Linked Visualization Dock**：默认收起；点击 anchor 后在地图下方或旁边展开，不替换地图。

### 3.5 窗口与响应式规则

- `>= 1440 px`：sidebar 220--240 px；Conversation min 560 px；Spatial Workbench 占剩余约 40%--48%。
- `1200--1439 px`：sidebar 可折叠为窄 rail；Conversation 和 Spatial Workbench 双栏，地图仍可见。
- `< 1200 px`：进入 compact mode；Conversation/Map 用显式 segmented view 切换，但 MapLibre 和 TaskMapState 保持挂起/可恢复，不重置场景。
- 最低支持窗口尺寸在 D0 prototype 后冻结；低于阈值显示可恢复的 compact layout，不允许控件重叠。
- Linked Visualization Dock 默认占 Spatial Workbench 高度的 35%--45%，可拖动；地图始终保留可操作的最小高度。
- 图层面板与 inspector 是 overlay/drawer，不把地图装进装饰性 card。

## 4. Spatial Workbench 详细设计

### 4.1 Spatial field：直接叠加地图

`SpatialLayerArtifact` 在 MapScene 中作为真实地图图层呈现，至少支持：

- visibility、opacity、layer order。
- colorbar、units、variable、time/depth label、data range 和 nodata。
- 经检查的 EPSG:4326 registration、经度约定、纬度方向和 dateline parts。
- 时间或深度维度存在时的 typed selector；切换后必须指向明确 slice/version，不靠文件名推断。
- 点击 layer 后打开 Layer Inspector：registration、source dataset、run、checks、code snapshot 和 exact ref。

Renderer 只读取 backend 发布的 bounded PNG/tile/structured resource；不能直接在 renderer 打开任意 NetCDF，也不能把大数组放进 Redux/IPC。

### 4.2 Point / Station anchor

Point `SelectionArtifact` 在地图上显示为 marker。点击后：

- 高亮 marker，并在 Linked Visualization Dock 打开默认 `LinkedPlot`。
- 若存在多个 plot refs，使用紧凑 segmented selector 切换时间序列、profile、T-S、scatter 等。
- 显示站点名、坐标、时间/深度范围、units、source refs 和 provenance 入口。
- “Use in task”把 exact selection ref 和可选 linked plot ref加入 composer，不复制坐标文本代替引用。
- 点击地图空白可以创建临时 probe；只有用户确认或 agent 正式发布后才成为 SelectionArtifact。

### 4.3 Transect anchor

LineString `SelectionArtifact` 显示为 transect。点击后：

- 地图保留完整线路和起止方向标记。
- Dock 展开 section、Hovmoller、transport 或沿线 profile 等关联图。
- 图的 horizontal axis 与 transect distance/waypoint 关系必须来自 LinkedPlot schema。
- hover/cursor 可以在地图与断面图之间联动，但只作为 view state，不修改 artifact。
- “Use in task”把 transect exact ref加入下一轮，例如“沿这条线比较 2010 和 2020 年温盐结构”。

### 4.4 Region anchor

Polygon `SelectionArtifact` 用于海区、数据覆盖范围或统计区域：

- 可以关联 area-mean time series、histogram、anomaly distribution 或 budget summary。
- spatial field 与 region 可以同时显示，region 边界不得遮挡 color field。
- region aggregation 的 mask、weighting、calendar 和 missing-data 规则必须存在于 AnalysisPlan/FigureSpec 或 provenance，不藏在 UI。

### 4.5 Linked Visualization Dock

第一版支持现有 `LinkedPlotKind`：

- `time_series`
- `profile`
- `section`
- `ts_diagram`
- `hovmoller`
- `scatter`

行为合同：

- LinkedPlot 必须引用 Selection exact ref；MapScene feature 的 `plot_refs` 决定可打开内容。
- Dock 只呈现当前 active anchor 的 plots，关闭 Dock 不清除地图 selection。
- 切换 plot 不改变 MapScene artifact；仅更新 TaskMapState 的 active plot ref。
- 图表 hover、zoom 和 range selection 可与地图联动，但不能隐式成为 agent context。
- 用户点击“Use in task”后，selection/plot refs 才作为明确 context chip进入请求。
- plot、source data、Figure、run、checks、code 和 review 可通过 inspector 双向追踪。

### 4.6 非空间输出

Paper、Report、Figure、Code、Dataset 和 Evidence 不进入永久顶级 tabs：

- assistant turn 中显示 compact artifact row。
- task header 提供 Output button，打开当前 task 的可搜索 output shelf。
- 点击对象打开 Artifact Inspector；默认占 Conversation 的临时 focus area或 Spatial Workbench 的受限 drawer，但不销毁地图。
- Figure 若关联 LinkedPlot/MapScene，inspector 提供“Locate on map”。
- Paper/Report PDF 使用隔离 viewer，显示文档不等于把全文发送给模型。
- Code 只显示 immutable snapshot；“Edit and rerun”创建新的 staged attempt，不改写历史。

## 5. Agent 交互与研究流程

### 5.1 一个 task 内的基本循环

```text
User submits question + exact context
-> agent interprets research intent
-> inspect papers/data/artifacts as needed
-> decide whether coding is needed
-> if needed: plan -> write code -> AnalysisRun -> checks
-> publish real artifacts and provenance
-> update task output index / active MapScene
-> explain result, limits and next choices
```

UI 不以固定 workflow stepper 强迫所有任务经过同一路径。系统可以在讨论、检索、coding、绘图和写作之间往返，但每一次 mutation 都走 typed protocol，并保留可检查的终态。

### 5.2 Skills 的位置

Desktop 可以提供 Skills & Connections 页面，但 skills 的职责仍是：

- 数据坐标、单位、calendar、mask、weighting、uncertainty 和 provenance 检查规范。
- 海洋异常、趋势、水团、输运、剖面、断面和地图制图时应考虑的问题。
- 文献证据、引用、假设、实验、审阅和报告规范。
- coding/xarray/matplotlib 等工具使用约束与常见错误检查。

skills 不包含 anomaly/trend/transport 的固定函数实现。模型根据数据 schema、研究问题和方法参考生成 task-specific code，并由 AnalysisRun、independent checks 和 artifact publisher 约束正确性。

### 5.3 权限与活动展示

- 普通 tool activity 在对应 assistant turn 下显示一行摘要，可展开参数类型、耗时和结果 refs。
- command execution、dependency installation、remote fetch、file import、delete 和外部连接需要 typed approval。
- provider disclosure 显示将发送的内容类别，不泄露 secret。
- 长 AnalysisRun 在 task header 和 turn 内同时显示进度；详细 logs 按需打开。
- stdout/stderr 默认只显示 bounded redacted tail。
- checks 明确显示 `state`、`origin`、`independence` 和 `evidence_level`，禁止含混地标记“科学验证通过”。
- failed attempt 保留 Inspect、Edit and rerun、Abandon；不允许绕过 sandbox 后直接 Publish。

## 6. TUI 与 Backend 协议先行

在做视觉 UI 前，先冻结 Task、Spatial Context 和 Desktop Host 三个合同。Desktop 和 TUI 必须使用同一 Protocol v2，不新增 Electron 专属科研 mutation。

### 6.1 Protocol v2 增量

新增 `client_kind: desktop`，并增加下列领域请求；最终命名以 schema ADR 为准：

```text
task.create
task.list
task.get
task.open
task.rename
task.archive
task.reopen
task.snapshot.get
task.context.pin
task.context.unpin
task.map_state.update
task.output.list
```

`RequestContext` 增加 `task_id`；除 handshake、workspace 和 task-list/create 类请求外，task-bound request/event都必须携带它。现有 `session.submit` 可以在 Protocol v2 内继续保留名称，但它只表示“向指定 task 提交一次 agent request”，不能再用 transport `session_id`代表长期对话。如果当前 `session.*` 同时承担 transport session 与 conversation session，Phase F0 必须先拆清语义；不能让 Desktop 通过本地数组伪造 task history。

`task.snapshot.get` 返回 bounded snapshot：

```text
TaskRecord
transcript page/cursor
active request summary
pinned exact refs
active MapScene exact ref
TaskMapState
output summaries
workspace_revision + task_revision
stable checkpoint metadata
```

规则：

- Python Pydantic 是单一类型源，生成 JSON Schema 与共享 TypeScript unions。
- request ID、canonical payload hash、revision conflict、唯一 terminal event 和 crash replay 语义保持不变。
- task mutation 使用 `task_revision`；科学 artifact mutation 继续使用 `workspace_revision`。
- MapState 更新不能修改 artifact、run 或 workspace scientific revision。
- Event envelope 增加 optional `task_id`；task-bound events必须填充，workspace广播不能把一个task的 transcript/stream发给另一个task视图。
- `task.snapshot.get`只返回 checkpoint ID、generation、compatibility状态和 display transcript；内部 messages payload不属于任何客户端 capability。
- Conversation checkpoint是 backend internal commit，不提供 `task.checkpoint.write` 客户端请求，也不允许renderer上传模型历史。
- TUI 至少能 list/open/create task，并显示 active MapScene refs；无需复制完整图形界面。

### 6.2 TaskStore、checkpoint 与迁移合同

建议在现有 project-local SQLite 中增加：

```text
research_tasks
task_transcript_items
task_conversation_checkpoints
task_map_states
task_artifact_links
```

并为现有 `request_records` 增加 nullable/indexed `task_id`。核心约束：

- `research_tasks(task_id, workspace_id)` 是所有 task-local rows 的父记录；删除 workspace/task不得级联删除 immutable artifacts/runs。
- `task_transcript_items` 使用 task-local monotonic sequence，记录 role、bounded text、request/turn/tool IDs、terminal/interrupted projection和时间；streaming delta不逐token写数据库。
- `task_conversation_checkpoints` 只允许backend写入，`payload_sha256`覆盖canonical versioned messages payload。
- 每个 task 同一 generation只有一个 current stable checkpoint；旧 checkpoint可在有界保留策略下审计，但不能被renderer任意选择注入模型。
- `task_map_states` 绑定 exact MapScene ref，使用独立 `task_revision`，保存bounded viewport/layer controls/active anchor/plot。
- `task_artifact_links`只保存 task与 exact artifact ref的关系、relation和origin request，不复制artifact content。
- 所有 JSON column在写入前由Pydantic schema验证，读取时再次验证；未知版本fail closed。

成功请求的原子提交顺序：

```text
agent loop reaches a stable terminal point
-> serialize and validate ConversationMessage[]
-> compute payload hash and checkpoint metadata
-> BEGIN IMMEDIATE
-> commit request.completed terminal event
-> insert stable conversation checkpoint
-> update ResearchTask generation/checkpoint/active_request
-> append final display transcript projections
-> COMMIT
-> broadcast terminal/task events
```

崩溃语义：

- transaction之前崩溃：请求恢复为 `interrupted`，旧checkpoint仍是唯一稳定记忆。
- transaction中崩溃：SQLite原子回滚，不允许terminal event与checkpoint只成功一半。
- commit之后、broadcast之前崩溃：RequestStore replay同一terminal，task snapshot指向已提交checkpoint，不生成第二份。
- renderer/backend重连只读取authoritative task snapshot，不根据本地最后一条assistant消息猜测checkpoint。

旧数据库迁移：

- migration前执行现有私有备份与schema-version记录。
- 保留全部workspace、artifact、run、review、request和session records。
- 旧 `session_records` 没有真实 `ConversationMessage[]` 时不得伪造可恢复task memory；UI可以显示“旧会话没有可恢复模型上下文”。
- 不自动把workspace中最新MapScene绑定到所有新task；首次创建task时由用户/agent显式选择base scene。
- migration必须幂等，并覆盖旧库、空库、部分失败恢复和downgrade拒绝fixture。

### 6.3 MapScene/LinkedPlot 合同不重复发明

当前 backend 已有 `SelectionArtifact`、`SpatialLayerArtifact`、`LinkedPlotArtifact` 和 `MapSceneArtifact`，新版前端直接以它们为领域基础：

- `MapScene.layer_refs` -> spatial field overlays。
- `MapScene.feature_refs/features` -> Point、LineString、Polygon anchors。
- feature `selection_ref` -> canonical geometry。
- feature `plot_refs` -> 点击 anchor 后的 linked visualizations。
- `MapScene.linked_plot_refs` -> scene 中所有可达 plots。

“SpatialAnchor”只是 renderer selector 生成的 view model，不能新增一套不同的数据库对象或 JSON schema。

### 6.4 Main 与 backend private transport

- Electron main 以 stdin/stdout JSONL 拥有一个 workspace-bound sidecar。
- stdout 只允许 Protocol v2 event envelope；diagnostics 进入 bounded/redacted stderr。
- main 与 backend 分别用 AJV/Pydantic 验证 payload，发现 sequence gap 时停止应用增量并请求 authoritative snapshot。
- renderer reload 不终止 backend request；重新连接后恢复 task snapshot。
- backend restart后按active task的stable checkpoint重建`QueryEngine`；不复用退出进程中的runtime对象。
- handshake 校验 app/backend version、protocol range 和 schema hash。
- main 不解释 assistant 文本、不改写 domain payload、不直接写 SQLite。

### 6.5 DesktopHost Protocol

Host 协议只允许：

```text
app.info.get
dialog.workspace.pick
dialog.file.pick
artifact.resource.open
artifact.reveal
external_link.open
notification.show
update.check
update.install
window.state.get/set
```

明确禁止任意 `file.read`、`file.write`、`shell.execute`、URL fetch 和 raw IPC。preload 只导出命名方法；renderer 不接触 `ipcRenderer`。

### 6.6 FileGrant 与 artifact resource

- 系统 picker/drag-drop 产生短期、单次、workspace-bound opaque grant。
- renderer 只看到 grant ID 和 display name，不得到绝对路径。
- backend核对 purpose、identity、size、expiry 后执行 immutable materialization。
- Renderer 只通过 `ocean-artifact://resource/<opaque-token>` 读取 allowlisted bytes。
- token 绑定 WebContents、workspace、artifact URI、MIME、size 和 expiry。
- PDF、SVG、PNG、JSON 和 plot payload 分别执行 CSP、MIME、shape、point-count 和 byte limits。
- NetCDF、完整 logs、raw arrays 和 provider secrets 不进入 renderer store。

## 7. 进程与信任架构

```text
Electron Main
  |-- window / menu / update / OS dialogs
  |-- Python sidecar supervisor
  |-- Protocol v2 stdio transport
  |-- DesktopHost Protocol
  +-- ocean-artifact:// broker
          |
          v
Context-isolated Preload
          |
          v
React Renderer
  |-- Project/Task sidebar
  |-- Conversation workspace
  |-- persistent Spatial Workbench
  +-- no Node / fs / process / secrets

Python Ocean Backend
  |-- agent loop / compact / tools
  |-- TaskStore / RequestStore
  |-- Artifact Store / AnalysisRun
  |-- permissions / review / provenance
  +-- platform sandbox adapter
```

Electron main 只管理本机生命周期，不成为第二个 backend。所有 task、artifact、run、review、permission 和 map-scene mutation 都必须经过 Python router。

Workspace lifecycle：

- v1 同时只打开一个 project 和一个 sidecar。
- v1 同时只激活一个 ResearchTask runtime；其他tasks只有durable metadata、transcript、checkpoint和map state，不长期占用provider/MCP资源。
- 打开task时由backend验证并hydrate checkpoint；renderer不能创建或缓存`QueryEngine`消息。
- 切换 project 前处理 active request/attempt：保持当前、取消后切换或返回。
- project/task 切换使旧 file grant 和 artifact resource token 失效。
- renderer crash/reload 不关闭 sidecar；backend crash 最多自动重启一次并从 stores恢复。
- app quit 先 graceful shutdown，再 bounded process-tree cleanup。
- 未正常终止的 attempt 下次启动显示 `interrupted`，不能伪装 resume。

## 8. Frontend 代码与状态边界

### 8.1 建议目录

```text
frontend/
  packages/
    ocean-client/             # generated protocol, validators, reducers, transport interfaces
    ocean-ui/                 # tokens, icons, accessible controls
    ocean-spatial/            # spatial contracts, bounded validators, offline graticule
  ocean-desktop/
    src/main/                 # Electron main, sidecar, host protocol, resource broker
    src/preload/              # typed narrow bridge
    src/renderer/
      app-shell/
      features/projects/
      features/tasks/
      features/conversation/
      features/spatial-workbench/
      features/artifacts/
      features/runs/
      features/interactions/
    tests/
  ocean-terminal/             # retained TUI, consumes ocean-client
  plot-studio/                # migration source; remove only after parity
```

### 8.2 Shared ocean-client

- generated Protocol v2 request/event unions 和 AJV validators。
- request lifecycle、sequence tracker、snapshot resync 和 compatibility helpers。
- transport-independent reducers/selectors。
- 不依赖 React、Ink、Electron、DOM 或 MapLibre。
- TUI 与 Desktop 共用协议行为，但拥有不同 view components。

### 8.3 Renderer normalized store

建议 slices：

- `backend`：starting/ready/degraded/restarting/crashed。
- `projects`：recent/active project summary。
- `tasks`：task summaries、active task、task revisions。
- `transcript`：paged turns 和 compaction markers。
- `requests`：active/terminal request summaries。
- `artifacts`：summary index 和 exact refs。
- `mapScenes`：MapScene projections、SpatialLayer/Selection/LinkedPlot summaries。
- `taskMapState`：viewport、controls、active anchor/plot。
- `runs`：run/attempt/check summaries。
- `interactions`：permission/question/disclosure/review/proposal。
- `view`：sidebar、split sizes、focus mode、theme。

MapLibre runtime object、raster bytes、PDF、完整 plot arrays、NetCDF 和 logs 不进入 Redux。Conversation streaming state 与 map state 分离，保证 token 更新不会触发地图重建。

## 9. 桌面安全与跨平台合同

### 9.1 Electron 安全基线

- `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`。
- 禁用 remote module、`webview`、任意 navigation、新窗口和 renderer shell open。
- CSP 默认 `default-src 'self'`；不加载 remote code、CDN、font 或 analytics。
- IPC channel、payload、response 和最大尺寸全部 allowlisted/schema-validated。
- sidecar/OS command 使用固定 executable + argument array，不使用 shell string。
- external link 只允许用户触发的 HTTPS，并显示目标 host。
- credentials 使用 macOS Keychain / Windows DPAPI-backed storage；renderer只看到 profile ID。
- PDF/SVG viewer 禁止 script、form、external resource 和 navigation。
- updater 校验签名、版本单调性与 app/backend schema compatibility。

### 9.2 平台支持矩阵

| 能力 | macOS 13+ arm64/x64 | Windows 11 x64 |
| --- | --- | --- |
| Project/Task/Chat | required | required |
| Persistent Map/SpatialLayer/LinkedPlot | required | required |
| Dataset/Paper import grants | required | required |
| Agent request/cancel/recovery | required | required |
| Scientific Python sidecar | required | required |
| Trusted AnalysisRun | required | required |
| Signed installer/update | required | required |

Windows UI preview 可以先出现，但 trusted AnalysisRun 在安全 ADR 通过前必须 fail closed，不能用 warning + unsandboxed fallback 补齐功能。Job Object 只解决资源和进程树，不等于文件/网络隔离。

### 9.3 Packaging

- Python sidecar 优先 spike PyInstaller `onedir`；每个平台独立构建。
- `electron-builder` 生成 macOS DMG/ZIP 与 Windows NSIS。
- app、backend 和 protocol schema hash写入同一 version manifest。
- first launch 不执行 npm/pip install，不依赖系统 Python/Node。
- workspace 永远留在用户 project；卸载默认不删除科研数据。
- stable release 需要 macOS signing/notarization 与 Windows Authenticode。

## 10. 分阶段实施

### Phase F0：Task 与 Spatial Protocol Freeze

目标：先把 UI 最容易做错的 task/map contract 和真正可恢复的agent memory定清。

- [x] ADR：ResearchTask、ClientConnection、AgentRequest 的边界。
- [x] ADR：TaskStore、task revision、artifact/task provenance 和ConversationCheckpoint关系。
- [x] ADR：MapScene vs TaskMapState，确认 SpatialAnchor 只是 Selection projection。
- [x] 为`ConversationMessage[]`定义versioned checkpoint schema、hard size limit、hash和compatibility policy。
- [x] 增加`research_tasks`、transcript/checkpoint/map-state/artifact-link表与旧库migration。
- [x] 把request terminal event、stable checkpoint和task generation做成一个SQLite transaction。
- [x] runtime按`task_id`hydrate，并通过`QueryEngine.load_messages()`恢复；renderer永远收不到messages payload。
- [x] 扩展 Protocol v2 schema 和 generated TypeScript。
- [x] TUI 增加最小 task create/list/open 支持。
- [x] 冻结宽屏/compact wireframes 和 keyboard flow（[Desktop Workspace Interaction Contract](docs/desktop/workspace-interaction-contract.md)）。
- [x] 使用已有 MapScene fixtures 制作可点击 Point、Transect、Region prototype：loopback capability 为 desktop/mobile 各签发一次性 bootstrap URL；浏览器 visual fixture 验证 marker、polygon、transect 分别打开 time-series/profile/section，并检查图层控制、dateline raster parts 和截图中的空间方位。

F0 implementation note (2026-07-14):

- `src/ocean_partner/backend/store.py` now owns `ResearchTask`, bounded display transcript, private `ConversationCheckpoint`, `TaskMapState`, schema migration v15, revision checks, checkpoint hashing and atomic terminal commits.
- `src/ocean_partner/backend/router.py` exposes task create/list/get/open/rename/archive/reopen/snapshot/map-state commands. Task-bound `session.submit` restores a checkpoint through `QueryEngine.load_messages()` and commits its new checkpoint only after a complete agent turn.
- `RequestContext.task_id` is intentionally optional while the legacy TUI is migrated. Desktop and future task-aware clients must supply it for task-bound work.
- Contract decisions are recorded in ADR 0014--0016. Current tests live in `test_research_tasks.py`, `test_task_router.py`, and `test_agent_router.py`; the remaining unchecked acceptance cases below are required before F0 is declared complete.

- Offline agent-router coverage now creates Task A and Task B through the typed TUI `task.create` route, submits distinct `ALPHA_MARKER` / `BETA_MARKER` turns, closes the backend, then continues both tasks in fresh runtimes. Each resumed `QueryEngine` receives only its own checkpoint history and never the other task's marker. This proves task creation, private display/checkpoint restoration and runtime memory isolation together, rather than inferring them from store-only records.

- `QueryEngine` now owns a restorable `compaction_generation`: every successful automatic compaction increments it, a cancelled request restores the prior generation alongside its prior messages, and task checkpoint hydration restores the saved count. Offline coverage drives four turns through deterministic session-memory compaction, verifies the stored synthetic memory has replaced the older individual messages, restarts the backend, and verifies the resumed model context contains only that synthetic memory, the recent turns, and the new submission.

- Corrupt checkpoint handling is now exercised through the real `session.submit` route, not only through `RequestStore`: the router fails closed with `task_checkpoint_incompatible` before a second model call, while a following `task.open` reports checkpoint incompatibility and preserves the original display transcript for recovery.

- Desktop task-router coverage now creates two empty but valid `MapScene` artifacts, pins a distinct scene, viewport and layer-control state to each task, then reopens both tasks. The returned snapshots retain their own exact scene refs and presentation state, proving task switches cannot leak the other task's persistent map state.

- The task-agent terminal path now lets the test-only `CrashAfterTerminalCommit` signal escape its background task instead of converting it to a model error. Its regression test commits a task checkpoint, crashes before terminal broadcast, restarts the backend, and replays the same request without a second model call or a second checkpoint. Store-level task coverage now also proves the other two windows: a request left active before its terminal transaction is recovered as interrupted while retaining the prior stable checkpoint, and an exception after terminal-row work inside the checkpoint transaction rolls back its event, checkpoint, task pointer and generation before the same recovery path runs.

- Task map-state persistence now enforces MapScene membership in the backend transaction. An active Selection must be a current scene feature, and an active LinkedPlot must be attached to that exact feature; tests reject a same-workspace cross-scene Selection and an unattached plot ref. The renderer's existing restore-time membership check is now defense in depth rather than the authority.

- Protocol v2 now has a regression gate proving its generated request/event schemas and TypeScript contract contain neither Electron-specific domain concepts nor a `SpatialAnchor`/`spatial_anchor` artifact model. Running that gate exposed stale checked-in contracts after the diagnostic-export addition, so `protocol/v2/schema/*.json` and the terminal TypeScript contract were regenerated from the current Pydantic models and are reproducible again.

- The v1 wide/compact interaction contract now freezes the three-pane task-first geometry, compact Task/Map switcher, focus-mode and spatial-state invariants, and the small keyboard surface. Electron coverage verifies palette action activation by keyboard and the compact Task/Map semantic buttons at the minimum size and 200% zoom, so later visual polish cannot silently turn those paths into pointer-only behavior.

验收：

- [x] backend fake client 与 TUI 可创建两个tasks，分别提交请求并恢复各自display transcript和模型记忆。
- [x] backend完整重启后，fake model在Task A仍记得A的marker，在Task B只记得B的marker，二者不串上下文。
- [x] compaction后保存并恢复synthetic memory；被压缩掉的旧messages不重新出现。
- [x] 请求在checkpoint transaction前、中、后crash时分别得到预期interrupted/rollback/replay结果，不产生半提交。
- [x] corrupted/incompatible checkpoint只读可见并typed fail closed，不静默清空或把display transcript当模型历史。
- [x] 两个 tasks 指向不同 MapScene，来回切换不会串 layer/viewport/selection。
- [x] Point/Transect 的 plot refs 只从 exact MapScene/Selection/LinkedPlot refs解析。
- [x] 协议中没有 Electron-only domain mutation，也没有第二套 SpatialAnchor artifact。

### Phase F1：Desktop Shell 与 Sidecar

目标：建立真实 Desktop app 壳和长期边界。

- [x] 建立 `ocean-client`：生成 Protocol v2 types、transport sequence/revision helpers，TUI 与 Desktop 均直接消费。
- [x] 提取 `ocean-ui`：只在跨 renderer 重用的 DOM/React controls、tokens 和 accessibility primitives 已稳定后进行。
- [x] 提取 `ocean-spatial`：共享空间 artifact contract、bounded validators 和离线 graticule；MapLibre renderer components仍只在视图交互契约进一步收敛后再提取。
- [x] 建立 `ocean-desktop` package。
- [x] Electron main实现 sidecar supervisor、single-instance、handshake、一次异常退出后的自动重启、shutdown 和源 checkout 的 Python 解析。
- [x] preload实现 typed bridge；renderer无 Node/fs/process能力。
- [x] 实现左侧 Projects/Tasks sidebar、project picker 和任务创建/打开。
- [x] 将 Skills & Connections 落为侧栏中的渐进展开 `Research guidance`：只显示后端显式投影的研究规范 metadata 与 AnalysisRun/NetCDF/Zarr/TEOS-10 可用状态；Protocol v2 的 `DesktopRuntimeCapabilitiesPayload` 固定连接 inventory/标签并限制 runtime/skill 字段，`system.ready` 不再透传 doctor 的解释器、安装路径、`sys.prefix`、命令或原始失败原因。完整本机诊断仍只由 `ocean doctor` 提供。
- [x] 将 Settings 落为侧栏底部的真实模态工作面：显示安全的项目名、连接状态、固定 runtime inventory、模型连接和既有 model disclosure policy；披露配置复用既有 typed policy mutation。模型连接使用一个独立且窄的 DesktopHost IPC：renderer 只能读写 provider/model/base URL 与一次性的 API-key 输入，main process 以短生命周期 stdin 把 key 交给 sidecar helper，helper 只写 OS credential store（或权限受限的 credential fallback）。renderer、Protocol v2、task transcript/checkpoint、diagnostic、日志与 localStorage 都不会收到或持久化 key；回传 status 也只含 profile、provider、model、base URL 和 configured。`system`/`light`/`dark` color theme 与 `comfortable`/`compact` density 都是独立于科研状态的 renderer-local preference，保存在 localStorage；updates 或原始诊断不会以空壳入口出现。
- [x] 模型连接的可用性不等于“存在 API key”：Desktop 只在 credential 已配置且 model 是具体 provider model ID 时才启用 composer；继承设置里的 `default` 占位值会清空为待填写状态，main/sidecar helper 同样拒绝持久化它。disclosure policy 确认后的 renderer state 只采用 backend event/terminal 返回的 exact workspace revision，绝不自行 `+1`；Electron 回归覆盖“确认 policy 后立即发送并收到回复”，防止 revision conflict 在模型调用前静默阻断请求。
- [x] 实现中心 task shell、task transcript 和 composer frame。
- [x] 嵌入始终挂载的 MapLibre Spatial Workbench（F1 仅提供离线 graticule，artifact layers留给 F3）。
- [x] 实现 `ocean-artifact://` resource broker（直接路径仅允许当前 workspace state 的 `artifacts/` 子树中的常规 PNG/WebP/JPEG/JSON 文件，采用 realpath containment、5 MiB cap、fixed MIME、`nosniff` 和 no-store；受控文档另走 F4 的短期 opaque grant，不开放通用文件读取）。
- [x] macOS packaged-dir 启动 Python sidecar spike（2026-07-15：重建 frozen sidecar、Protocol v2 handshake 和 `Ocean Research Partner.app` 内 bundle 检查通过）。
- [ ] Windows packaged-dir 启动 Python sidecar spike。

F1 implementation note (2026-07-14):

- `frontend/ocean-desktop` is now a buildable Electron + React application. The main process owns directory selection and the Python stdio sidecar; preload exposes only a typed, bounded request/event bridge; the renderer runs with `contextIsolation: true`, `sandbox: true`, and `nodeIntegration: false`. Every DesktopHost `ipcMain.handle` endpoint additionally verifies that its sender is the active main Ocean `WebContents`, so a future auxiliary/hidden window cannot invoke picker, sidecar lifecycle or backend-frame channels.
- `frontend/packages/ocean-client` is now the generated Protocol v2 declaration home and owns the transport sequence tracker, snapshot-gap decision and workspace revision helper. The generator, its reproducibility test, the retained TUI, and Desktop renderer all import this one transport-independent source. `frontend/packages/ocean-ui` now owns the renderer-independent modal focus primitive, which keeps the top-most accessible dialog keyboard-contained and restores its opener's focus; it has no React runtime dependency and is exercised by the Desktop Electron accessibility path. `frontend/packages/ocean-spatial` now owns the EPSG:4326 image-overlay and bounded LinkedPlot contracts plus the shared offline graticule; Plot Studio and Desktop both parse those contracts before rendering, and Desktop now rejects malformed fields rather than rendering a partial layer. MapLibre lifecycle, selection hit-testing and view-specific inspectors remain local until their interaction contracts converge, rather than forcing current renderer components into a premature common component.
- The renderer handshakes as Protocol v2 `desktop`, opens the selected local workspace, lists/creates/opens `ResearchTask`s, submits task-bound prompts, and refreshes task revision after an agent terminal. It applies the authoritative `workspace_revision` from every typed terminal and workspace-change event before subsequent mutations, so chained imports/reviews/reports do not reuse a stale optimistic revision. It is connected to the real backend rather than a browser mock.
- The persistent MapLibre surface now parses task-restored `MapScene` refs, overlays `SpatialLayer` raster parts through the broker, projects Point/LineString/Polygon anchors, persists viewport/inspection state through `TaskMapState`, and renders bounded `LinkedPlot` JSON in its dock. Colorbars, advanced time/depth controls, section/Hovmoller renderers and full evidence navigation remain F3 work.
- `scripts/build_desktop_sidecar.py` now defines the platform-local PyInstaller `onedir` build and `frontend/ocean-desktop/scripts/{build,verify}-sidecar.mjs` make it a required `electron-builder` resource. A packaged renderer no longer falls back to system Python: it only starts `resources/sidecar/ocean-backend/...` and fails closed if it is absent. The main process passes `backend --state-dir ... --client-kind desktop` to a frozen executable while retaining `python -m ocean_partner backend ...` for a development interpreter; `verify-sidecar.mjs` now sends a real desktop Protocol v2 handshake and requires `system.ready`, instead of merely checking that the binary exists. A local macOS x64 spike built the sidecar (193 MiB), ran its frozen `ocean-backend doctor`, and verified the executable again from `Ocean Research Partner.app/Contents/Resources/sidecar`; this frozen build reports Seatbelt sandbox, NetCDF and AnalysisRun capability, while `cartopy`, `gsw` and `zarr` are currently absent and must remain unavailable rather than implied by the UI. Windows and macOS signing/notarization remain unchecked. PyInstaller is an explicit `desktop-build` extra rather than an untracked developer-machine dependency.
- The package verifier now has two explicit target expectations: macOS must prove Seatbelt through `doctor`, `sandbox-self-check`, and a Protocol v2 handshake; Windows must prove the frozen executable is contained in `win-unpacked/resources`, emits the same handshake, and reports no sandbox backend or AnalysisRun capability. A `windows-latest` CI job now builds that package using the target platform's Python and Electron builder. The F1 Windows spike remains unchecked until its first target-run artifact is reviewed; this job intentionally does not claim the unimplemented Windows trusted-execution broker is safe.
- Target package CI now uploads compact, path-free `package-verification.json` evidence beside the generated checksum/SBOM/license metadata. The verifier reports target platform/architecture, the required backend schema, and only the doctor sandbox/self-check contract after it has completed the frozen sidecar handshake. This makes the first Windows packaged-dir run reviewable without exposing a build workspace or pretending that its fail-closed broker permits AnalysisRun execution.
- Package verification additionally reads the final Electron executable and frozen sidecar as Mach-O/PE binaries and refuses a package unless both agree with the native Node target architecture. This closes the gap where a CI bundle could have executed a valid sidecar but carried the wrong Electron or Python ABI for its declared target.
- Package CI also launches the target packaged Electron executable after clearing inherited Python/Node development paths, starts its frozen sidecar through the typed bridge, and creates one task in a fresh workspace. The path-free `packaged-launch.json` records only platform/architecture, the expected backend schema and those two successful state transitions. It materially strengthens the no-preinstalled-runtime packaging evidence, while the clean-machine checkbox remains open until macOS and Windows target runs are independently reviewed.
- The native `package:release:mac` and `package:release:win` commands now run the same packaged-app launch smoke after their post-package sidecar verifier. A release archive cannot pass its local build command merely by containing a directly executable sidecar; the Electron main/preload/renderer route must also start that sidecar and create a task under a stripped development-runtime environment.
- Current local verification: `npm --prefix frontend/ocean-desktop run check`, `npm --prefix frontend/ocean-desktop run build`, `npm --prefix frontend/ocean-desktop run verify:sidecar`, `npm --prefix frontend/ocean-desktop run package:dir`, a macOS Electron launch smoke, and Python/Node syntax checks for the sidecar build path. The sidecar verifier permits a bounded 45-second scientific-runtime cold start, requires a Protocol v2 desktop `system.ready`, and the generated macOS `.app` sidecar has separately passed the same handshake. The package now carries project-owned Ocean Partner ICNS and ICO assets instead of Electron's default app icon; both derive from the checked-in PNG/SVG master. `npm start` uses a small cross-platform launcher that removes an inherited `ELECTRON_RUN_AS_NODE` automation flag before spawning Electron, so an IDE/CI shell cannot silently run the desktop main process under Node semantics. The old unpacked Electron-only shell is intentionally no longer a clean-machine success criterion because `package:dir` now requires a sidecar.
- Electron E2E now sets one explicit development-only `OCEAN_DESKTOP_TEST_ISOLATED_PROFILE=1` flag. Before it requests Electron's single-instance lock, the unpackaged main process creates a unique `ocean-desktop-e2e-*` temporary `userData` directory; it is synchronously removed on normal app quit. This prevents a failed test from sharing a developer's local storage or causing the next test to attach to its stale production-profile instance. The security suite launches two simultaneous isolated instances, proves their profile paths differ, and proves both profiles disappear after quit. Packaged builds ignore the flag entirely, so production remains single-instance per normal user profile.
- `verify:package-launch` independently starts each packaged macOS/Windows candidate with a unique command-line `--user-data-dir` profile, verifies Electron reports that exact isolated directory before it opens a workspace, and removes it after shutdown. It clears the E2E profile flag together with Python/Node development paths, records `isolated_user_data_profile: true` in its path-free launch evidence, and therefore cannot accidentally attach to a locally installed Ocean app while claiming a first-launch smoke result.
- The Settings modal now owns renderer-local Appearance controls: `System`/`Light`/`Dark` color theme and `Comfortable`/`Compact` density. The selected modes are persisted only under the namespaced local-storage keys `ocean-desktop:appearance-theme/v1` and `ocean-desktop:display-density/v1`; they change bounded task/transcript/composer spacing, non-data map background, and chart grid/readout surfaces through root classes, but never write a task, map state, artifact, request, provider policy, checkpoint, or scientific palette. System mode listens for the OS color-scheme change without recreating MapLibre. The Electron accessibility contract proves initial dialog focus, pressed-state semantics, keyboard reachability, compact-class activation, theme persistence, System-mode adaptation, and continuous map canvas identity; reviewed light and dark visual baselines cover the resulting workbench. Current local evidence is `npm run check`, 87 Vitest cases, and all 19 Electron scenarios; it does not replace the open target-platform accessibility review.

F3 implementation note (2026-07-14):

- Desktop requests each `MapScene`, `SpatialLayer` and `LinkedPlot` by exact artifact ref through Protocol v2. It never resolves an artifact by display title.
- A `MapScene` Point, LineString or Polygon is a clickable renderer projection. Its selection and plot refs are persisted to `TaskMapState`; a linked plot whose `selection_ref` differs from the anchor is rejected instead of being silently displayed.
- The linked-visualization dock also provides an explicit `Use in task` action. It pins only the selected feature's immutable `selection_ref` and its active exact `linked_plot` ref into removable composer chips; clearing a hover or switching the dock does not implicitly change agent context.
- SpatialLayer Evidence and LinkedPlot Evidence now open the shared exact-ref Artifact Inspector even when the map object is not a task-output relation. This reuses the constrained `artifact.get`, `artifact.versions.get` and `analysis.run.get` route: a researcher can follow the map object to immutable files, source links, run, checks, verified code and reviews without turning a visual inspection into an implicit task mutation.
- Run Evidence exposes every pinned `RunInput.artifact_ref` as a compact exact-ref link. Selecting one opens the same Artifact Inspector, completing the practical Map/Plot -> Artifact -> Run -> pinned Dataset path without disclosing the sandbox source path or treating a display label as identity.
- The current dock is a bounded uPlot renderer for aligned time-series/profile payloads plus true two-axis scatter/T-S payloads, and a bounded canvas matrix renderer for section/Hovmoller payloads. Matrix plots now label their horizontal/vertical axis units and endpoint values, and orient the vertical rows from the immutable axis ordering plus `metadata.depth_positive`: depth-positive-down sections place shallow values at the top even when an upstream axis is descending, while non-depth vertical coordinates retain normal Cartesian orientation. Layer visibility, opacity and order are task-local controls: moving a layer updates one persisted `layer_controls` projection which is consumed by the MapLibre raster stack, layer panel and colorbar ordering. The layer panel exposes variable/units, scalar range, time/depth selection, exact ref, run/attempt provenance and check/review counts. Visible `SpatialLayer`s now carry a canvas colorbar legend derived from their immutable colorbar metadata, including units, levels, palette name and transparent nodata declaration; it is not yet an independently verified PNG color calibration. Pointer entry to a linked plot transiently highlights its bound MapScene Point/LineString/Polygon anchor through MapLibre paint properties; it neither persists a selection nor changes agent context, and intentionally does not invent a false per-sample geographic mapping. Conversation and spatial focus modes only resize/hide sibling panes; at `<=1199px` an explicit Task/Map segmented control selects the visible pane while keeping the MapLibre instance mounted and listening for container resize. Task output inspection follows `analysis_run_id` through typed `analysis.run.get` to bounded run state, selected attempt, checks and immutable code snapshots. Independent axis-tick calibration, dataset/source navigation and further plot interaction remain explicitly unfinished.

- A selected MapScene anchor now preloads bounded metadata for every declared exact `plot_ref` and exposes them as a compact mode selector in the linked dock. The selector displays a scientific kind (`Time series`, `T-S`, `Profile`, `Section`, `Hovmoller`, or `Scatter`) rather than an artifact ID; a click validates and renders the chosen immutable `LinkedPlot`, persists only the same `selection_ref` plus the chosen `active_plot_ref` to `TaskMapState`, and leaves MapLibre mounted. Mismatched selection versions are removed fail-closed. The Electron fixture attaches both a time series and a T-S diagram to the same station; the spatial E2E switches `Time series -> T-S -> Time series`, asserts the T-S canvas and pressed selector state, then continues through the existing transect Evidence -> Run -> Dataset path.

- The offline SpatialLayer vertical slice now also follows the production execution-consent contract: after a local NetCDF is imported and the agent has written its analysis/verification code, the test waits for the durable `analysis_run_execute` permission interaction and answers it through typed `interaction.respond`. Only then may the sandbox execute, checks pass, and immutable SpatialLayer/Selection/MapScene artifacts publish. This keeps the coding walkthrough covered without weakening the user-confirmation boundary.

- Electron E2E now seeds the production Artifact Store with a checked SST `SpatialLayer`, Point time-series and Transect section linked to the same selected `AnalysisRun` and Dataset. The desktop test verifies the persisted viewport and feature registration, constrained PNG/JSON reads through `ocean-artifact:`, layer variable/units/time/depth controls and colorbar/nodata legend, then clicks the Point and Transect through MapLibre. It confirms that the Point keeps the map visible while rendering its time series; that the Transect renders a section with west-to-east distance and depth-positive-down axis labels; and that its Evidence path reaches immutable plot ref, run checks, verified code and the exact pinned Dataset input. The artifact protocol handler now uses checked `readFileSync` responses rather than a nested `net.fetch(file:)`, and registers only its read-only artifact scheme for CORS-enabled Fetch; it still permits only contained fixed MIME resources under the active workspace state directory.

验收：

- [ ] 干净机器不安装 Python/Node即可打开真实 workspace。
- [x] renderer 无 Node/fs/process 能力，伪造 IPC/navigation 被拒绝：Electron E2E 断言 renderer 无 `process`/`require` 且只暴露 typed bridge；外部 navigation 保持在本地页面；加载同一 preload 的隐藏次级窗口也会被 main-process active-window sender 校验拒绝。`startBackend` 运行时只接受 `workspacePath`，拒绝 renderer 注入的 `pythonExecutable`；开发解释器仅可由 main-process 启动环境的 `OCEAN_PYTHON` 决定，packaged app 始终使用 bundle 内 frozen sidecar。
- [x] 打开 task 能恢复 title、transcript summary、active MapScene 和 viewport：Electron E2E 先用真实 `OceanBackendHost` fixture 写入 task、两条 renderer-safe transcript、精确 `MapScene` ref 和 `{center: [130, -5], zoom: 5}`；桌面 sidecar 重连同一 workspace 后，测试确认 `task.open` 实际收到 `task.snapshot`，并恢复标题、两条文本与原始 viewport。
- [x] 打开task后第一条fake-model响应证明实际加载了stable ConversationCheckpoint，而不只是恢复界面文本：Electron E2E 用仅限未打包、显式 main-process test environment 的 fake sidecar 先提交并提交 durable `ALPHA_MEMORY` checkpoint；桌面重启、reload、打开任务后发送一个不含 marker 的续问，fake model 只有在 `QueryEngine.load_messages()` 载入 checkpoint 时才会返回确认文本。renderer 不可配置该 test sidecar，packaged app 忽略此 hook。
- [x] renderer reload 不取消 backend request，也不丢失 task map state：Electron E2E 在 fake sidecar 的延迟模型调用处于 active 状态时 reload renderer，随后确认同一 in-flight request 的 terminal assistant 文本仍抵达，且 fixture 的 `MapScene` 和 `{center: [132, -8], zoom: 4}` viewport 均由 task snapshot 恢复。

### Phase F2：Agent Task Experience

目标：达到 Codex 式可持续任务交互，而不是聊天 demo。

- [x] streaming transcript、tool activity、compact markers 和 request terminal states。
- [x] composer、exact-ref chips、file grants 和 map-selection context。
- [x] task create/rename/archive/reopen、project grouping 和 history search。
- [x] typed permission/question/disclosure/fetch/review interactions。
- [x] request/run cancel、backend crash recovery 和 sequence resync。
- [x] task output shelf 和 Artifact Inspector。
- [x] command palette只映射 typed actions。

F2 implementation note (2026-07-14):

- The Desktop renderer already streams task-bound assistant deltas, offers request cancellation, and exposes task create/open/rename/archive/reopen with archived-task filtering and local task-history filtering. While a foreground request is active, selecting another task opens an explicit Keep Current / Cancel and Switch decision; the latter records the target only in ephemeral renderer state and sends `task.open` only after the original request emits its authoritative terminal event, so task work is never silently switched or run in parallel. A restored `task.snapshot.active_request_id` is reconciled through typed `request.status.get`, so renderer reload does not rely on stale page state to decide whether cancel is available. On a model `permission_denied` disclosure failure, Desktop opens a typed policy dialog for the backend-provided configured provider and persists explicit per-category decisions through `disclosure.policy.set`; conservative metadata/aggregate-only defaults are never silently broadened. `ask_user_question` is now an explicit Ocean tool: the router writes a session/principal-bound pending interaction before emitting `interaction.requested`; Desktop renders the question only from that typed event or a restored task snapshot, and `interaction.respond` atomically changes its state and commits its request terminal before releasing the agent loop. Pending interactions disappear from task snapshots when the foreground request is cancelled or recovered as interrupted after backend restart. The same durable interaction path now gates only `analysis_run_execute`: `OceanExecutionPermissionChecker` allows regular scoped work-file/artifact operations but requires an Allow/Deny decision immediately before the bounded sandbox starts. Tool calls and compaction progress appear as bounded, collapsible task activity; successful agent tool publications and task-scoped `artifact.create` mutations now create exact task output links. The output shelf opens an exact-ref Artifact Inspector with bounded metadata, provenance, checks/reviews and scalar fields, and may submit an explicit approved/changes-requested/rejected review through `review.submit`. A selected MapScene feature can now be explicitly pinned through `Use in task`: composer chips expose exact Selection and, when present, LinkedPlot refs; `session.submit.context_refs` carries at most eight unique refs, the router verifies each belongs to the current workspace, and it persists a controlled exact-ref footer in the user turn/checkpoint. A missing or cross-workspace ref fails before an agent runtime or task request is started. `Cmd/Ctrl+K` exposes only existing typed actions (project/task/map actions), never an Electron-only mutation path. The renderer now owns a transport-local sequence tracker: `system.ready` starts a new connection generation, duplicate or malformed frames are ignored, and a sequence gap suppresses the incremental event and requests authoritative `workspace.snapshot.get` plus `task.snapshot.get`; the task snapshot in turn reconciles any active request through `request.status.get`. A sidecar exit clears stale renderer handlers/streaming state, while the next ready event reopens the previously active task from its durable checkpoint. The pure tracker is covered for contiguous, duplicate/malformed and gap frames. File grants beyond explicit local imports and full code/source/run navigation remain open.

- Run Evidence now also exposes the run-owned immutable `analysis_plan_ref` as an exact-ref button alongside run state, inputs, checks and verified code snapshots. It opens the existing Artifact Inspector rather than duplicating plan content or trusting a display title; `analysis.run.get` router coverage locks the reference into the renderer-safe result. The desktop E2E fixture executes a deterministic `RunService` attempt, persists a task output with that run provenance, opens it through the output shelf, and asserts the rendered run/check/code/input sections plus navigation to the exact immutable plan ref.

- Task rows now retain the existing Protocol v2 `active_request_id` and `updated_at` rather than flattening every task to `active`: the sidebar renders only backend-authoritative `Working`/lifecycle state, an accessible exact update timestamp and a stable relative recency label. It deliberately does not invent `attention` or `failed` task health before the backend exposes a durable summary contract for those states.

验收：

- [x] 文献/思路任务不创建代码也可完整结束：task-scoped fake-model 回归验证零 tool call、零 AnalysisRun、completed terminal 和可恢复 checkpoint。
- [x] 计算任务显示 plan、code、run、checks 和 artifact refs：Electron E2E 用真实持久化 `RunService` fixture 创建 checks-passed attempt、task output、pinned input/plan refs 与三份 verified code snapshots；Desktop 从 Output shelf 打开 artifact 后渲染 Run Evidence，并可点击精确 `analysis_plan_ref` 进入该计划的 Artifact Inspector。
- [x] 重启 app 后任务顺序、terminal state、pinned refs 和 map scene一致：Electron E2E 验证两个 task 经 sidecar restart 与 renderer reload 后仍按最近更新顺序列出；持久化 checkpoint fixture 恢复 task 后保留已完成 assistant terminal transcript、无 active-cancel control、researcher-selected `map_scene_checkpoint_fixture@v1` context footer，以及精确 MapScene/viewport。另一个真实-host fixture 覆盖 title、双向 transcript 和 MapScene snapshot 的恢复。
- [x] 重启app/backend后agent继续task时保留compacted memory、tool-result context和conversation generation：Electron E2E 重启到 fixture workspace 后只从 `task.snapshot` 显示 `Generation 2`，不暴露 checkpoint 内部的 `COMPACTED_MEMORY`；随后 fake model 只有同时收到 canonical synthetic memory 与结构化 `ToolResultBlock` 中的 `figure_checkpoint@v1` receipt 时才返回确认。fixture 还验证该 checkpoint 的 `compaction_generation=7` 被持久化；现有 compaction unit tests 覆盖真实 summary/microcompact 的生成与 durable mutation receipt 保留。
- [x] Cancel 清理 agent stream与 active process tree，并只产生一个 terminal event：Desktop Electron E2E 验证取消延迟 agent response 后只收到一个 target `request.cancelled`，active control 清除且原响应不会泄露；router/store tests 验证 target/control 的唯一 durable terminal pair；真实 Seatbelt sandbox test 让 sandboxed Python 写入 PID 后取消其 coroutine，并确认进程组终止后该 PID 不再存活。

Cancel evidence note (2026-07-15):

- Desktop Electron E2E starts the fixture's delayed agent response, clicks the typed cancel control, asserts exactly one backend `request.cancelled`, confirms the active control clears, and waits past the original delay to prove no assistant response leaks through. Router/store tests separately cover the durable target/control terminal pair. The sandbox execution test writes a sandboxed process PID, cancels its coroutine, then verifies process-group termination by observing that PID disappear. Packaged-app quit and Windows-specific process-tree behavior remain F5/F6 release work.

### Phase F3：Persistent Spatial Workbench

目标：完成本产品区别于通用 agent app 的空间交互核心。

- [x] 迁移 MapLibre SpatialLayer rendering、registration 和 layer controls。
- [x] 实现 task-bound persistent MapScene/TaskMapState。
- [x] Point/Station marker -> time series/profile/T-S/scatter Dock。
- [x] Transect -> section/Hovmoller/transport Dock。
- [x] Region -> aggregate plot Dock。
- [x] map/plot linked hover、selection、version picker 和 “Use in task”。
- [x] Layer/Plot Evidence Inspector：source、run、checks、code、review。
- [x] focus modes与 compact layout；任何切换不销毁场景状态。
- [x] 移除 Desktop 核心路径中的外部 Plot Studio browser open。

验收：

- [x] SpatialLayer 直接叠加地图，colorbar/units/time/depth/nodata 完整：Electron E2E 读取受限 PNG resource，确认 MapLibre canvas、layer controls、`sst_anomaly · degC · 2010-07-01 · 0` 和 `viridis · nodata transparent` legend。
- [x] 点击 Point 打开 time series/T-S 等，地图保持可见：Electron E2E 点击 MapLibre Point 后确认 time-series Dock canvas 与同一 map canvas 同时存在。
- [x] 点击 Transect 打开 section/Hovmoller，方向与距离轴正确：Electron E2E 点击中心外的 LineString，确认 section matrix 的 `distance [km]` 和 depth-positive-down `depth [m]` 端点；已有 browser fixture 同时覆盖 Hovmoller matrix。
- [x] anchor、plot、code、run、checks 和 dataset 能沿 exact refs双向追踪：Electron E2E 从 Transect Dock Evidence 打开 `linked_plot_checkpoint_section@v1`，读取真实 Run Evidence 的 check/code snapshot，再通过其 exact RunInput 到 `dataset_checkpoint_fixture@v1`。
- [x] checkerboard、north/south、dateline、50k points 和 5 MiB payload fixtures通过：macOS Chrome/Plot Studio visual smoke 以真实 capability URL 验证 checkerboard 的 north/south 像素方位、antimeridian 两个 raster part 的左右顺序，以及 50,000 点、`5 MiB - 2 KiB` LinkedPlot JSON 在 desktop/mobile 都能切换、解析并绘制；同一 transect 的 section 与 Hovmoller exact refs 也分别完成 matrix render。
- [x] 选中 Point/Transect/Region 后，composer 在尚未 pin 该 exact Selection 时提供一个紧凑的 “Use [anchor]” 动作；它复用既有 selection/LinkedPlot exact-ref pinning，成功后只显示可移除的 immutable context chips，不创建 renderer-local 科学状态。Electron accessibility coverage proves the action is named, moves the exact station context into the composer, and then disappears to avoid duplicate intent.

### Phase F4：Artifacts、Runs 与 Research Workflows

目标：把 Open Science 式真实产物与可复现性完整接入 task UI。

F4 implementation note (2026-07-15):

- Desktop now exposes explicit local NetCDF and PDF pickers through the DesktopHost bridge. The host returns only a current-workspace relative path after user selection; the renderer sends the existing `dataset.import` / `paper.import` mutations with immutable-snapshot acknowledgement and never receives a general filesystem capability. Dataset/PDF imports made from an active task are linked as exact task outputs. The sidebar lists and locally filters bounded Dataset/Paper summaries by title, type or artifact ID, then opens them through the same exact-ref Artifact Inspector. The inspector fetches `artifact.versions.get`, immutable file manifest metadata and incoming/outgoing evidence links; it lets the researcher inspect historical or linked immutable versions by exact ref rather than silently substituting a current version. PDF intake collects bibliographic metadata but does not extract or disclose document text. A command-palette Figure Request dialog persists user goal, question, requested formats, selected Dataset refs and optional task evidence through `figure.request.create`; the user-owned intent artifact is linked back to the active task before any model/code execution begins. An existing AnalysisRun can be explicitly rerun from its artifact evidence through `analysis.attempt.start`, and its run evidence now exposes state-aware `Cancel attempt` and `Abandon run` controls through the corresponding typed protocol requests; terminal actions refresh the persisted run/check/code projection. The command palette now opens a task-scoped Report builder: it selects immutable non-report output refs, requests `report.preview` to show conclusion eligibility/limitations, and creates `report.create` only from the selected exact refs. The router links the resulting report version back to the originating task; a protocol test proves that both the source observation and report appear in `task.output.list`.

- The first bounded document-viewer slice is now present. `artifact.resource.grant` accepts an exact immutable artifact ref, a manifest-declared safe file name and a narrow purpose: only a local `paper` PDF up to 25 MiB (`paper_viewer`) or a generated `report.md` up to 2 MiB (`report_viewer`). The Python router issues a fresh `res_...` token and precise manifest metadata; Electron main resolves the URI under the workspace artifact root, validates realpath/file size and manifest SHA-256, stores that immutable identity with the token for ten minutes, strips the filesystem URI before forwarding the completion event, and clears all grants on workspace/task switch, sidecar stop or primary-window close. Each grant records the originating Ocean `WebContents` ID; the app uses a non-persistent dedicated Electron session whose `ocean-artifact://resource/*` request gate rejects every other renderer, including a hidden window deliberately created in that same session. The renderer can only request the two approved viewer purposes, renders Markdown as inert plain text and embeds PDF in a sandboxed, no-referrer iframe. Direct artifact URLs still reject PDF/Markdown, so this does not create a general file API. Manifest-declared PNG/JPEG/WebP artifacts additionally have a local raster viewer through the pre-existing `ocean-artifact://` broker; it remains constrained by realpath containment, regular-file checks, fixed MIME and the 5 MiB cap. SVG remains excluded and a forged in-store SVG receives `415`. Router tests cover report Markdown and paper PDF grants plus cross-purpose rejection; the Desktop E2E test proves the owning renderer can read a report resource while a same-session auxiliary `WebContents` cannot, and that post-grant content tampering produces `409`. The immutable code snapshots already render in bounded inspector disclosures; richer provenance graph coverage remains open.

- Run Evidence now exposes the safe fields already returned by `analysis.run.get`: selected attempt state/trust, duration, exit code, resource-limit trigger, bounded failure reason, declared execution limits, checks and verified immutable code snapshots. Raw stdout/stderr remain deliberately absent: their only backend path is a separately policy-gated, redacted and audited diagnostic-excerpt service.

- `analysis.diagnostic_excerpt.get` now makes that diagnostic service explicit in Protocol v2. It accepts only one exact run/attempt, one of four fixed stream names and a 1--8192 byte bound. The router first verifies workspace ownership, resolves the current configured provider for the policy lookup, and returns either a withheld disposition/reason or a redacted bounded text excerpt plus audit ID; it never adds log content to `analysis.run.get`, checkpoints or artifact metadata. The Run Inspector requests it only after a researcher clicks a named stream, displays the returned disposition/audit ID, and keeps the excerpt as ephemeral renderer state. Router tests cover default denial and explicit approval.

- A separate `analysis.diagnostic_excerpt.export` mutation now creates a private `ocean-diagnostic-export/v1` directory only for a freshly re-authorized, redacted bounded excerpt. It repeats the exact run/attempt/stream/bounds request through the same provider disclosure policy and audit service; a denial completes with `exported: false` and creates no files. An allowed export contains `diagnostic-excerpt.json`, an audit ID and a checksum inventory, but never a raw log, absolute path or text in the Protocol terminal result. The Run Inspector offers export only after an allowed excerpt is visible and uses the existing typed, containment-checked `export_id` reveal bridge. Router tests assert both the no-file denial path and the allowed bundle/result boundary.

- Portable evidence export is now available through the task command palette. The researcher selects exact task-output versions; `portable.export.create` accepts at most 100 unique `ArtifactRef`s and is guarded as a workspace-write operation. The backend delegates to the existing audited `PortableExportService`, follows intrinsic immutable links, verifies manifests, rejects local paper PDFs/raw logs/path-or-secret-bearing content, and creates a private bundle without returning a filesystem path to Protocol clients. Its terminal result contains only an opaque `export_id`, included refs and fixed inclusion guarantees. The renderer can ask the typed preload bridge to reveal that identifier; Electron main validates the identifier, realpath-containment and directory type below the active workspace's `exports/` root before asking Finder/Explorer to show it. Protocol export tests assert exact-version selection, no path/URI in the result and a materialized manifest. Diagnostic excerpts use their separate, narrower export contract above.

- Artifact Inspector now renders local evidence diagnostics directly from the backend-owned `ArtifactProjection`: invalidated dependencies, unavailable sources, failed verification and rejected review are blocking states; stale dependencies, verification warnings and non-approved review are attention states. The UI does not infer alternative lifecycle semantics or override the Report builder's conclusion gate; it makes the same durable state visible at the point where a researcher inspects an artifact.

- `frontend/ocean-desktop` now provides `npm run test` via Vitest and `npm run typecheck` as the documented alias for the existing strict TypeScript check. The first pure renderer test suite covers projection-to-diagnostic translation for blocking, attention and current/approved evidence states. This is a real unit-test foundation, not a substitute for the planned Playwright Electron E2E/fake-sidecar coverage.

- Playwright Electron coverage now runs through `npm run e2e`: it builds and launches the real desktop main/preload/renderer process, verifies the initial workbench renders, confirms renderer `process`/`require` are unavailable, and asserts that only the typed `oceanDesktop` bridge is exposed. It then creates a temporary local project, starts the real local Python backend through that bridge, reloads the renderer so the normal status/handshake path opens the workspace, creates a research task, and opens the proposal-only multi-agent panel. With the default feature flag off, the status request fails closed: the panel names the feature as unavailable and keeps its start button disabled. No model provider or network is needed. Building this test uncovered two production startup defects that are now fixed: the build compiles the single authoritative `src/preload.ts` into Electron sandbox-compatible CommonJS (`preload.cjs`) with esbuild, and Vite emits relative asset paths for the app's `file://` renderer. Fake-sidecar error paths, spatial pixel fixtures and forged-IPC E2E coverage remain separate unfinished gates.

- NOAA OISST remains a bounded backend/engineering fixture, including its no-network preview, confirmation hash and immutable DatasetArtifact tests, but it is no longer a product-level intake route. In chat, the agent can use the generic source discovery/preview/confirmed-import contract to select a backend-configured adapter from the research requirement; it never exposes an OISST button or a dataset-specific command. The sidebar continues to expose only explicit local `Data` (NetCDF-family) and `Paper` (PDF) imports. Each new remote connector extends the backend source registry and its provenance/permission contract rather than adding another dataset-specific sidebar button.

- A researcher can now record a falsifiable HypothesisArtifact from the task command palette, instead of leaving an intended mechanism only in chat. The form requires a title, statement, mechanism, one prediction with an explicit test context and one falsification criterion; competing explanations are optional. It offers only existing immutable workspace/task artifacts as optional evidence refs and sends the matching intrinsic `motivates` links with `artifact.create`. The generic artifact mutation links the created exact version back to the current task. A hypothesis shown in the Artifact Inspector has a separate explicit `Make active hypothesis` action backed by `hypothesis.activate`; creating a hypothesis never activates it implicitly. This is the Claim/Hypothesis portion of the workflow only: structured Claim authoring, Experiment authoring, result attachment and their report navigation remain unfinished.

- The Claim portion is now available too, with a deliberately narrow first form: an active task and at least one imported/registered PaperArtifact are required; the researcher records one statement, claim kind, exact paper version, locator kind, locator text, relationship and optional limitations. Submission creates a typed ClaimArtifact with the matching `derived_from` intrinsic link and task output link. It cannot substitute paper text, invent a citation target, or create an ungrounded claim. The command palette also exposes `paper.register`: it stores user-entered bibliographic metadata only, never document bytes/text, and now links that exact PaperArtifact to the active task just like a local PDF import. Router coverage proves the metadata-only paper appears in `task.output.list`. Multiple-locator editing, Experiment authoring/result attachment and a complete Literature -> Claim -> Hypothesis -> Experiment -> Result -> Report navigator remain unfinished.

- Researchers can now also create a pre-execution ExperimentArtifact in a task. The form requires an existing exact HypothesisArtifact, one or more exact DatasetArtifacts, scientific question, baseline, metric ID/definition/comparison, success criterion, failure criterion and stopping rule. It always writes `outcome: proposed`, an empty result set, and the required `tests_hypothesis` / `uses_dataset` intrinsic links; it exposes no control that could declare a result or success before checked result evidence exists. Newly committed artifact events are now upserted into the renderer's bounded workspace index, so a newly created hypothesis/dataset becomes immediately selectable in subsequent forms without reopening the project. Backend coverage now accepts the exact user-facing proposed-experiment payload and rejects the same payload without required links. Result attachment, outcome transitions and a workflow navigator remain unfinished.

- Result attachment and outcome transition now have a constrained first UI slice. An inspected `proposed`/`ready` ExperimentArtifact can open an outcome editor, but it must select one or more existing exact Figure/SpatialLayer/LinkedPlot/Observation artifacts and write an outcome summary. The desktop preserves the original experiment contract, original intrinsic links and title, appends `derived_from` links for result refs, and creates a new immutable ExperimentArtifact version through `supersedes_version`; it never mutates the original experiment. The only selectable outcomes are `completed`, `inconclusive`, and `failed`, all of which require result evidence under the backend schema. Router coverage proves a user proposal can become a v2 completed experiment only with the required hypothesis/dataset/result links. Cross-artifact result provenance navigation is available through the existing inspector links; richer workflow graph/navigation remains unfinished.

- Multi-agent is now a feature-flagged desktop panel rather than an implicit second writer. Opening it calls `multi_agent.status.get`; when the backend flag is off the returned typed failure is visible and no worker can start. When enabled and a matching confirmed disclosure policy exists, the researcher selects up to four bounded worker roles, supplies a prompt, and starts proposal-only workers. The panel polls durable task/proposal status, can cancel queued/running workers, and lets the researcher explicitly accept or reject pending proposals. Acceptance goes through the existing single-writer `multi_agent.proposal.accept` request with the current workspace revision; rejection sends a typed reason. The renderer never receives worker filesystem/model capabilities, and no proposal appears as a workspace artifact until acceptance. Existing multi-agent tests cover flag behavior, non-mutation before acceptance, cancellation/failure isolation, stale acceptance rejection and proposal validation.

- The desktop checkpoint E2E fixture now creates a report through the production `report.create` request with the active `task_id`, after a checks-passed observation has been linked to that same task. The test opens the report from the task Output shelf, follows its exact immutable Evidence link back to the observation, and reloads its Run Evidence. This proves the visible task -> report -> checked output -> run/code/input path; it deliberately does not yet claim the broader task/request/environment walkthrough acceptance below.

- Run Evidence now also shows only the selected attempt's immutable `environment_sha256`, never its environment URI or JSON contents. Router and Electron E2E coverage confirm the 64-character fingerprint survives the run-evidence contract and is displayed alongside the same checked code/input record used by the task report path.

- [x] Dataset/Paper import、project source search 和 bounded previews。
- [x] run attempts、resource usage、redacted logs、checks 和 rerun flow。
- [x] Figure/PDF/Report/Code viewers和 provenance navigation（受控 PDF/Markdown/raster viewer、per-WebContents artifact grant binding、SVG broker rejection 与 verified code snapshot viewer 已完成；更丰富 provenance graph 仍属强化项）。
- [x] Literature -> Claim -> Hypothesis -> Experiment -> Result -> Report workflow。
- [x] additional stale/invalidated/source-unavailable/unreviewed diagnostics beyond Artifact Inspector。
- [x] diagnostics export（仅限重新授权、脱敏且有审计记录的 bounded excerpt；原始日志仍不导出）。
- [x] Multi-agent保持 feature-flagged、proposal-only、single-writer。

验收：

- [x] 一个 task 可在讨论、论文、数据、coding 和写作间往返，不丢 evidence chain：Electron E2E 在同一 task 中完成一轮讨论并等待新的 durable checkpoint，注册论文 metadata、用 exact locator 创建 claim、记录可证伪 hypothesis、以 pinned Dataset 提出预执行 experiment，并从同一任务输出创建 report。report preview 明确包含既有 checked SST observation；该 observation 在独立 E2E 中已回溯到同一 AnalysisRun 的 verified code、pinned input 和 immutable environment fingerprint。整个默认路径使用 fake model 与 frozen local fixture，无需模型 API 或网络。
- [x] artifact 能回到 task、request、run、code、input 和 environment：Electron E2E 从 task Output shelf 打开 `report_checkpoint_sst@v1`，显示其 task-link `req_checkpoint_report`，沿 exact Evidence link 回到 checked observation，再打开同一 `AnalysisRun` 的 verified code snapshots、pinned Dataset input 与 64-character immutable environment fingerprint。environment URI/content 与 logs 不进入 renderer。
- [x] report gate 对 stale、failed checks 或缺失 source fail closed：stale evidence 必须有精确用户 decision；source unavailable 或 verification fail 不会被该 decision 掩盖。系统仍可保存明确标记为非 conclusion-ready 的 report draft，但不能把它作为可导出的当前科学结论。
- [x] multi-agent worker 不因 Desktop IPC获得额外 mutation/filesystem权限：Electron E2E 断言 renderer 只持有 typed `oceanDesktop` bridge、没有 Node/process/require 或 worker capability；worker-runtime tests 构造独立 proposal-only registry，确认它仅能读取受限研究状态并提交 proposal，不能获得 AnalysisRun create/execute、work-file write/edit、artifact publish/create、shell 或 web tools。multi-agent integration tests 进一步确认 proposal 在显式 accept 前不改变 canonical workspace，取消/失败同样不产生 mutation。

### Phase F5：Cross-platform Trusted Execution

目标：macOS 和 Windows 都能安全运行并发布 agent-authored code。

- [x] macOS packaged App 重新认证 frozen sidecar：`doctor` 断言 Seatbelt hard/postflight contracts，随后执行实际 `sandbox-self-check`（声明输出可写、未挂载私有文件不可读），再完成 Protocol v2 handshake；任一项不可用时保持 fail closed。
- [ ] macOS 签名/notarization 后重新认证并记录 `sandbox-exec` 替代策略。
- [ ] 完成 Windows sandbox ADR与 adversarial implementation：设计合同见 [ADR 0017](docs/adr/0017-windows-trusted-execution-broker.md)；native broker 的受控启动链已实现，但 final signed sidecar、Windows 11 adversarial fixtures 与目标机证据仍未完成，因此 Windows 保持 fail closed。
- [ ] 两个平台冻结 scientific runtime fingerprint和 dependency baseline。
- [ ] 覆盖 file/network/process/resource/symlink/junction/ADS escape tests。
- [ ] cancel、timeout、crash和app quit清理完整 process tree。

F5 implementation note (2026-07-15):

- Desktop artifact URI and opaque resource-grant resolution now share a cross-platform containment guard before any filesystem operation. It rejects parent traversal, POSIX/UNC/drive aliases, Windows ADS (`:`), DOS device names, trailing dot/space aliases, control characters and encoded separator escapes; realpath containment and immutable grant hash/size checks remain the second line. Unit coverage runs those Windows-shaped paths on every host, while Electron fetches prove ADS/device/encoded-path attempts receive a `403` from the real `ocean-artifact://` broker. This advances the portable resource/ADS portion of F5 but does not replace the pending Windows-native junction/ADS adversarial fixture evidence.

- The Python backend now applies the same cross-platform segment policy before it resolves a `dataset.import` or `paper.import` path, creates an immutable artifact file, or resolves an internal `ocean://` URI. Direct Protocol clients therefore cannot bypass the Desktop picker to request an ADS, DOS device alias, control-character path, trailing-dot/space alias, backslash path, duplicate URI separator, or traversal-shaped component. Storage/artifact/import regression coverage exercises both Dataset and PDF paths on every host; target-native Windows junction/ADS execution evidence remains an open F5 gate.

- AnalysisRun now captures a deterministic private `ocean-scientific-runtime/v1` manifest when a run is created. It records the resolved interpreter, platform, required modules, every visible Python distribution/version, redacted direct-url/editable metadata, and available HDF5/NetCDF/PROJ/GEOS native versions. The baseline manifest is immutable under the run root and its SHA-256 is stored in the durable runtime profile.
- Before every attempt, the backend captures a fresh manifest. It records the actual manifest URI and both actual/baseline fingerprints in the private attempt evidence; a mismatch creates a durable rejected attempt before the analysis or verifier subprocess begins. `analysis.run.get` exposes only the hashes, state, checks, code snapshot and artifact references, never the runtime/environment URI, local path, request snapshot, source URI or logs. Backend regression coverage proves both the stable and drifted paths; Desktop Run Evidence displays the runtime hash without receiving its manifest.
- A frozen desktop sidecar now emits a package-level `ocean-frozen-scientific-runtime/v1` manifest after PyInstaller has produced the final executable. It omits interpreter/install paths and OS patch release, but fixes the Python implementation/version, target system/machine, required scientific modules, distributions and native-library versions. The builder writes it atomically beside the sidecar; the Node verifier independently re-invokes the exact frozen executable, requires canonical-JSON/SHA-256 equality, and accepts only its schema, fingerprint and dependency count in `doctor`, Protocol capability and package attestation. Python and Node tests use the same fixed canonical hash and reject unknown distribution metadata, so neither side can broaden the private manifest contract silently. Package-only frozen-sidecar probes receive a 120-second cold-start budget because they load the full scientific stack; timeout remains fail closed and does not change interactive request latency targets.
- This package baseline is locally verified for the macOS target build, but the F5 checkbox remains open until a packaged Windows runtime has generated and revalidated the same baseline under its final scientific environment.
- The macOS adversarial suite now executes a declared-input symlink that targets an unmounted secret and proves the child cannot read through it. It also attempts output symlink and hardlink creation and an NTFS ADS-shaped filename; either Seatbelt denies the operation or the shared postflight inventory classifies the result as `unsafe_output_tree`, never a publishable success. Declared policy roots themselves now reject direct symlink/reparse-point paths before their canonical target could be mounted. The same suite continues to execute network, child-process, stdout, wall-time, memory, file-size, cancellation/process-group, output-count and output-byte checks. Junction/reparse and ADS parser cases have deterministic platform-neutral coverage, while the native Windows adversarial group remains a target-platform F5 gate.
- The target CI jobs now run those contracts explicitly rather than treating a successful Electron package build as sandbox evidence: macOS runs the real Seatbelt adversarial suite together with the packaged-sidecar self-check and AnalysisRun regressions before it builds the app; Windows runs the Python broker wire-contract and the native Rust broker contract before its fail-closed package spike. Successful target artifacts still need review, and the Windows job intentionally cannot turn a passing broker-owned AppContainer `doctor` probe into trusted execution.
- Desktop `before-quit` now prevents the first quit, awaits owned sidecar shutdown, and only then re-enters `app.quit`; an Electron E2E starts the fixture sidecar, reads its real PID and verifies that PID has exited after the app closes. On a system `resume`, the main process keeps a healthy sidecar intact, but eagerly consumes the bounded pending recovery when the owned sidecar has already exited; this preserves its active workspace and avoids a stale backend after sleep/wake. A PID-backed Electron regression kills the fixture sidecar, emits the Electron power-monitor resume event, observes the typed recovery diagnostic, and verifies a distinct replacement PID becomes ready. Existing sandbox cancellation coverage separately checks the macOS AnalysisRun process group. Windows native process-tree evidence remains required before the broader cleanup checkbox can close.
- These execution-contract changes intentionally advance the five frozen real-model manifests to point v1.51, spatial v1.42, linked v1.33, anomaly v1.31 and seasonal v1.31. Their locks now also bind the current `cli.py` and `backend/router.py` source surface, in addition to `runs/runtime_manifest.py` and `sandbox/execution.py`; the previous real-model reports are historical evidence only. No provider call is made during ordinary development or CI, so a fresh real-model run and cost attestation remain an explicit pre-release gate.
- The offline local-NetCDF vertical slice now proves the complete checked publication chain in one real `OceanBackendHost`/`RunService` request: generated `analysis.py` writes NetCDF, an EPSG:4326 raster registration and bounded scatter JSON; generated `verify.py` validates both contracts; the researcher explicitly allows `analysis_run_execute`; then the same checks-passed attempt publishes a `SpatialLayer`, a `LinkedPlot`, and a `MapScene` whose Region feature and `linked_plot_refs` contain the exact immutable plot ref. This is a frozen local fixture, not a substitute for the pre-release real-data or Windows sandbox gates.
- The independent OISST smoke was also run against the live NOAA PSL subset endpoint on 2026-07-15 without a model call. It confirmed the 2010 `105E..125E / 15N..42N` proposal, retained 12 source responses in one immutable DatasetArtifact (`2,940,617` bytes), verified all 365 days and the `0.25` degree grid, and wrote independent annual-mean NetCDF/PNG outputs. The result is an engineering/data-materialization record only; it does not close the real-model, target-platform, or researcher-walkthrough release gates.
- The Windows broker now has a real but still release-disabled constrained-launch path. `native/windows-sandbox-broker` cross-compiles for `x86_64-pc-windows-gnu`, owns strict versioned JSON request/result parsing, canonical handle-backed path validation, temporary AppContainer DACL grants with original-DACL restoration, and a per-request Job Object with `KILL_ON_JOB_CLOSE`, one active process, CPU user-time, process-memory and job-memory limits. It binds its completion port before launch, creates the AppContainer process suspended with an explicit security-capabilities attribute and inherited-standard-handle allowlist, assigns the process to that Job before `ResumeThread`, drains bounded stdout/stderr, and terminates/closes the Job before revoking ACLs when it cannot prove `ACTIVE_PROCESS_ZERO`. Windows CI now compiles a test-only feature-gated fixture and launches it in that path to exercise an allowed output write, denied ungranted read, denied loopback connection, stdout-cap termination and wall-time Job termination; the fixture is absent from the production build, and the Windows package verifier explicitly rejects `native-contract-fixture.exe` if it appears beside the broker. This must still execute successfully on a Windows runner before it counts as target evidence. `ocean_partner.sandbox.windows_broker` pins the frozen sidecar-relative executable, a release-manifest SHA-256, Authenticode, and a model-free self-check before the Python capability provider may return a Windows backend. `doctor`/`self-check` deliberately remain unavailable until target Windows AppContainer outside-read/network, timeout/cancel/crash, filesystem-escape and signed-package evidence exist; Windows `AnalysisRun` therefore remains unavailable. The target-platform sidecar build includes this release-disabled broker and the package verifier requires its fail-closed doctor contract rather than silently omitting it.
- The Python broker-result boundary now also rejects non-standard JSON numeric constants and duplicate object fields before schema handling; it requires finite non-negative duration, a signed 32-bit exit code, ASCII identifier-shaped limit triggers, an affirmative Job cleanup proof, and a coherent terminal tuple. In particular, `succeeded` requires return code `0` with no trigger, a non-success result cannot claim `0`, and timeout/resource-limit terminals must name their trigger. The request encoder separately refuses non-finite limit values and cannot serialize non-canonical JSON. This is a fail-closed wire-contract refinement, covered by 81 focused sandbox tests on 2026-07-16; it does not make the unlaunched AppContainer broker available or close the native Windows F5 gate.
- The Electron main process now applies the same bounded strict-JSON decoder to every `OHJSON:` sidecar stdout frame before it can update ready state, emit an event, or mint an artifact resource grant. A frame must be UTF-8, no larger than the Desktop protocol byte limit, a non-array object, and free of duplicate keys; malformed/oversized frames become bounded diagnostics rather than ambiguous Protocol data. This keeps package/update metadata, the native-broker boundary, and the Desktop-sidecar boundary aligned on the same duplicate-key fail-closed rule.
- The frozen-sidecar and packaged-sidecar verifiers now reuse that strict decoder for `doctor`, `scientific-runtime`, `sandbox-self-check`, frozen-runtime manifest and Protocol-ready evidence. They bound JSON/stdout to 4 MiB and retained stderr to 256 KiB, terminate a sidecar that exceeds the JSON limit, and reject duplicate-key, malformed-UTF-8 or non-object Protocol output. Release attestation can therefore not obtain a permissive last-key-wins interpretation merely because it is executed outside Electron main.
- The 2026-07-16 local regression record is green: the desktop production build, strict TypeScript check, 87 renderer/unit tests, and all 19 Electron desktop scenarios passed; the combined sandbox and AnalysisRun contract selection passed 117 tests with one intentionally skipped target-specific case. The broker crate passed its 15 platform-neutral Rust tests, and `cargo check --tests --target x86_64-pc-windows-gnu` type-checks the Windows AppContainer launch/native-contract path. Its Windows-only native-contract test module cannot execute on this macOS host, so this record is explicitly not Windows broker or Windows package evidence.

验收：

- [ ] 两个平台 sandbox adversarial fixtures全部通过。
- [x] local NetCDF -> code -> checks -> SpatialLayer/LinkedPlot -> MapScene E2E通过：`tests/test_ocean_partner/test_spatial_layer_agent_e2e.py` 使用真实 `OceanBackendHost`、`RunService`、普通 tool calls 与本地 NetCDF，验证同一 checks-passed attempt 发布精确 SpatialLayer、LinkedPlot 和 MapScene Region `plot_refs` 链。
- [x] sandbox unavailable时 publish/approve/report gate fail closed：AnalysisRun publisher 重查当前 runtime capability；review/report 对 exact immutable evidence 作有界 outgoing-link 遍历，命中任一 AnalysisRun-derived artifact 即拒绝，纯论文/非coding evidence 不被全局误伤。
- [ ] 平台 numeric reference在 frozen tolerance内；视觉 baseline按平台维护。

### Phase F6：Packaging 与 Release

目标：形成可安装、可升级、可恢复的 macOS/Windows 产品。

- [ ] signed/notarized macOS arm64/x64 package。
- [ ] Authenticode Windows 11 x64 installer。
- [x] 可审计发布元数据：bundle SHA-256、CycloneDX 风格 SBOM 与组件许可证清单（`scripts/generate_desktop_release_manifest.py`；拒绝 bundle escape symlink 和覆盖已生成元数据目录；macOS/Windows package CI 均对最终 target bundle 生成 metadata，Windows `resources/sidecar` 的冻结 Python 依赖也纳入 SBOM）。
- [x] macOS CI 构建 unsigned frozen sidecar 与 `package:dir`，并对最终 App 重跑 `doctor`、sandbox self-check 和 Protocol handshake。
- [ ] atomic updater 和 rollback。
- [x] workspace-sidecar restart retention：Electron E2E 创建两个 task 后停止/重启 sidecar 并 reload renderer，验证同一 workspace 的 task 均可恢复，且最近更新的 task 排在列表首位。
- [ ] first-run、upgrade、uninstall和sleep/wake cross-platform matrix。
- [x] 最小窗口与 200% 缩放下的紧凑任务/地图切换基线（真实 Electron E2E 验证无横向溢出、切换器不越界，且 `Task`/`Map` 均可经语义按钮操作）。
- [x] GPU/WebGL fallback：MapLibre 构造失败或 `webglcontextlost` 时停止地图 renderer、隐藏 layer/colorbar controls，并保留 Linked Visualization Dock 与 task context；Electron E2E 主动派发 context-loss 事件验证该路径。
- [x] Spatial Workbench 的 layer inspector 默认收起为地图右上角带计数的语义按钮；空间场、colorbar 和 selection readout 不再被持续遮挡。展开后仍在同一地图内提供显隐、顺序、opacity 和 evidence，并以 `aria-expanded` / `aria-controls` 保持键盘与辅助技术路径可检查；这是 renderer-local 的查看偏好，不写入 MapScene 或 TaskMapState。
- [x] 宽屏工作台可通过 Conversation/Spatial 边界的 keyboard-accessible separator 调整 Spatial Workbench 宽度；Arrow Left/Right、Home/End 和 pointer drag 均受 sidebar、最小 conversation（420 px）与 spatial（360 px）边界约束。请求宽度仅保存在 renderer-local localStorage，窄屏和 focus mode 自动隐藏控制，不影响 MapScene、viewport 或任何 artifact。
- [x] 开发期 Desktop smoke performance baseline：`benchmark:smoke` 以真实 Electron/sidecar/Protocol v2 路径记录 renderer ready、backend ready、task snapshot 与 map canvas ready；macOS Desktop CI 持续记录结果，并可由四个 `OCEAN_BENCHMARK_MAX_*_MS` 环境变量升级为回归 gate。
- [ ] GPU memory 的目标平台 profiling 与 assistive-technology audit（50,000-point LinkedPlot 与多 layer raster 的真实 Desktop 基线已覆盖）。
- [x] 更新 README、operator docs、walkthroughs和support policy（Desktop build/package/release-manifest 说明、release metadata operator guide、support boundary 与私有 walkthrough 均已落地）。
- [ ] 发布前由真实非实现者完成 researcher walkthrough；它不是日常工程开发 blocker。

F6 implementation note (2026-07-15):

- `benchmark:linked-plot` now creates a local fixture workspace with exactly 50,000 numeric time-series points, selects the published station through the real MapScene interaction, receives the bounded JSON through `ocean-artifact://`, and measures click-to-uPlot-canvas readiness. It also verifies the brokered point count/payload and nonblank canvas pixels. `OCEAN_BENCHMARK_MAX_LINKED_PLOT_READY_MS` is optional so each target platform can adopt a reviewed threshold without treating a development machine as universal. The matching Electron test passed with 50k points; the local macOS x64 benchmark measured 98 ms for 1,144,730 bytes.
- The Desktop accessibility contract now rejects visible unnamed controls, gives the persistent map and bounded LinkedPlot canvas concise semantic descriptions, and verifies command-palette focus plus Escape dismissal through Electron. All `aria-modal` dialogs now share a keyboard focus scope: opening moves focus into the topmost dialog, Tab/Shift+Tab remain inside it, Escape invokes only an enabled close/cancel/explicit dismiss action, and a normal close restores the previous control. The Electron contract covers the Command Palette's focus loop and return target. This closes a keyboard/semantic baseline only; screen-reader, high-contrast, reduced-motion, platform-scaling, and manual assistive-technology review remain explicitly open.
- The renderer now also honors system `prefers-reduced-motion` and `forced-colors` preferences: the former removes decorative transitions, while the latter uses system colors for focus, active tasks and map tool surfaces instead of relying on subtle custom color/shadow distinctions. Electron accessibility coverage emulates both preferences and checks the media queries, compressed transition duration, and high-contrast surfaces. This is an automated Chromium baseline; screen-reader and target-platform assistive-technology review remain open.
- `benchmark:spatial-rasters` and its matching Electron scenario now create four real `2048x2048` MapLibre image sources through the bounded `ocean-artifact://` broker. The fixture PNGs remain small on disk but decode to a 64 MiB RGBA texture working-set estimate; the benchmark verifies each broker response and scans a rendered map screenshot for the published raster colors. The 2026-07-15 macOS x64 informational baseline is 247 ms source-ready, 64 MiB estimated decoded raster bytes and 16.1 MB renderer JS heap used. This is deliberately not a claim about actual GPU memory, which still needs target-platform tooling and a reviewed budget.
- `capture:fixture -- --output PATH` now creates a repeatable 1440 x 920, 100%-zoom Electron screenshot of the restored checkpoint task, persistent MapLibre field, task sidebar, transcript and composer. The v1 visual pass makes the sidebar quieter, gives the active task a clear but restrained selection state, strengthens the task-reading/composer hierarchy, and keeps map controls as a compact tool layer. Map status reports actual layer and anchor counts; the graticule is above rasters but below user selections, so spatial orientation remains visible without obscuring Point/Transect evidence. The capture is reviewed visual QA, not a pixel-perfect cross-platform baseline.
- The persistent map now keeps one MapLibre instance across task/map state rerenders. Its runtime generation advances only after the base style `load` event; all task-scene spatial layers are gathered and committed to renderer state atomically, and stale style-load callbacks are removed on cleanup. This fixes the previous single-layer race where the Layers panel could list a SpatialLayer while its image source never reached the canvas. `capture:fixture` and the station/transect Electron E2E now scan the actual MapLibre canvas for the deterministic field pixels, rather than treating a resource fetch or colorbar as proof of a visible overlay.
- The 2026-07-16 visual refinement keeps the three-pane research-workbench geometry but gives task status and immutable outputs stronger scanning hierarchy, narrows the floating layer instrument panel so the spatial field remains the map's primary surface, and uses concise state copy when no linked plot is active. A fresh fixed-viewport capture was visually reviewed after the change; `npm run check`, all 19 Electron scenarios, and 50 sandbox/runtime/spatial publication tests passed. This remains local development evidence, not a signed-package or target-platform assistive-technology approval.
- The Linked Visualization Dock now begins collapsed until a map anchor is selected, then opens into a bounded working region whose default height is about 37% of a normal desktop Spatial Workbench. A keyboard-accessible horizontal separator supports pointer drag, Arrow Up/Down, Home and End; its bounds always preserve a minimum map height, and compact/scaled windows constrain the dock rather than overflowing the workbench. This is local renderer view state only: changing it neither recreates MapLibre nor mutates `TaskMapState`, `MapScene`, or any scientific artifact.
- The real station/transect Electron scenario now proves the initial collapsed state, automatic expansion from a Point click, a 72 px pointer resize, keyboard resize, continued MapLibre visibility, and the unchanged exact Evidence -> Run -> Dataset path. The full 19-scenario Electron suite and a new 1440 x 920 visual capture passed after the change.
- The sidebar project control now treats the selected workspace as a named project: it shows only the final directory name while retaining the complete path as a hover title and accessible label. The idle composer no longer describes its internal memory implementation; it reserves that compact status row for a real diagnostic or an explicit pinned map/plot context. This keeps the surface focused on research decisions instead of implementation details.
- A 1440 x 920 Electron fixture capture was reviewed after this refinement: the project label, action row and persistent map remain balanced without exposing the temporary fixture path. The desktop TypeScript check and all 19 Electron E2E scenarios pass against the same contract.
- The renderer build now isolates React, uPlot and MapLibre. The entry bundle is about 135 kB minified rather than a single 1.35 MB application asset, and `PersistentMap` imports MapLibre at runtime so the task shell can paint before the mapping engine evaluates. MapLibre remains an intentionally visible 1.03 MB vendor chunk; the Vite size warning is retained as a release signal until target-platform startup and package-size budgets decide whether a further map-engine change is warranted. A post-split local smoke record measured 531 ms renderer-ready, 2,415 ms backend-ready, 99 ms task snapshot and 33 ms map canvas readiness; it is informational, not a cross-machine threshold.
- The release-metadata generator now discovers the frozen sidecar from either target package layout: macOS `Contents/Resources/sidecar` or Windows `resources/sidecar`. It rejects a missing or ambiguous sidecar instead of producing a partial SBOM; the corresponding Python fixtures cover both layouts, and the macOS/Windows package jobs run the generator against their real `dist` bundle after sidecar/package verification. This produces auditable unsigned-build evidence only and does not relax the separate signing, trusted-execution or update gates.
- On 2026-07-16, the local macOS x64 `package:release:mac` path produced the unsigned zip and dmg, then re-verified the final bundled sidecar's Seatbelt self-check, Protocol v2 handshake and `ocean-frozen-scientific-runtime/v1` fingerprint before launching the packaged app with inherited development-runtime paths cleared and creating a task. This is reproducible unsigned local evidence only; it does not close the Developer ID/notarization, clean-machine or Windows release gates.
- `build:sidecar` now gives the PyInstaller child an explicit cross-platform `src` import root while preserving any caller-supplied `PYTHONPATH`. It therefore no longer depends on the invoking shell having already exported the repository source tree; the maintainer still selects the reviewed build environment explicitly through `OCEAN_PYTHON`. The packaged launch verifier now reads the canonical Protocol v2 `runtime_capabilities.scientific_runtime` field rather than an obsolete alias, so its frozen-runtime assertion follows the same `system.ready` contract as the renderer. A rebuilt local macOS x64 `dir` package passed sidecar verification, isolated packaged launch, task creation and the runtime-fingerprint assertion; it is unsigned local evidence only.
- The Electron suite now includes a reviewed 1440 x 920 wide-workbench screenshot baseline. It opens a fixed-name real fixture workspace through the frozen Protocol v2 sidecar, waits for the actual MapLibre field pixels, then compares the complete task/sidebar/map/dock/composer surface with a one-percent pixel-diff ceiling. It is a macOS renderer regression gate, not a substitute for the still-open Windows visual baseline or target-platform accessibility review.
- The release path now has a provider-independent, fail-closed update admission contract in `frontend/ocean-desktop/src/shared/update-manifest.ts`: an Ed25519-signed canonical manifest must name a strictly newer version, exactly match the installed Protocol/backend schema, select one exact platform/architecture package, and pass complete-byte size plus SHA-256 validation before it may be staged. The schema is now a real packaged-sidecar identity (`ocean-desktop-backend/v1`): the backend emits it in `system.ready` and `doctor`, and package verification rejects any other value. Unit coverage rejects bad or unknown signatures, downgrade/equal versions, schema incompatibility, ambiguous targets and partial/tampered bytes. This does not close the `atomic updater and rollback` checkbox: a hosted update source, key custody/rotation, final runtime identity, atomic replacement and clean-machine rollback evidence remain open; see `docs/release/desktop-update-contract.md`.
- The release path now also has a main-process update transport coordinator around `electron-updater`: after a project-signed `VerifiedUpdate` is admitted, it disables implicit download/install, downgrade, prerelease, web-installer and differential-download paths; reconciles Electron Builder feed metadata to the exact signed version/URL/byte count; rejects updater-owned symlinks or SHA-256 mismatches both after download and immediately before installation; and only then permits `quitAndInstall`. The injectible `DesktopUpdateService` performs signed-manifest fetch/admission before the coordinator ever contacts the platform updater. A packaged, exact-schema `update-config.json` is disabled by default; only a release-signed replacement containing HTTPS manifest/feed URLs and Ed25519 public keys enables the typed Settings status/check/install actions. `verify:package` now rejects a package with a missing, malformed or schema-invalid update config. The focused unit tests cover altered feed version/URL/size, absent package, multiple artifacts, symlink and post-prepare tampering, an incompatible signed manifest that must not contact the updater, and invalid disabled/enabled config shapes. No active production endpoint/key set, signed package run, atomic replacement/previous-version retention, or rollback evidence exists yet, so F6 remains deliberately open rather than advertising an update capability in ordinary development builds.
- Update metadata is now decoded through one bounded strict-JSON parser before configuration, remote-manifest, or staging-journal schema validation. It rejects duplicate keys at every depth, `__proto__` prototype mutation, non-finite numeric values, invalid UTF-8 bytes, malformed syntax and nesting beyond 128 levels. The staging recovery suite writes a duplicate `schema_version` key into an otherwise valid journal and proves that it is discarded rather than interpreted according to JavaScript's last-key-wins behavior; the parser and all Desktop tests remain green. This is an input-integrity hardening step, not a claim that target-platform replacement/rollback is closed.
- Windows CI now builds a `native-contract-fixture` only under Cargo's `native-contract-fixture` test feature; production broker packages do not include it. The target-native broker contract now exercises an AppContainer child that writes only to the declared output root, attempts an ungranted read and a local loopback connection, floods stdout beyond the bounded capture budget, and sleeps beyond its wall-time limit. Each case asserts an authoritative broker result and `job_terminated=true`. The feature still needs an actual Windows runner result before it can count as adversarial evidence, and AnalysisRun remains fail closed until that evidence and signing are complete.
- [Desktop lifecycle validation matrix](docs/release/desktop-lifecycle-validation.md) now turns the open first-run, update, interrupted-install, rollback, uninstall/reinstall, sleep/wake, accessibility and GPU gates into exact signed-candidate evidence rows for macOS arm64/x64 and Windows x64. It is deliberately an operator record rather than a synthetic local claim: target installer behavior, SmartScreen/Gatekeeper, previous-version retention and manual/automatic recovery must be recorded on clean machines before F6 can close.
- A private local update staging journal now covers the part before installation replacement: only an exact runtime `VerifiedUpdate` shape admitted by the signed manifest contract can be streamed into a fresh private directory; byte count and SHA-256 are verified before the archive is renamed and an fsynced journal is committed. On macOS, the staging directory itself is also fsynced after both the archive and journal renames, closing the durable-rename crash window rather than relying on a file fsync alone. Electron main runs recovery after it creates the workbench window, without exposing archive paths or bytes over Desktop IPC; it retains only a bounded canonical journal plus exactly one regular archive, both matching immutable HTTPS/semver/schema/hash metadata. Partial, unjournaled, oversized, non-canonical, extra-content, or subsequently modified staging directories are discarded. The unit suite exercises successful restart recovery, interrupted download cleanup, post-stage tampering, unsafe journal metadata, non-canonical timestamps, unexpected files, and the final private archive/journal layout. This narrows the crash window but does not yet make an installer switch, previous-app retention or rollback claim.
- `npm run sign:update-manifest` now makes that contract an operator workflow: it accepts a reviewed unsigned release description and an Ed25519 PKCS#8 private key, signs canonical JSON, validates target URL/hash/size metadata, and refuses to overwrite an already reviewed manifest. A CLI regression generates a fresh key, signs a target package, then verifies the result through the Desktop admission contract. It still does not put private keys in CI, publish a manifest, or replace an installed application.
- The main process now also records a private, fsynced `ocean-desktop-update-handoff/v1` before it invokes `quitAndInstall`: it binds the exact current runtime identity to the already admitted newer target. The next launch accepts only the target identity (`applied`) or the exact prior identity (`previous_runtime_resumed`); malformed journals, duplicate fields, symlinks and unexpected versions are discarded/fail closed. Settings can surface this bounded outcome without exposing package paths or manifest bytes. This is a verifiable upgrade-handoff observation point, not a claim that platform installer replacement, previous-bundle retention, or signed-candidate rollback is complete.
- The Desktop package configuration now distinguishes CI’s verified `dir` bundle from delivery artifacts: `package:release:mac` builds the macOS zip (update payload) and dmg (user install) formats, while `package:release:win` builds an NSIS installer. Both retain sidecar verification before and after packaging. They are deliberately unsigned development artifacts until the separate Developer ID/notarization and Authenticode gates execute on their target release infrastructure.
- The separate protected `desktop-signed-release` workflow now defines that target release infrastructure without weakening normal CI: it accepts only an immutable tag matching the package version, builds native macOS x64/arm64 and Windows x64 candidates, requires all platform signing credentials before packaging, forces `electron-builder` to reject a missing identity, and verifies the final app, frozen sidecar, installer/notarization evidence before uploading private candidate artifacts. The repository intentionally contains neither credentials nor a completed signed target run, so the signed/notarized macOS and Windows Authenticode acceptance boxes remain open; see [Desktop signing runbook](docs/release/desktop-signing-runbook.md).
- Release packaging now derives one exact target architecture from the native Node runtime, reads the frozen sidecar binary before Electron Builder runs, and rejects any mismatch. This prevents an arm64 Mac running Node/Python under Rosetta from producing an x64 sidecar inside an artefact labelled arm64. macOS arm64, macOS x64 and Windows x64 therefore require their own native release runners; cross-building is intentionally rejected because the sidecar belongs to the desktop ABI.
- The 2026-07-16 workbench refinement makes task state an instrument-style readout instead of scattering it between sidebar and header: the active lifecycle, conversation generation and output count are visible beside the task title; output cards show concrete artifact type and immutable version (plus a non-default relation where present); and the map Layers surface exposes its exact layer count. The macOS visual baseline and DOM assertions cover this state without changing the task, map or artifact contracts.
- Task outputs now follow the intended progressive-disclosure rule: the header's accessible `FileOutput` control exposes the exact immutable output shelf only on demand, while the lifecycle/generation/output-count readout stays visible during normal conversation. Opening or closing that renderer-local shelf does not mutate a task, selection, MapScene or artifact. The Electron visual contract checks the collapsed state, its labelled control/count, the expanded exact-output list, and the collapsed conversation screenshot.
- The output shelf's hidden state now has an explicit layout invariant: `conversation-pane` names independent `header`, `outputs`, `transcript`, and `composer` Grid areas. Removing the optional shelf from layout therefore collapses only its own row; it cannot auto-place the transcript or composer into a different track and turn the message input into unused vertical space. The visual Electron contract asserts the hidden shelf and a bounded composer height before it accepts the collapsed-state screenshot. On 2026-07-16, the production renderer build, 87 Desktop unit tests, and all 19 Electron scenarios passed with this invariant.
- The task composer now grows with a multi-line research question until `min(240 px, 32vh)`, then scrolls internally. It no longer exposes an unconstrained browser resize handle that can push the transcript out of the working viewport; clearing the draft restores the compact height. This remains renderer-local draft presentation, works with pinned map/plot context chips, and does not create task or MapScene state. The Electron visual contract fills 40 lines, proves the 240 px/overflow boundary, clears the text, and proves the compact composer returns before taking its baseline screenshot.
- The same fixed-viewport visual contract now exposes the persistent map's live center and zoom in the Spatial Workbench header (for example, `8.0°S · 132.0°E · z4.0`). This is derived from `TaskMapState.viewport`, carries an accessible name with the exact value, and disappears in compact layout so map controls retain their fixed space. The Electron visual regression asserts the fixture coordinate plus a valid runtime zoom before taking the reviewed screenshot.
- A selected Point, Transect, or Region now also receives a compact map-local readout beside the navigation controls. It names the anchor, its geometry class, and the number of linked views without covering the layer control or duplicating the dock's evidence actions. The accessibility contract requires this readout and the detailed linked-visualization anchor context to agree after a real map click; the visual baseline captures the selected state.
- The Linked Visualization Dock now carries a compact selected-anchor context bar before its plot controls: it derives `Point`, `Transect`, or `Region` from the existing MapScene GeoJSON geometry and shows the exact anchor label plus its bounded linked-view count. This is renderer-only presentation over the existing `SceneFeature`, `selection_ref`, and `plot_refs`; it introduces no duplicate spatial model or new backend state. Both the accessibility contract and reviewed visual baseline wait for this state after the MapLibre click, so an asynchronous transition frame cannot become the accepted map-linked visualization screenshot. Current local evidence is `npm run check`, 87 Vitest cases, and all 19 Electron scenarios; it remains separate from the target-platform release gates.

## 11. 测试策略

### 11.1 默认 CI

- fake streaming model覆盖 task、chat、tools、permission、compaction、cancel和recovery。
- synthetic NetCDF/PDF/MapScene fixtures覆盖地图与artifact流程。
- 普通 PR CI 不调用真实模型，不下载真实海洋大数据。
- real-model eval与真实数据 walkthrough是显式发布 gate。

### 11.2 不可妥协的测试不变量

以下断言必须由自动测试直接证明，不能依赖截图、日志目测或“代码看起来正确”：

1. **Task isolation**：Task A 的display transcript、checkpoint messages、active request、pinned refs和MapState不会出现在Task B。
2. **Memory equivalence**：同一stable checkpoint在backend重启前后恢复出的canonical `ConversationMessage[]`完全一致。
3. **Transcript is not memory**：修改renderer transcript cache不能改变backend checkpoint；interrupted display rows不能自动进入模型上下文。
4. **Atomic terminal**：`request.completed`、checkpoint、task generation和active-request clear要么全部提交，要么全部回滚。
5. **Replay idempotence**：commit后broadcast前crash只重放同一terminal/checkpoint，不重复工具mutation或conversation generation。
6. **Compaction fidelity**：恢复的是压缩后的synthetic memory；已被移除的原始消息不重新注入。
7. **Exact spatial refs**：task只能恢复其active MapScene exact ref以及该scene可达的Selection/SpatialLayer/LinkedPlot。
8. **No renderer checkpoint access**：Protocol schema、snapshot、IPC和diagnostics都不暴露`messages_json`或隐藏system prompt。
9. **Fail closed**：损坏、超限、未知schema或hash错误的checkpoint不会被部分解析或退化为纯文本。
10. **Offline determinism**：F0--F4的默认门禁使用fake model和frozen fixtures，不依赖API、网络或真实研究者。

### 11.3 Backend unit、store 与 migration tests

Conversation serialization：

- round-trip覆盖text、tool-use、tool-result、空assistant text、Unicode、structured metadata和synthetic compaction memory。
- canonical JSON、schema version、payload hash和hard size/message limits。
- 不允许API key、file grant、resource token或raw environment进入checkpoint metadata。
- `QueryEngine.load_messages()`后messages与checkpoint canonical payload相等。

TaskStore：

- create/list/get/open/rename/archive/reopen与task revision conflict。
- append-only transcript sequence、分页cursor、request/turn/tool关联和interrupted projection。
- task artifact links固定artifact version，不接受title/path作为identity。
- TaskMapState绑定exact MapScene ref，未知workspace/ref、stale revision和cross-task write被拒绝。
- 同一task只允许一个stable checkpoint pointer和一个active foreground request。

事务与恢复：

- 在terminal transaction前、transaction中、commit后broadcast前分别注入crash。
- 验证rollback/replay后request state、checkpoint count、generation和task pointer。
- repeated request ID + same canonical payload返回同一terminal；different payload触发冲突。
- failed/cancelled/interrupted request保持旧stable checkpoint，同时display transcript保留明确终态。

Migration：

- fresh database、当前schema数据库、含旧`session_records`数据库和只含artifacts/runs的workspace。
- migration重复运行不改变结果；中途失败后可从backup恢复。
- 无真实messages的legacy session不会被标成“模型记忆已恢复”。
- downgrade或未知未来schema明确拒绝，不删除workspace evidence。

### 11.4 Router、agent memory 与 recovery integration tests

使用scripted fake provider，不靠自然语言模糊判断：

- Task A首轮写入marker `ALPHA_ONLY`，Task B写入`BETA_ONLY`；切换和backend重启后，provider收到的messages必须各自只包含对应marker。
- Task A完成tool use/result后重启；下一轮fake provider必须收到结构化tool blocks，而不是UI拼出的文本。
- 强制触发auto-compaction；checkpoint中只保留synthetic memory和规定recent messages，恢复后保持相同generation。
- request执行中kill backend；恢复后request为`interrupted`，模型从请求前checkpoint继续，部分assistant delta不进入messages。
- request完成后、event broadcast前kill backend；重连返回相同terminal event ID/checkpoint ID。
- cancel agent request与cancel AnalysisRun分别清理正确execution handle和process tree，不清除其他task状态。
- provider/model变更需要匹配disclosure policy，记录新conversation generation；未确认时不hydrate runtime。
- renderer sequence gap触发task/workspace snapshot resync，不把旧task的增量应用到新task。
- task archive/reopen不会重写checkpoint；active request期间switch/open另一个task返回typed conflict。

### 11.5 Spatial 与 Desktop renderer tests

Spatial contract：

- MapScene -> SpatialAnchor projection只读取Selection-backed feature，不创建第二套artifact。
- SpatialLayer registration覆盖checkerboard、north/south、nodata、dateline parts、units和colorbar。
- Point点击打开time series/profile/T-S/scatter；Transect点击打开section/Hovmoller。
- LinkedPlot的`selection_ref`与MapScene feature `plot_refs`不一致时fail closed。
- Task A/B拥有不同scene/viewport/layer controls；连续切换100次不串state。
- scene升级到新exact version时显式迁移可匹配layer control，未知layer control被丢弃并记录diagnostic。

Renderer component：

- transcript streaming只更新conversation slice，MapLibre component不unmount/recreate。
- Dock开关、plot切换和inspector不改变MapScene artifact或隐式加入agent context。
- “Use in task”生成exact selection/plot chips；仅hover/current tab不生成context。
- renderer reload从task snapshot恢复，不从localStorage恢复科研状态。
- large transcript、50,000-point plot、5 MiB structured payload和32 MiB raster parts不越过预算。

Desktop E2E：

- Playwright Electron驱动packaged renderer与fake backend。
- macOS/Windows覆盖1280x720、1440x900、Retina和Windows 125%/150% scaling。
- 创建Task A/B -> 分别对话 -> 分别打开MapScene -> 重启app/backend -> 验证conversation与map精确恢复。
- active request时尝试switch，验证Keep Current/Cancel and Switch路径和focus restore。
- canvas pixel checks验证registration、north/south、nodata和dateline。
- keyboard-only、screen-reader label、focus trap、high contrast和reduced motion。

### 11.6 Security、host 与跨平台 tests

- malicious task title/transcript/metadata、PDF/SVG、model URL、forged IPC和XSS。
- arbitrary path、file-grant replay、artifact-token replay和cross-workspace/task access。
- renderer尝试请求checkpoint payload、system prompt或provider secret时被schema/capability拒绝。
- diagnostics/export不包含messages payload、secret、absolute path、raw data或完整logs，除非另有明确受控研究导出合同。
- sidecar executable/version/schema hash不匹配时不进入ready state。
- macOS/Windows sandbox覆盖file/network/process/resource/symlink/junction/ADS escape。
- updater覆盖bad signature、downgrade、partial package和app/backend不兼容。

### 11.7 建议测试文件与阶段命令

新增backend tests：

```text
tests/test_ocean_partner/test_task_store.py
tests/test_ocean_partner/test_task_checkpoint.py
tests/test_ocean_partner/test_task_protocol.py
tests/test_ocean_partner/test_task_router.py
tests/test_ocean_partner/test_task_recovery.py
tests/test_ocean_partner/test_task_map_state.py
tests/test_ocean_partner/test_task_migration.py
```

新增frontend tests：

```text
frontend/ocean-desktop/src/**/*.test.ts(x)
frontend/ocean-desktop/tests/task-switch.spec.ts
frontend/ocean-desktop/tests/task-memory-recovery.spec.ts
frontend/ocean-desktop/tests/spatial-workbench.spec.ts
frontend/ocean-desktop/tests/security-boundary.spec.ts
```

F0 merge gate：

```bash
pytest -q \
  tests/test_ocean_partner/test_task_store.py \
  tests/test_ocean_partner/test_task_checkpoint.py \
  tests/test_ocean_partner/test_task_protocol.py \
  tests/test_ocean_partner/test_task_router.py \
  tests/test_ocean_partner/test_task_recovery.py \
  tests/test_ocean_partner/test_task_map_state.py \
  tests/test_ocean_partner/test_task_migration.py
```

F1--F3 merge gate在上述基础上增加：

```bash
npm --prefix frontend/ocean-desktop run typecheck
npm --prefix frontend/ocean-desktop run test
npm --prefix frontend/ocean-desktop run e2e
npm --prefix frontend/plot_studio run check
```

CI 必须把task/checkpoint tests加入默认required checks；不得只放在手动desktop workflow。真实模型和真实数据walkthrough保持独立pre-release job。

### 11.8 三条产品 walkthrough

1. **Non-coding research**：比较论文、提炼证据和局限，不创建 AnalysisRun。
2. **Spatial field**：请求“绘制 2010 年中国海 SST”，agent获取/读取数据、写代码、运行checks并把 SpatialLayer直接叠加到地图。
3. **Map-linked analysis**：在地图选择站点或 transect，点击后显示时间序列、T-S 或 section，并把 selection exact ref带入下一轮分析。

日常 CI 使用 frozen local subset完成 2/3；真实数据下载和真实模型版本留到 pre-release，不阻塞前端主体开发。

## 12. 性能与视觉标准

性能至少监控：

- first paint、backend ready、task snapshot ready和map first meaningful render。
- idle/active renderer、sidecar和GPU memory。
- token-to-paint latency；streaming期间map frame stability。
- 50,000-point LinkedPlot、5 MiB structured payload、32 MiB raster parts和多layer地图。
- task history分页、长transcript virtualisation和artifact search。
- task switch、renderer reload、backend restart与map state restore时间。

视觉要求：

- 安静、工作型、低chrome；不做landing page、hero、渐变背景或card wall。
- sidebar紧凑，中间conversation留白，Spatial Workbench使用真实全幅地图。
- 只在重复artifact item、modal和真正framed tool中使用card，圆角不超过8 px。
- Lucide icons用于熟悉操作，陌生图标有tooltip和accessible name。
- toolbars、split panes、map、chart和composer尺寸稳定，动态内容不引发布局跳动。
- 字体大小不按viewport缩放；macOS/Windows 100%--200% scaling下不溢出或遮挡。
- 深浅主题使用多种语义颜色，色彩不是状态的唯一表达。
- toast只提示瞬时UI事件；request/artifact/run状态必须有持久位置。

## 13. Open Decisions 与风险

| ID | 状态 | 决策点 | 关闭条件 |
| --- | --- | --- | --- |
| FE-01 | closed | Electron vs Tauri | v1保留Electron；只有实测预算超限才重开 |
| FE-02 | closed | Renderer如何连接backend | main-owned stdio + typed preload |
| FE-03 | open | TaskStore schema、ConversationCheckpoint和现有session迁移 | F0 atomicity/recovery/compaction/migration fixtures通过 |
| FE-04 | open | TaskMapState哪些字段project-local持久化 | 明确evidence/view边界和restore tests |
| FE-05 | open | PyInstaller onedir/Nuitka/standalone Python | 双平台clean-machine/native libs/signing spike |
| FE-06 | open | Windows trusted sandbox机制 | 完整adversarial contract通过，Job Object单独不合格 |
| FE-07 | open | macOS Seatbelt退出策略 | signed package重测并记录替代方案 |
| FE-08 | open | 最低窗口宽度与compact交互 | Windows scaling和macOS小屏prototype通过 |
| FE-09 | open | updater服务与签名密钥运营 | rollback、offline、privacy和key custody完成 |
| FE-10 | open | checkpoint与provider/model/runtime-profile变化的兼容策略 | schema/hash fail-closed和新generation continuation tests通过 |

主要风险：

| 风险 | 应对 |
| --- | --- |
| 照搬Codex后失去海洋特色 | 常驻Spatial Workbench和MapScene关系是v1硬合同 |
| 地图又变成普通output tab | 宽屏始终挂载；linked views在Dock中打开，不替换地图 |
| 为UI发明第二套地图模型 | 复用Selection/SpatialLayer/LinkedPlot/MapScene，SpatialAnchor只做projection |
| task history只存在renderer | backend TaskStore和Protocol v2先行，localStorage不保存科研状态 |
| UI transcript被误当作agent memory | display rows与versioned ConversationCheckpoint分表；只允许backend调用`QueryEngine.load_messages()` |
| terminal event与memory checkpoint半提交 | 同一SQLite transaction，覆盖commit前/中/后crash injection |
| checkpoint泄露隐藏prompt或secret | renderer capability不可读取messages；只存prompt fingerprint，不存secret/token/environment |
| 新task继承错误图层造成研究混淆 | task-bound MapScene/TaskMapState，project base map显式继承 |
| streaming导致地图频繁重建 | conversation/map slices分离，MapLibre instance有稳定lifecycle |
| frontend重写backend | Electron main禁止domain mutation，所有科研状态走router |
| 大数据拖垮IPC/renderer | artifact URI、bounded payload、raster parts和virtualisation |
| Windows UI完成后误称完整支持 | Desktop v1包含trusted AnalysisRun，未通过则fail closed |
| skills再次变成固定算法仓库 | skills只放研究规范，算法实现由模型coding + AnalysisRun/checks产生 |
| 真实研究者验证延期阻塞工程 | 工程阶段使用fixtures；真实walkthrough只作为stable release gate |

## 14. Desktop v1 Definition of Done

只有以下条件全部满足，才能称为 Ocean Research Partner Desktop v1：

1. App 以 Project/ResearchTask 为第一层组织，支持创建、恢复、重命名、归档和搜索任务。
2. 左侧task history、中间conversation和右侧常驻Spatial Workbench构成默认工作区。
3. task切换和app/backend重启后，display transcript、stable ConversationCheckpoint、terminal request、pinned refs、MapScene和TaskMapState正确恢复。
4. SpatialLayer可直接叠加地图，registration、units、colorbar、time/depth和nodata可检查。
5. Point、Transect和Region均可作为Selection-backed anchors；点击后LinkedPlot在Dock打开且地图保持可见。
6. 用户可把selection/plot exact refs明确加入composer，agent不能把hover或当前tab当隐式研究上下文。
7. 非coding论文任务不被强迫执行代码；数据任务可完成plan -> code -> AnalysisRun -> checks -> artifact闭环。
8. artifact可回溯到task、request、inputs、code、environment、run、checks和conversation。
9. Desktop与TUI使用同一Protocol v2、backend、workspace和exact artifact refs。
10. renderer无Node/filesystem/process/credential权限，Host IPC、file grant和artifact resource均fail closed。
11. macOS 13+ arm64/x64与Windows 11 x64安装、首次启动、升级、恢复和卸载通过。
12. 两个平台trusted AnalysisRun通过adversarial sandbox contract；不可用时publish/approve/report fail closed。
13. renderer永远不能读取checkpoint messages或隐藏system prompt；display transcript不能被伪造为模型上下文。
14. terminal/checkpoint/task-generation原子提交、compaction恢复、A/B task memory isolation和三处crash injection测试通过。
15. renderer/backend crash、cancel、sleep/wake和app quit不产生重复mutation、丢失evidence或orphan process。
16. 2010中国海SST spatial-field walkthrough与Point/Transect linked-view walkthrough在frozen fixture上通过。
17. stable发布前完成real-model frozen gates和真实非实现者researcher walkthrough；日常开发不等待这一人工步骤。

## 15. 下一步工程顺序

1. 先完成 F0：为ResearchTask、ConversationCheckpoint、TaskMapState和现有四类spatial artifacts写ADR、schema、migration与crash-recovery fixtures。
2. 从 `frontend/ocean_terminal` 提取 `ocean-client`，让TUI先消费新的task-aware client。
3. 从 `frontend/plot_studio` 提取MapLibre、SpatialLayer和LinkedPlot components，保持现有visual checks通过。
4. 建立最小Electron shell，连接真实packaged backend，完成project/task snapshot和sidecar lifecycle。
5. 做第一个vertical slice：左侧切换两个tasks，中间显示真实conversation snapshot，backend分别恢复不同模型记忆，右侧分别恢复不同MapScene。
6. 做第二个vertical slice：SpatialLayer直接叠加；点击Point/Transect在Dock打开linked plot；“Use in task”加入exact refs。
7. 再迁移streaming、permissions、file grants、AnalysisRun、artifacts、review和multi-agent。
8. 最后完成Windows sandbox、双平台签名更新、real-model gates和真实researcher walkthrough。

第一个可演示里程碑不应是静态mockup，而应是：Desktop App启动真实Ocean backend，打开project-local workspace；用户从左侧切换ResearchTask，中间看到对应对话，backend用各自stable checkpoint恢复不会串场的模型记忆，右侧地图恢复对应SpatialLayer和viewport；点击站点或transect后，在不关闭地图的情况下打开真实LinkedPlot、code和verification evidence。关闭并重启app/backend后，同一行为仍然成立。
