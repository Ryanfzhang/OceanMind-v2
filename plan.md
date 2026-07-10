# Ocean Research Partner Workbench 实施计划

> 状态：架构基线与实施路线 v4  
> 目标仓库：OpenHarness 作为 agent runtime，Ocean Research Partner 作为独立上层应用  
> 当前优先级：先用 walking skeleton 验证受限 runtime、AnalysisRun 和地图链路，再固化协议、store 与完整领域模型

## 0. 这份计划解决什么

Ocean Research Partner 不是“自动研究机器”，而是一个科研伙伴型 workbench。它应当帮助研究者围绕论文、数据、代码、图、假设、实验、审查和报告持续协作，同时始终保留人工判断权。

这份计划同时承担三种职责：

- 定义产品边界：什么属于 OpenHarness core，什么属于 Ocean Partner。
- 定义工程契约：尤其是 TUI 与后端协议、artifact/run 状态机和验证门禁。
- 定义实施顺序：每个阶段必须形成可运行、可测试、可验收的纵向成果。

本计划不按“第几个月”强行推进，而按依赖关系和验收结果推进。阶段只有在验收条件满足后才进入下一阶段。

## 1. 已确定的架构决策

以下决策作为第一版基线，后续若修改必须记录 ADR（Architecture Decision Record）。

| 主题 | 决策 |
| --- | --- |
| 产品名称 | 统一使用 **Ocean Research Partner**；代码包使用 `ocean_partner`，不再混用 Ocean Researcher Partner |
| 产品形态 | Ocean Partner 是组合 OpenHarness runtime 的独立 App，不只是一个普通插件 |
| Core 边界 | OpenHarness 只提供通用 agent loop、模型适配、工具、权限、hooks、MCP、session、structured events 和 cancellable execution |
| Ocean 边界 | 科研对象、artifact store、分析运行、FigureSpec、验证、review、workspace 和领域 UI 全部位于 `ocean_partner` |
| Runtime 能力边界 | Ocean 使用 deny-by-default `RuntimeProfile`，显式列出 tools、MCP、hooks、权限、路径范围和 model/tool budgets；不直接继承 OpenHarness 默认 registry |
| Tool 调度 | 只读 tool calls 可并发；workspace/run mutation 按 effect class 串行，分别持有 workspace lock 或 per-run lock |
| CLI | 保留 `openharness` / `oh`；新增 `ocean`，入口为 `ocean_partner.cli:app` |
| TUI 与 Plot Studio | React + Ink TUI 负责会话、run 和 artifact 管理；交互地图由同一 backend 提供的本地 React graphical view 承担 |
| 进程所有权 | TUI 启动并拥有唯一 Ocean backend；backend 服务 stdio 与 loopback WebSocket；TUI 退出时关闭 backend 和对应 graphical session |
| Run 生命周期 | AnalysisRun 是 plan-bound attempt 容器；v1 coding attempt 是有明确资源上限的 foreground operation。backend 退出时只将活动 attempt 标为 `interrupted`，run 保留为可显式重试的 `ready` |
| 前后端传输 | Protocol v2 envelope 与 transport 解耦；TUI 使用 stdin/stdout JSONL，graphical Plot Studio 使用仅监听 loopback 的 WebSocket adapter |
| 协议兼容 | 现有 `oh` 继续使用 v1；`ocean` 从 v2 开始，不在同一事件模型中混用 v1/v2 |
| 协议类型源 | Python Pydantic model 为唯一源，导出 JSON Schema，再生成 TypeScript discriminated union |
| 元数据存储 | project-local SQLite 是 workspace、关系、review、状态和 event 的权威数据源；文件系统 version directory 是不可变内容存储与可移植导出 |
| 请求幂等 | mutating request 先写 durable RequestRecord；request identity、domain mutation 和 terminal result 必须可以在崩溃后重放，不以内存 session 去重 |
| Agent 形态 | Ocean Partner 是 research-first、evidence-grounded agent；论文分析、假设设计、审查和写作不强制编码 |
| 计算任务 | 当研究结论依赖计算时，默认使用可见、可修改、可复现的代码，而不是隐藏 skill、recipe 或固定算法工具 |
| Kernel 定义 | kernel 是受控 Python 科学计算环境及成熟第三方库，不是预定义的 `compute_anomaly` / `compute_trend` 函数目录 |
| 工具边界 | Ocean 工具只提供数据检查、运行隔离、执行、输出检查、artifact 和 provenance 等横向基础设施；不按分析方法制造一批纵向 tools |
| Skill 边界 | 不采用“一种海洋分析对应一个 SKILL.md”；skill 用于研究过程规范、Ocean 科学判断和审查框架，不承载固定代码和工具调用流程 |
| 分析执行 | Python script 是第一版规范执行单元；Notebook 可导入/导出，但不作为唯一可复现记录 |
| Python 环境 | v1 使用安装时确定的 Ocean runtime environment；运行中不静默安装依赖，缺失依赖返回 capability error |
| 数据可复现等级 | DatasetArtifact 明确区分 `materialized_snapshot` / `cached_subset` / `immutable_remote_version` / `reference_only`；checksum 不等于保存了可重跑字节 |
| 模型数据边界 | 完整 run logs/arrays 只留本地；模型默认只获得结构化状态、受限 sample 和经 disclosure policy 处理的 diagnostics，sandbox 禁网不能代替这一层 |
| 绘图栈 | Python + xarray + NumPy/Pandas + Matplotlib；地图能力作为可选 extra 引入 Cartopy |
| Map MVP | spatial field 使用本地 PNG image overlay + 严格 EPSG:4326 pixel-registration contract；linked plots 使用有大小上限的结构化 JSON + PNG/PDF 导出；离线验收不依赖公网底图 |
| 代码安全 | 可发布的模型生成结果必须来自受控 sandboxed run；unsandboxed 只能作为显式标记的手工实验，不能直接 publish/approve |
| 正确性 | 模型不能自行宣称“正确”；execution trust、machine checks、dependency impact 和 human review 是独立门禁，`checks_passed` 不等于科学结论成立 |
| 依赖影响 | 上游 artifact 变更不改写旧版证据；另行计算 `impact_state`，让下游 figure/claim/report 显示 stale、invalidated 或 source-unavailable |
| 多 agent | 先完成可靠的单 agent 闭环；多 agent 后置并通过 feature flag 显式启用 |

## 2. OpenHarness 当前基线与差距

### 2.1 可以直接复用的能力

当前 OpenHarness 的主要调用链为：

```text
openharness.cli:app
  -> ui.runtime.build_runtime()
  -> QueryEngine.submit_message()
  -> engine.query.run_query()
  -> api_client.stream_message()
  -> tool call / permission / hook
  -> tool result 回填消息
  -> 下一轮模型调用，直到没有 tool_use
```

可以直接复用：

- `src/openharness/api/`：Anthropic、OpenAI-compatible、Codex、Copilot 等模型客户端。
- `src/openharness/engine/query.py`：真正的 agent loop。
- `src/openharness/engine/query_engine.py`：conversation ownership、usage 和 turn orchestration。
- `src/openharness/tools/`：文件、shell、搜索、Notebook、MCP、skill 和后台 shell task。
- `src/openharness/permissions/` 与 `sandbox/`：执行授权与沙箱适配。
- `src/openharness/hooks/`：pre/post tool 等生命周期扩展。
- `src/openharness/services/compact/`：conversation context 压缩。
- `src/openharness/memory/`：持久偏好和项目记忆的基础能力。
- `src/openharness/plugins/`：skills、commands、agents、hooks 和 MCP 配置加载。
- `src/openharness/ui/backend_host.py` 与 `frontend/terminal/`：React TUI 的 stdio JSONL 进程模型。

### 2.2 不能直接当作 Ocean backend 的部分

当前 plugin loader 能贡献 skills、commands、agents、hooks 和 MCP server，但不能直接贡献：

- 新的 TUI 页面和 reducer。
- 新的后端 request handler。
- 新的 domain event schema。
- 与 artifact store 同事务提交的 Python domain tool。

因此 Ocean Partner 不能只靠 `plugins/ocean-research-partner/` 完成。正确结构是：

- `src/ocean_partner/`：真正的应用、协议、store、tools 和 backend。
- `plugins/ocean-research-partner/`：少量项目规范资料、commands、agent definitions 和外部 MCP 配置；不承载海洋分析 workflow。
- OpenHarness core：只增加不含 Ocean 语义的通用扩展接口。

### 2.3 开工前必须解决的 runtime 风险

这些问题进入 Phase 0，不允许拖到 Plot Studio 之后：

1. `run_query()` 在 compaction 后把局部变量 `messages` 重新绑定到新 list，但 `QueryEngine._messages` 仍可能引用旧 list；必须明确 conversation ownership，并用测试证明压缩后的消息会被后续 turn 和 session snapshot 持久化。
2. `AssistantTurnComplete` 当前表示一次模型 turn 完成，不代表整个用户 request 完成；现有 frontend 却可能据此清除 busy。v2 必须区分 `assistant.turn.completed` 和唯一终态 `request.completed`。
3. tool event 缺少 `tool_call_id`、`turn_id`、`request_id` 和 result metadata；并行同名工具无法可靠关联。
4. 当前 TypeScript `BackendEvent.type` 是普通 `string`，且大量字段都是 optional，Python/TypeScript 很容易漂移。
5. 当前无 request cancellation、事件 sequence、幂等语义和断线后的 snapshot resync。
6. `BackgroundTaskManager` 的状态在内存中，日志写入全局用户目录；它可以继续服务普通 shell task，但不能作为科研 run 的权威记录。
7. `BashTool` 只向模型返回截断输出；科研任务必须把完整 stdout/stderr、return code、环境和输出 checksum 写入 run record。
8. 沙箱不可用时 OpenHarness 可配置为继续执行；Ocean 的 artifact-producing run 必须 fail-closed，不能通过一次通用确认降级为可发布的 unsandboxed execution。
9. 当前工作树中 `pyproject.toml` 仍引用 `README.md`，发布前必须保证该文件存在或修改 package metadata，否则 build 会失败。
10. `swarm/`、`coordinator/`、`local_agent_task.py` 和 agent/team tools 的保留边界需要测试锁定，不能意外回到默认 registry。
11. `build_runtime()` 目前自动创建默认 tool registry 并连接所有可见 MCP；Ocean 需要能从空 registry 组合 allowlisted tools/MCP/hooks，否则 Bash/Web/task tools 可以绕过 AnalysisRun。
12. `run_query()` 对同一轮的多个 tool calls 使用并发执行；Ocean domain mutation 需要通用 effect classification 和调度策略，不能只靠每个 tool 自己小心。
13. 现有 sandbox adapter 主要控制文件和网络，尚未形成 CPU、memory、disk、process count、output size 和 process-group cleanup 的 AnalysisRun 合同。
14. 如果 mutation 已提交而 terminal event 尚未发送时 backend 崩溃，单纯 session-local `request_id` 去重不足以防止重试产生重复状态。

### 2.4 Open Decisions Register

未关闭的问题必须记录决定、理由和 ADR；超过 `close_before` 不得进入对应阶段。

| ID | 状态 / ADR | 未决问题 | close_before | 关闭条件 |
| --- | --- | --- | --- | --- |
| OD-01 | open | Plot Studio 使用哪一个 React map/chart library | Phase 1 implementation | 离线 assets、Point/Polygon/LineString、image overlay、5 MB linked plot、打包体积原型通过 |
| OD-02 | open | real-model reference eval 的模型 profile 与通过阈值 | Phase 4 implementation | 冻结 benchmark/version、model、turn/cost budget、numeric tolerance、repeat count 和最低成功率 |
| OD-03 | open | project-local state 的备份、gitignore、filesystem permissions 和 portable export 策略 | Phase 2 implementation | 明确 DB 是否提交 Git、0700/0600 等本地权限、export bundle 内容、恢复测试和敏感路径/披露过滤 |
| OD-04 | open | remote dataset identity、许可与 catalog provider | Phase 5 implementation | 选定首个 provider，定义 license/size/credential/version/query/fingerprint/materialization contract 和 disclosure audit |
| OD-05 | open | Literature Mode 的检索、PDF、citation 与 prompt-injection contract | Phase 6 implementation | 定义允许来源、全文获取、citation locator、版权边界、不可信文档隔离和 document-text disclosure policy |
| OD-06 | open | Ocean `RuntimeProfile`、tool effect classes 与 MCP/hook allowlist | Phase 0 implementation | 冻结 caller-supplied registry/profile API、effect/scheduler 合同和 skill/MCP/hook filtering，并用测试证明 Bash/Web/task/MCP 不会意外暴露 |
| OD-07 | open | AnalysisRun sandbox backend、resource limits 与 unsafe-run 策略 | Phase 0 implementation | 冻结 OS/platform support、read/write mounts、network、CPU/memory/disk/process/output limits、symlink policy 和 publish gate |
| OD-08 | open | foreground request、attempt、budgets、cancel、shutdown 和 crash recovery 语义 | Phase 1 implementation | 冻结 request terminal 时机、model/tool/attempt 限额、中断后 run/attempt 状态、v1 不透明 resume 的边界，以及 request/attempt cancel API |
| OD-09 | open | Dataset materialization/reproducibility levels | Phase 1 generalization | 用 walking skeleton 定义并验证四种 identity level、pre/post fingerprint、subset/query 记录、source mutation 和 rerun 可用性的 UI 语义 |
| OD-10 | open | Artifact dependency impact/staleness 传播 | Phase 2 implementation | 定义 impact states、触发条件、哪些 typed links 传播、cycle-safe 图遍历、report gate 与 projection rebuild tests |
| OD-11 | open | EPSG:4326 image-overlay pixel-registration contract | Phase 1 generalization | 用 walking-skeleton map spike 冻结 pixel edge/center、row orientation、axis order、antimeridian、nodata/alpha、resampling 和 synthetic alignment fixtures |
| OD-12 | open | ModelDataDisclosurePolicy 与 tool/log redaction contract | Phase 1 generalization | 用 walking skeleton 冻结 workspace disclosure levels、sample approval、stdout/stderr summary、path/secret/numeric-table redaction、显式 excerpt approval 和 audit fields |

每次关闭 Open Decision 时，在 `docs/adr/` 新增 ADR，并把本表状态改为 `closed` 和对应 ADR 路径。

## 3. 产品定位与原则

### 3.1 产品定位

Ocean Research Partner 围绕以下研究循环协作：

```text
Paper -> Claim -> Hypothesis -> Experiment -> Result -> Report
  |         |          |             |          |
  +------ Dataset -----+---------- Figure ------+
                         ^             |
                         +-- Review ---+
```

四个平级工作模式共享同一 workspace 和 artifact store：

- **Literature Mode**：论文、paper card、claim、method、dataset、limitation 和 gap synthesis。
- **Exploration Mode**：观察、假设、可证伪条件、实验计划、baseline、metric 和失败记录。
- **Figure Mode / Plot Studio**：自然语言需求、FigureSpec、数据检查、代码执行、spatial map layers、map-linked plots、静态论文图、版本比较和审查。
- **Writing Mode / Report Builder**：只使用具有明确状态和 provenance 的 artifacts 组织报告或论文草稿。

Review 是跨模式能力，不是单独的自动 agent。用户反馈必须成为 `ReviewArtifact` 或 `DecisionArtifact`，不能只留在聊天历史中。

### 3.2 不可妥协的产品原则

- Agent 可以建议和执行，但不能代替用户做科学判断。
- 不默认写代码；先根据任务判断需要文献证据、推理、计算、审查还是写作。
- 当结论依赖计算时，代码和运行证据必须透明；纯论文分析或研究构思不为形式完整而强行编码。
- exploratory result 不能被表述成 conclusion。
- 每个假设必须写出可证伪条件。
- 每个实验必须包含 baseline、metric、success criteria 和 failure criteria。
- 每个 FigureArtifact 必须关联 FigureSpec、输入数据、代码、参数、环境、运行记录和 verification。
- 分析方法必须以可见、可修改、可重跑的 Python 代码存在，不能隐藏在自然语言 skill 或不可审查的预定义 workflow 中。
- 不为 anomaly、trend、transport、EOF、bloom 等任务逐一创建 skill 或 model-visible tool。
- 失败运行和被拒绝的图不能被静默覆盖，它们是研究过程的一部分。
- 对话是交互界面，不是研究状态的唯一数据库。
- context compaction 不能删除 artifact、decision、review 或 provenance。
- 所有远程数据操作先显示来源、许可、预计体积和目标路径。
- 发送给模型提供商的本地数据 sample、日志片段和文档内容必须符合 workspace disclosure policy；“代码在本地运行”不等于“数据从未离开本地”。
- “生成了文件”不等于“科学上正确”；机器检查与人工审批必须分开。

### 3.3 第一版明确不做

- 不做完全自主的 AutoResearch pipeline。
- 不做大规模多源数据自动融合。
- 不默认下载超出用户确认范围的大数据。
- 不做 3D 海洋可视化。
- 不做通用 GIS 桌面软件。
- 不做 Illustrator/Figma 级出版排版系统。
- 不承诺自动复现任意论文中的全部图。
- 不建设 OceanMind 式的一分析一 skill / recipe catalog。
- 不把 OpenHarness 变成固定海洋算法菜单；新方法默认通过模型写代码实现。
- 不在第一版同时运行多个可写 agent。
- 不让 report builder 把未 approved、checks failed 或 invalidated 的 claim/figure 作为确定结论。stale 证据只能在显式版本理由和用户 DecisionArtifact 后保留；source-unavailable 可以作为历史证据引用，但必须显示无法重跑的 limitation，不得声称 fully reproducible。

### 3.4 Threat 与 Trust Model

v1 可信计算基（TCB）仅包含：本地 Ocean backend 代码、schema/permission/transaction services、打包的 harness validators、选定的 OS sandbox/resource backend、SQLite/filesystem integrity checks 和用户当前 OS account。TUI/Plot Studio 是经过身份绑定的客户端，但不是权威状态源。

下列内容一律作为不可信输入：

- 模型输出、tool arguments、agent-authored `analysis.py` / `verify.py` 和其 stdout/stderr/outputs。
- 论文、PDF、dataset metadata/attributes、文件名、外部数据、网络响应和从中读取的指令性文本。
- MCP/plugin/hook 返回内容，除非对应 provider 和 capability 已在 RuntimeProfile/ADR 中明确信任。
- TUI/Plot Studio 发来的 payload、URI、actor/client ID 和 artifact manifest 声明；backend 必须独立验证。

v1 必须保证：不可信内容不能提升权限、伪造 human approval、绕过 disclosure/sandbox/resource policy、直接改写 canonical store 或把 unsafe/checks-failed output 发布为正式结果。

v1 不保证：自动判定科学结论一定正确、对恶意 root/已被攻破的 OS 隔离、外部 provider 永久可用，或模型提供商在其明示合同之外的保密性。产品必须准确告知已披露什么，不用“local”模糊这些边界。

## 4. 目标架构

### 4.1 分层

```text
Ocean React TUI -------- stdio JSONL -------+
                                            |
Local Graphical Plot Studio -- WebSocket ---+-> Protocol v2
                                                 |
                                      Ocean Backend / Request Router
  -> WorkspaceService / ArtifactService / RunService / ReviewService
  -> Ocean Runtime Adapter
       -> OpenHarness QueryEngine / run_query
       -> OpenHarness tools / permissions / hooks / MCP
       -> Ocean infrastructure tools
  -> SQLite metadata + event journal
  -> versioned filesystem artifacts and run directories
```

依赖方向必须单向：

```text
ocean_partner -> openharness
openharness -X-> ocean_partner
```

OpenHarness 任何文件都不能 import `ocean_partner`。

v1 进程拓扑固定为：

- `ocean` TUI 创建唯一 backend child process，并持有其生命周期。
- backend 从 packaged assets 提供 Plot Studio 页面，并在 loopback 随机端口开放 WebSocket。
- browser 首次打开使用一次性 bootstrap token 换取 session-scoped client capability；refresh 通过该能力重新连接并请求 workspace/scene snapshot，长期 token 不保留在 URL。
- agent submit、permission 和 question interaction 由 TUI 发起并响应；Plot Studio v1 只处理 map、artifact、review 等 domain requests。
- TUI 正常退出或 backend 崩溃时，graphical session 失效；Plot Studio 显示断开状态，不自行启动第二个 backend。

### 4.2 建议目录结构

```text
src/
  openharness/
    ...                         # 通用 runtime
  ocean_partner/
    __init__.py
    cli.py                      # `ocean` 入口
    runtime.py                  # 组合 OpenHarness runtime
    runtime_profile.py          # deny-by-default tools/MCP/hooks/capabilities
    backend/
      host.py                   # Protocol v2 process host
      router.py                 # request -> handler
      event_bus.py
      principals.py             # transport-bound principal/capability
    protocol/
      envelope.py
      requests.py
      events.py
      errors.py
      schema_export.py
    domain/
      workspace.py
      artifacts.py
      links.py
      figures.py
      datasets.py
      hypotheses.py
      experiments.py
      reviews.py
      reports.py
      runs.py
    store/
      database.py
      migrations/
      request_store.py          # durable idempotency/terminal replay
      artifact_store.py
      commit_intents.py
      event_store.py
    services/
      context_builder.py
      dataset_inspector.py
      dataset_materializer.py
      disclosure_policy.py
      impact_projector.py
      figure_service.py
      run_executor.py
      resource_policy.py
      verifier.py
      artifact_publisher.py
      report_service.py
    tools/
      artifact_query.py
      dataset_inspect.py
      dataset_sample.py
      analysis_run.py
      analysis_output.py
    prompts/
      system.md
      figure.md
      exploration.md
    resources/
      skills/                   # 第一方 Ocean research-process skills
      references/               # 方法、数据语义、库和 review references

frontend/
  terminal/                     # 现有 oh v1 frontend
  ocean-terminal/               # Ocean v2 frontend
    src/
      protocol.generated.ts
      reducers/
      views/
      components/
  plot-studio/                  # 本地 graphical view，交互地图与关联图表
    src/
      protocol.generated.ts
      map/
      plots/
      reducers/

plugins/
  ocean-research-partner/
    plugin.json
    skills/                     # 只放实验室规范、审查协议等少量认知资料
    commands/
    agents/
    mcp.json

protocol/
  v2/
    schema/
    fixtures/

docs/
  adr/

tests/
  ocean_partner/
    unit/
    contract/
    integration/
    e2e/

spikes/
  ocean_walking_skeleton/       # Phase 0.5 临时纵向实验，Phase 3 前迁移/删除执行路径
```

### 4.3 OpenHarness core 只增加四个通用扩展点

1. **Runtime composition profile**

   `build_runtime()` 支持调用方传入完整 `ToolRegistry`/registry factory、system prompt sections、metadata、MCP allowlist 和 hook policy。传入 profile 时不再先创建默认 registry 后追加；OpenHarness 只理解通用 capability，不知道 Ocean 类型。

2. **Structured tool events**

   `ToolExecutionStarted/Completed` 增加 `tool_call_id`、`turn_id`、result metadata 和 duration；保留现有 v1 adapter 的兼容转换。

3. **Cancellable query execution**

   `QueryEngine` 提供可取消的 execution handle，取消时终止 model stream 和当前 foreground tool/subprocess。Ocean 的 stdio、WebSocket、event bus 和 request router 全部留在 `ocean_partner.backend`，不进入 OpenHarness core。

4. **Effect-aware tool scheduling**

   `BaseTool.effect_for(parsed_input)` 声明通用 effect class，至少区分 `read_only` / `mutation` / `external_io`，并可返回可选 concurrency key。`run_query()` 仅并发执行可安全并发的调用，mutation 按模型返回的 tool-call 顺序交给可注入 scheduler 串行化。workspace revision、per-run lock 和 domain transaction 仍由 Ocean services 实现，不信任模型自行提供 lock key。

除这四个扩展点外，第一版不为 Ocean 业务修改 `run_query()` 的领域逻辑，也不把 WebSocket 或 Ocean protocol 放入 OpenHarness core。Ocean 必须有回归测试证明模型不能看到 profile 之外的 Bash、Web、task、worktree 或 MCP tools。

### 4.4 Plugin 与 App 的职责

`ocean_partner` App 必须独立可运行。插件 wrapper 是可选增强，只负责：

- 实验室或项目特有的数据规范、审查协议和复现要求。
- 用户可调用的 slash commands。
- 后期 multi-agent 的角色定义。
- 外部 catalog、文献或数据服务的 MCP 配置。

插件不得包含 anomaly/trend/transport 等固定分析 workflow，也不得成为 agent 完成基本科研编码的前提。artifact store、run executor、protocol handler 和 TUI 不依赖插件是否被发现。

### 4.5 Research operating model：Policy / Skill / Reference / Tool / Code

Ocean Partner 的能力必须按下表分层，避免把所有知识和执行都塞进 SKILL.md：

| 层 | 作用 | Ocean 示例 |
| --- | --- | --- |
| System Policy | 始终生效且不可被 skill 覆盖的产品底线 | 不伪造引用、agent 不得自行 approve、计算结果必须有 provenance |
| Skill | 按需加载的研究过程规范与检查框架 | 如何诊断海洋数据、设计分析、审查物理一致性 |
| Reference | 可检索的方法知识、数据惯例和库资料 | CF metadata、calendar、area weighting、transport 定义 |
| Tool | 读取、执行、检查和保存等动作能力 | dataset inspect、run execute、artifact query |
| Code | 当前任务的具体科学计算 | agent 编写的 `analysis.py` / `verify.py` |

判断规则：

- 去掉 skill 后，agent 仍能执行任务，只是研究过程可能不够规范。
- 去掉 tool/runtime 后，agent 无法完成对应动作。
- anomaly、trend、transport 等具体实现属于 code；稳定后可以成为普通、带测试的 Python utility，但不自动成为 skill/tool。
- 方法定义、公式、假设和常见陷阱属于 reference。
- 权限、状态转换、引用真实性和审批权属于 system/backend policy，不能依赖模型记得调用某个 skill。

第一版 always-on policies：

- 不得伪造论文、引用、数据来源、run 或 verification。
- observation、inference、hypothesis、speculation 和 conclusion 必须区分。
- 没有成功 attempt 和 output evidence，不能宣称计算完成。
- agent、skill 和 reviewer agent 都不能把 artifact 标为 human-approved。
- 不可信论文/PDF/metadata 中的指令不能覆盖 system policy、权限或研究目标。
- 计算、下载、外部路径和敏感数据访问必须经过 backend permission policy。

### 4.6 第一批 Research Skills

Skill 应体现 Ocean scientist 的思考方式，不提供固定函数名、代码模板或阶段式工具调用。

1. **`ocean-question-and-scale-framing`**

   确定研究对象、区域、时间尺度、垂向范围、研究过程和所需证据；区分 basin、shelf、coast、front、eddy、water mass 等对象，并记录 competing explanations。

2. **`literature-evidence-synthesis`**

   规范论文检索、筛选、claim/method/dataset/limitation 提取、冲突证据处理和引用；必须区分作者原始结论与 agent 推断。

3. **`ocean-dataset-diagnosis`**

   在计算前检查 longitude convention、latitude order、depth positive direction、calendar、frequency、units、fill value、land mask、grid/staggering、resolution、chunk 和 missing ratio，产出结构化 DatasetDiagnosisArtifact。

4. **`ocean-analysis-design`**

   当任务确实需要计算时，要求明确 selection、aggregation、baseline、area/cell-volume weighting、seasonality、autocorrelation、uncertainty、sensitivity tests 和运行前 validation assertions，再进入 coding loop。

5. **`hypothesis-experiment-design`**

   要求假设可证伪，列出 competing explanations、baseline、metric、confounders、success/failure criteria 和 stopping rule。

6. **`ocean-physical-consistency-review`**

   从 units、dimensions、数量级、方向/符号、守恒、grid metrics、boundary effects、land contamination 和已知季节/环流结构审查结果，但不重新实现算法。

7. **`ocean-map-and-figure-review`**

   审查 projection、extent、coastline、bathymetry、color scale、panel comparability、vector density、depth axis、mask、marker/region/transect 与数据 selection 的一致性。

8. **`claim-grounded-writing`**

   要求重要 claim 链接到 paper/data/experiment/figure，并明确标记 observation、inference、hypothesis、speculation 和 conclusion。

9. **`reproducibility-audit`**

   检查数据版本、代码、参数、环境、随机种子、失败 attempts、checksum、verification 和复跑说明。

后续按真实任务加入 `ocean-model-observation-comparison` 等 skill，但不能因为某个分析失败就新增 method-specific skill。

### 4.7 Ocean References

第一版 reference 目录建议：

```text
references/
  methods/
    anomaly.md
    trend.md
    transport.md
    eof.md
    mixed-layer-depth.md
    water-mass.md
  data/
    cf-conventions.md
    grids-and-coordinates.md
    calendars.md
    common-variables-and-units.md
    common-datasets.md
  coding/
    xarray.md
    cartopy.md
    gsw.md
    large-array-practices.md
  review/
    physical-consistency.md
    map-quality.md
    reproducibility.md
```

Reference 可以包含定义、公式、适用条件、常见错误和短小示例，但不能包含要求 agent 逐步调用固定 tools 的执行 workflow。

Skill 通过 OpenHarness `skill` tool 按需加载；system prompt 只注入当前 RuntimeProfile 已启用 skill 的名称和简介，完整内容仅在当前研究动作匹配时进入 context。Skill 可以引导 agent 检索相关 references，并产出 AnalysisPlanArtifact、DatasetDiagnosisArtifact 或 ReviewArtifact。

Skill 还受 capability gate 约束：资源可以随 wheel 打包，但对应的 source contract/tool/store 尚未启用时，不得进入 model-visible registry。例如 `literature-evidence-synthesis` 在 OD-05 关闭和 Literature Mode 启用前不暴露，remote-data references 在 OD-04 关闭前不得引导实际下载。

每个 skill 至少包含：

```text
when_to_use
research_objective
questions_to_resolve
evidence_requirements
process_checkpoints
expected_artifacts
quality_gates
stop_or_escalation_conditions
relevant_references
```

Skill 中禁止出现：固定十阶段分析流程、必须调用的 `compute_*` 函数、不可修改的算法代码，以及把模型输出直接标为 approved 的规则。

## 5. TUI 与后端协议 v2

协议是第一批实现内容，不允许先写 UI 再猜后端字段。

### 5.1 Transport 与兼容边界

- Ocean TUI spawn Ocean Python backend，并可请求 backend 启动本地 Plot Studio graphical server。
- TUI 向 backend stdin 写一行一个 UTF-8 JSON request；backend stdout 只写 `OHJSON:<json>\n` protocol frame。
- Plot Studio 通过 loopback WebSocket 发送和接收相同的 Protocol v2 envelope，不另造一套 domain API。
- graphical server 只监听 `127.0.0.1` 随机端口，使用一次性 bootstrap token、session-scoped client capability 和 Origin 校验；不得默认暴露到局域网，不在页面 URL、server log 或 diagnostics 中持久化 token。
- Plot Studio 的 artifact HTTP endpoint 同样验证 client capability，只解析 backend 已注册的 `ocean://` URI，拒绝任意本地路径、symlink escape 和 MIME sniffing，并设置限制性 CSP。
- backend 日志写 stderr；frontend 可以显示非协议 stdout 为诊断信息，但绝不能从中推导状态。
- 大文件、图片和数组不进入 JSONL；只传 artifact URI、MIME、大小和 checksum。
- `oh` v1 schema 保持原样；`ocean` v2 使用独立 model 和独立 frontend hook。
- v2 schema、request lifecycle、sequence 和 revision 语义不依赖 stdio 或 WebSocket transport。
- 第一版一个 session 只支持一个 agent foreground request，但 map selection 等只读 UI request 可以并发；协议从一开始支持 request identity 和 cancellation。
- agent request 内的 `analysis_run_execute` 是有上限的 foreground tool operation，原 request 在 execute/debug loop 结束前保持 active；不把计算丢给不可审计的内存后台任务。
- 对已准备 run 直接发送 `analysis.attempt.start` 时，该 request 在当次 attempt 进入 terminal state 后才发 terminal event，不在“已排队”时提前 completed。
- v1 不支持 backend 退出后透明 resume 进程；已完成 attempt 始终可恢复，运行中 attempt 标记 `interrupted`，用户必须显式创建新 attempt。

handshake 后 backend 为每个 transport client 绑定 server-side principal 和 capability set：

- TUI client：agent submit、permission/question respond、workspace mutation、run control 和 human review。
- Plot Studio client：artifact/map read、受 revision 保护的 scene mutation 和 human review；无 agent submit 和 execution permission respond。
- model tools：只能通过 RuntimeProfile 中的 backend service tools 产生 proposal/mutation，不持有 TUI/Plot Studio human principal，也不能直接发 `review.submit`。

router 在解析 payload 后、进入 service 前检查 capability，不相信 `client_id`、request type 或 payload 中的 actor 声明。

### 5.2 Request envelope

```json
{
  "protocol_version": 2,
  "request_id": "req_01J...",
  "type": "figure.request.create",
  "payload": {
    "text": "Plot monthly SST averaged over 120E-140E, 20N-35N"
  },
  "context": {
    "session_id": "ses_01J...",
    "workspace_id": "ws_default",
    "client_id": "client_tui_01J...",
    "active_mode": "figure"
  }
}
```

规则：

- `protocol_version` 固定为整数 `2`。
- `request_id` 必填且对 mutating request 全局唯一，不因 backend session 重启而重置。
- `type` 使用小写 dotted namespace。
- `payload` 必须是 object，即使为空也写 `{}`。
- `context.session_id` 在 handshake 后必填。
- `context.client_id` 由 handshake 分配，用于 request-local event routing；backend 把 transport 绑定的 principal/capabilities 写入 RequestRecord，客户端不能自行声明 `created_by` 或提升能力。
- mutating request 可带 `expected_workspace_revision`，用于阻止 stale write。
- backend 在发送 `request.accepted` 前先持久化 RequestRecord，至少保存 `request_id`、principal、type、canonical payload hash、state 和 terminal result/event ref。
- canonical hash 覆盖 `protocol_version`、`type`、`payload`、`workspace_id` 和 `expected_workspace_revision`；`session_id/client_id` 不用于改变操作语义，但 replay 必须通过原 principal 的 capability 检查，不向其他 principal 泄漏 terminal payload。
- 重复 `request_id` + 相同 payload hash 不能重复执行 mutation；backend 重放已知 terminal result，或返回持久化的 `in_progress/interrupted` 状态。相同 ID 但 payload 不同返回 `request_id_conflict`。

### 5.3 Event envelope

```json
{
  "protocol_version": 2,
  "event_id": "evt_01J...",
  "session_id": "ses_01J...",
  "workspace_id": "ws_default",
  "request_id": "req_01J...",
  "sequence": 42,
  "type": "analysis.attempt.progress",
  "timestamp": "2026-07-10T08:30:00Z",
  "payload": {
    "analysis_run_id": "run_01J...",
    "attempt_id": "attempt_0003",
    "phase": "execute",
    "completed": 3,
    "total": 5,
    "message": "Rendering figure"
  }
}
```

规则：

- `event_id` 全局唯一。
- `session_id` 标识事件所属 backend session；handshake 失败事件可为 `null`。
- `workspace_id` 对 workspace/domain event 必填，纯 session event 可为 `null`。
- `request_id` 对 request 触发的事件必填；session 自发事件可为 `null`。
- `sequence` 在一个 transport connection 内严格递增；跨 TUI/Plot Studio 的一致性使用 `event_id` 和 workspace revision，而不是假设两个 client 收到完全相同的 stream。
- `timestamp` 使用 UTC RFC 3339。
- event 只能有与 `type` 对应的 payload，禁止“一个 class 上堆满 optional 字段”。
- `turn_id`、`tool_call_id` 和 `analysis_run_id` 属于各自 typed payload，不能复用一个含义模糊的通用 `run_id`。
- 所有事件 reducer 必须对未知 event type 安全忽略并记录诊断。

### 5.4 Request 生命周期不变量

每个被接受的 request 必须遵守：

```text
request.accepted
  -> 0..N progress/domain/tool/assistant events
  -> request.completed | request.failed | request.cancelled
```

约束：

- 三个 terminal event 中必须且只能出现一个。
- terminal event/result 必须先写入 RequestRecord 再发送；断线重试只能重放该结果，不能重做 domain mutation。
- `assistant.turn.completed` 不是 request terminal event。
- frontend 的 busy 状态只由 request accepted/terminal events控制。
- 每个 tool call 使用唯一 `tool_call_id`；并行同名工具也能区分。
- 每个 model-originated mutating tool call 使用 backend 生成的 `operation_id` 绑定 request/turn/tool_call，并在同一 domain transaction 持久化 operation checkpoint 和 ToolCallRecord。同一 operation 重放返回旧 tool result，不再执行 mutation。
- `request.cancel` 是 best effort；取消后 backend 必须终止模型 stream 和它启动的 foreground subprocess，并保存已完成 attempts。
- `analysis.attempt.cancel` 解析到该 attempt 的 execution handle；若它属于 agent request，当前 tool 返回 structured cancellation 且原 request 进入 `request.cancelled`，不再让模型在取消后继工作。取消 attempt 不删除 AnalysisRun，run 回到 `ready`。
- permission/question 交互不会结束原 request，响应通过 `interaction.respond` 关联。
- permission/question 只路由到发起 agent request 的 TUI client；Plot Studio v1 不能代答执行权限。
- TUI 与 Plot Studio 都可提交 `review.submit`，backend 根据已认证 session/client 将 actor 记录为 `user:local`；agent review 只能记录为 proposal。
- `interaction.respond`、`request.cancel` 和 `system.shutdown` 是 control requests，transport 必须在 foreground request 执行期间继续读取和处理，不能排在普通工作队列之后。
- backend shutdown 前先发送 `system.shutdown`，并尽力终止 child process、flush DB 和 event journal。
- 每个 foreground request、attempt 和 subprocess 都受 versioned resource policy 约束；到达 hard limit 时不能用一次通用 permission 继续无界执行。
- RuntimeProfile 为每个 agent request 声明 max model turns、input/output tokens、tool calls、wall time 和 optional cost budget。预算耗尽时写 `budget_exhausted` terminal result，取消活动 attempt 并将 run 恢复为 `ready`；用户可开启新 request 继续，但不在原 request 中静默超额。

### 5.5 基础 request 类型

| Request | 用途 | 是否修改状态 |
| --- | --- | --- |
| `system.handshake` | 协商 protocol version、client version 和 capabilities | 否 |
| `session.open` | 打开或恢复 session | 是 |
| `session.submit` | 提交普通聊天/agent 任务 | 是 |
| `request.cancel` | 取消一个 foreground request | 是 |
| `request.status.get` | 读取持久化 request/terminal result，用于断线恢复 | 否 |
| `system.shutdown` | 关闭 backend | 是 |
| `interaction.respond` | 回答 permission、question 或 selector | 是 |
| `mode.set` | 切换 literature/exploration/figure/writing | 是 |
| `workspace.open` | 打开或初始化 workspace | 是 |
| `workspace.snapshot.get` | 请求完整快照 | 否 |
| `artifact.list` | 按 type/status/link 查询 | 否 |
| `artifact.get` | 读取一个 artifact version | 否 |
| `plot_studio.open` | 启动或聚焦本地 graphical Plot Studio | 否 |
| `map.scene.get` | 获取当前 MapScene、layers、features 和 linked plots | 否 |
| `map.scene.update` | 更新 scene 中的 layer refs、features 与 viewport | 是 |
| `figure.request.create` | 从自然语言创建 FigureRequest | 是 |
| `figure.spec.revise` | 基于 feedback 创建 FigureSpec 新版本 | 是 |
| `analysis.attempt.start` | 为已准备 AnalysisRun 创建并执行一次 attempt | 是 |
| `analysis.attempt.cancel` | 取消一次活动 attempt，保留 run/work/旧 attempts | 是 |
| `analysis.run.abandon` | 用户显式结束不再继续的 run，不删除证据 | 是 |
| `figure.versions.compare` | 比较 FigureArtifact 版本 | 否 |
| `review.submit` | 提交人工 review | 是 |
| `report.artifact.add` | 把 exact artifact ref 加入报告草稿，附当前 review/verification/impact gate | 是 |

### 5.6 基础 event 类型

Transport/session：

- `system.ready`
- `system.shutdown`
- `request.accepted`
- `request.completed`
- `request.failed`
- `request.cancelled`
- `system.error`
- `interaction.requested`
- `interaction.resolved`

Agent/transcript：

- `transcript.item.appended`
- `assistant.delta`
- `assistant.turn.completed`
- `tool.call.started`
- `tool.call.completed`
- `context.compaction.progress`

Ocean domain：

- `workspace.snapshot`
- `workspace.changed`
- `active_context.changed`
- `plot_studio.ready`
- `map.scene.snapshot`
- `map.scene.updated`
- `map.layer.ready`
- `map.feature.upserted`
- `linked_plot.ready`
- `artifact.created`
- `artifact.version.created`
- `artifact.status.changed`
- `figure.spec.updated`
- `analysis.run.ready`
- `analysis.run.checking`
- `analysis.run.checks_passed`
- `analysis.run.checks_failed`
- `analysis.run.abandoned`
- `analysis.attempt.queued`
- `analysis.attempt.started`
- `analysis.attempt.progress`
- `analysis.attempt.succeeded`
- `analysis.attempt.rejected`
- `analysis.attempt.failed`
- `analysis.attempt.timed_out`
- `analysis.attempt.resource_limited`
- `analysis.attempt.cancelled`
- `analysis.attempt.interrupted`
- `analysis.attempt.source_changed`
- `verification.completed`
- `review.requested`
- `review.submitted`
- `report.updated`
- `suggestion.created`

### 5.7 一次完整 Figure request 的事件序列

```text
frontend -> figure.request.create(req_1)
backend  -> request.accepted(req_1)
backend  -> transcript.item.appended(req_1)
backend  -> assistant.delta(req_1, turn_1) ...
backend  -> tool.call.started(req_1, tc_1, figure_spec_version_create)
backend  -> artifact.created(req_1, FS001.v1)
backend  -> tool.call.completed(req_1, tc_1)
backend  -> assistant.turn.completed(req_1, turn_1)
backend  -> workspace.changed(req_1, revision 7 -> 8)
backend  -> request.completed(req_1)
```

frontend 不从 assistant 文本中解析 `FS001`，而从 `artifact.created` 和 `workspace.changed` 更新状态。

### 5.8 错误模型

`request.failed.payload` 至少包含：

```json
{
  "code": "analysis_exit_nonzero",
  "message": "Analysis process exited with code 1",
  "recoverable": true,
  "details": {
    "analysis_run_id": "run_01J...",
    "attempt_id": "attempt_0003",
    "stderr_uri": "ocean://runs/run_01J.../attempts/attempt_0003/stderr.log"
  }
}
```

错误码必须稳定，至少区分：

- `invalid_request`
- `unsupported_protocol`
- `request_id_conflict`
- `request_interrupted`
- `workspace_revision_conflict`
- `permission_denied`
- `interaction_timeout`
- `model_error`
- `tool_error`
- `budget_exhausted`
- `analysis_timeout`
- `analysis_resource_limit_exceeded`
- `analysis_exit_nonzero`
- `dependency_missing`
- `unsafe_execution`
- `source_changed_during_run`
- `verification_failed`
- `artifact_not_found`
- `store_error`
- `cancelled`

### 5.9 Snapshot、revision 与恢复

- workspace 维护递增 `revision`。
- 每个 domain mutation 在同一 DB transaction 中写状态和 event。
- domain mutation events 广播给 TUI 和 Plot Studio；assistant delta 等 request-local stream 默认只发给发起 request 的 client。
- 每个 transport adapter 在发送时分配自己的连续 sequence；同一个持久化 domain event 保持相同 `event_id`。
- mutation event 带 `previous_revision` 和 `workspace_revision`。
- frontend 发现 sequence gap 或 revision gap 时发送 `workspace.snapshot.get`。
- `workspace.snapshot` 是完整、可替换的状态，不要求 frontend 重放所有历史事件。
- event journal 用于审计和调试，不把第一版做成纯 event-sourcing 系统。
- backend 重启后从 DB 恢复 workspace、artifacts、open runs 和 reviews；处于 `running` 的旧 attempt 标记为 `interrupted`，对应 AnalysisRun 回到 `ready`。
- RequestRecord 与 terminal result 同样从 DB 恢复；客户可使用原 `request_id` 或 `request.status.get` 获取确定结果。
- domain mutation、对应 EventRecord 和 request operation checkpoint 在同一 transaction 提交；commit 后、broadcast 前崩溃时，重连通过 snapshot/event ref 恢复，不重做 mutation。
- 未完成 request 在重启后不自动恢复模型 stream 或 Python process；backend 为其写入 `request_interrupted` terminal result，用户可从保存的 work/attempt 状态发起新 request。

### 5.10 Schema 治理与 contract tests

- Pydantic request/event union 使用 discriminator=`type`。
- CI 导出 `protocol/v2/schema/*.json`。
- TypeScript 类型由 schema 生成，不手写宽泛的 `type: string`。
- Python 和 TypeScript 共用 `protocol/v2/fixtures/valid` 与 `invalid` fixtures。
- CI 检查生成文件无 diff。
- protocol breaking change 必须增加新 version，不能在 v2 中静默改变既有字段语义。

## 6. TUI 与 Graphical Plot Studio 产品结构

### 6.1 主区域

产品包含五个主区。TUI 承担会话和研究状态入口，Plot Studio 由 TUI 打开本地 graphical view：

- **Workspace**：研究问题、active hypothesis/dataset/figure、open reviews、recent runs 和下一步建议。
- **Literature**：papers、claims、methods、datasets、limitations 和 gaps。
- **Hypotheses**：observations、候选假设、可证伪条件、实验和状态。
- **Plot Studio**：交互地图、spatial layers、map-linked plots、FigureSpec、code、verification 和版本比较。
- **Report Builder**：exact artifact refs、review/verification/impact gates 与报告结构。

建议布局：

```text
+----------------+--------------------------------+----------------------+
| Workspace/Nav  | Conversation + Activity        | Artifact Inspector   |
| active context | agent/tool/run event stream    | spec/code/checks     |
+----------------+--------------------------------+----------------------+
| Composer / command palette / interaction modal                         |
+--------------------------------------------------------------------------+
```

窄终端时按 `Nav -> Main -> Inspector` 切换，不强行显示三栏。

TUI 中的 `Open Plot Studio` 命令打开浏览器中的本地 session URL。关闭 graphical view 不会终止 backend 或丢失 workspace；用户仍可从 TUI 查看 run、code 和 artifact metadata。

### 6.2 Frontend stores

frontend 至少维护：

- `transport`：ready、sequence、active request、connection error。
- `session`：model、permissions、usage、capabilities。
- `transcript`：用户、assistant、tool 和 system activity。
- `workspace`：snapshot、revision 和 active mode/context。
- `artifacts`：summary index 和 selected artifact detail。
- `runs`：AnalysisRun lifecycle、当前/selected attempt 和每次 attempt terminal state。
- `interactions`：permission/question/selector modal。
- `reviews`：open review 和当前 review draft。
- `mapScene`：当前 scene、viewport、layer refs 和内嵌 features。
- `linkedPlots`：地图 feature 对应的 time series、T-S、profile、section 等 plot summaries。

TUI 和 Plot Studio 可以维护不同的 view state，但共享 generated protocol types 和 domain reducers。所有研究状态只能通过 typed protocol events 更新；日志文本、assistant 文本和文件名都不是状态源。

### 6.3 TUI 与 graphical view 的边界

Ink 终端不能可靠提供可点击地图、hover、layer opacity 和图表联动，因此第一版采用：

- TUI 显示缩略信息、尺寸、format、verification 和路径。
- graphical Plot Studio 显示交互地图、spatial field overlay、markers 和 linked plots。
- 静态 publication FigureArtifact 仍可在系统图片查看器中打开。
- 检测 Kitty/iTerm2 等能力后可选 inline preview，但不是 MVP 的验收前提。
- protocol 只传 artifact refs、URI 和轻量 metadata，不传 base64 图片或完整大数组。

## 7. Research domain 与 Artifact Store

### 7.1 Artifact 与 operational record 分开

版本化科研 artifacts：

- `ProjectContextArtifact`
- `PaperArtifact`
- `ClaimArtifact`
- `ObservationArtifact`
- `HypothesisArtifact`
- `DatasetArtifact`
- `DatasetDiagnosisArtifact`
- `FigureRequestArtifact`
- `FigureSpecArtifact`
- `AnalysisPlanArtifact`
- `FigureArtifact`
- `SpatialLayerArtifact`
- `SelectionArtifact`             # 具有科研含义的 Point/Polygon/LineString selection
- `LinkedPlotArtifact`
- `MapSceneArtifact`
- `ExperimentArtifact`
- `ReviewArtifact`
- `DecisionArtifact`
- `ReportArtifact`

运行记录不是普通 artifact：

- `SessionRecord`
- `RequestRecord`
- `AnalysisRunRecord`
- `ToolCallRecord`
- `EventRecord`
- `InteractionRecord`
- `DisclosurePolicyRecord`

run 可以产生一个或多个 artifacts，artifact 通过 provenance 引用 run。

### 7.2 通用 artifact 字段

```text
artifact_id             # 稳定 ID，例如 fig_01J...
version                 # 正整数，不拼进 artifact_id
artifact_type
schema_version
title
summary
created_at
created_by
supersedes_version
content                 # type-specific JSON
intrinsic_links         # 创建时已知且不可变的输入/生成关系
provenance              # 创建时的运行证据
checksum
```

引用统一使用：

```json
{"artifact_id": "fig_01J...", "version": 2}
```

不再让 `active_figure_id` 同时承担 ID 与 version；workspace 使用 `active_figure_ref`。

`ArtifactVersion` 创建后完全不可变。当前状态单独保存在 SQLite projection 中：

```text
artifact_ref
lifecycle_state
review_state
verification_state
impact_state
impact_reasons
updated_at
source_event_id
```

projection 由 ArtifactVersion、ReviewArtifact、VerificationRecord、typed links 和状态事件计算。用户 approve/reject、报告引用、upstream 变化或 archive 都不能改写旧 manifest。

### 7.3 状态词汇

Artifact lifecycle projection：

```text
draft | available | superseded | archived | tombstoned
```

`rejected / failed / timed_out / resource_limited / interrupted` 属于 Attempt 状态，`abandoned` 属于 AnalysisRun；它们都不是已创建 ArtifactVersion 的 lifecycle。

Review status 独立：

```text
not_requested | pending | approved | changes_requested | rejected
```

Machine verification 独立：

```text
not_run | pass | warning | fail
```

Dependency impact 独立：

```text
current | stale | invalidated | source_unavailable
```

语义：

- upstream 仅出现新 version 时，旧证据链仍指向精确版本，但 active workspace/report 中的下游对象可标为 `stale`。
- upstream 被 rejected/tombstoned，或其关键假设被 DecisionArtifact 推翻时，依赖它的结论性 artifact 标为 `invalidated`。
- `reference_only` 数据已变化且旧字节无法重新访问时，相关下游结果标为 `source_unavailable`；已 materialize 的 snapshot 不因原路径变化而失效。
- impact 传播生成原因链，保留起点 artifact/event refs；用户可明确决定继续引用 stale 结果，但不能隐藏标记。

禁止用一个 `status` 字段同时表达生成进度、机器检查、人工审批和依赖影响。

创建时的 `derived_from`、`generated_by_run`、`implements_spec` 属于 intrinsic links，可写入 manifest；后续增加的 `reviews`、`included_in_report`、`displayed_in_scene` 等 contextual links 作为追加式 DB relation/event 保存。

### 7.4 Typed links

第一版关系：

- `derived_from`
- `uses_dataset`
- `generated_by_run`
- `implements_spec`
- `implements_plan`
- `displayed_in_scene`
- `located_at`
- `opens_plot`
- `supports_claim`
- `contradicts_claim`
- `tests_hypothesis`
- `reviews`
- `supersedes`
- `included_in_report`
- `motivates`

link 必须能按 source、target、relation 双向查询，并为 impact traversal 声明是否传播 stale/invalidated。删除 artifact 默认只做 tombstone，不级联删除其证据链。ReportArtifact 必须 pin 精确 refs，每次 preview/export 都重算被引用对象的 review、verification 和 impact projection。

### 7.5 Project-local 存储布局

```text
<project>/.openharness/ocean/
  workspace.sqlite3
  environment.lock.json
  artifacts/
    figure/
      fig_01J.../
        v0001/
          manifest.json
          preview.png
          figure.pdf
        v0002/
          manifest.json
          preview.png
          figure.pdf
    spatial_layer/
      layer_01J.../
        v0001/
          manifest.json
          layer.json
          overlay.png
          field.nc
    selection/
      selection_01J.../
        v0001/
          manifest.json
          geometry.geojson
    linked_plot/
      plot_01J.../
        v0001/
          manifest.json
          data.json
          preview.png
    map_scene/
      scene_01J.../
        v0001/
          manifest.json
          scene.json
  datasets/
    references/                 # 外部路径、identity 和可重跑等级
    snapshots/                  # 显式 materialize 的 immutable input/subset
    derived/
  runs/
    run_01J.../
      request.json
      analysis_plan.json
      inputs.json
      spec.json
      work/
        analysis.py             # agent 当前编辑副本
        verify.py
      attempts/
        attempt_0001/
          analysis.py           # 执行时不可变快照
          verify.py
          stdout.log
          stderr.log
          environment.json
          verification.json
          outputs/
            manifest.json
            result.nc
            figure.png
  exports/
  staging/
  quarantine/
  cache/
```

SQLite 保存：workspace、requests、tool calls、disclosure policy versions/audits、artifact versions、links、runs、attempts、reviews、state/impact projections、commit intents、events 和 schema migrations，并且是这些 metadata/state 的唯一权威源。大文件保存在文件系统，DB 中只存 project-relative URI、MIME、size 和 checksum。

v1 的本地安全假设是单一 OS user，不是多用户 server。backend 在支持的平台上以 0700 创建 workspace/run directories、以 0600 创建 DB、logs 和 manifests，并不在 store 中保存 API keys。第一版不声称提供 app-level encryption at rest；依赖 OS account/disk encryption，portable export 必须重新做 disclosure/path audit。

version directory 中的 `manifest.json` 是不可变、可移植的内容说明和导出材料，不覆盖 SQLite 中的 review、projection 或 event 状态。发生冲突时以 SQLite 为准；显式 export 会重新验证 DB、manifest 和 checksum 的一致性。

写入流程使用可幂等 commit intent：

1. 在 staging directory 写文件并计算 checksum。
2. 验证 manifest schema。
3. 在第一个 SQLite transaction 写入 `artifact_commit_intent`，包含唯一 operation ID、request/tool_call ID、staging/target URI、manifest hash 和预期 checksums。
4. 原子 rename 到目标 version directory。
5. 在第二个 SQLite transaction 幂等插入 version、links、event 和 workspace revision，并将 intent 标记 committed。
6. 启动恢复只按 intent 做确定性操作：target 存在且 checksum 匹配则幂等 finalize；staging 完整则继续 rename/finalize；不匹配则移入 quarantine 并生成审计事件。不根据目录名猜测重新提交。

### 7.6 Provenance 最低要求

每个 derived dataset、figure 和 metric 至少记录：

- 输入 artifact refs、原始 URI 和 checksum。
- 每个输入的 materialization level、identity strength、pre/post-run fingerprint 和 source-availability status。
- 使用的变量、坐标、单位、时间范围、空间范围和深度范围。
- 变换参数、mask、aggregation、baseline 和 weighting 方法。
- code URI 与 code checksum。
- run/attempt ID、interpreter path/version、完整 environment manifest ref/fingerprint 和平台。
- runtime profile/version、sandbox backend/policy、resource limits 和 execution trust level。
- started/ended time、exit code、stdout/stderr URI。
- output URI、MIME、size 和 checksum。
- machine verification report ref、verifier origin、independence level 和 evidence level。
- 影响该结果的 system prompt、skill 和 reference resource versions。
- 对外部模型实际披露的 metadata/sample/diagnostic 类型、字节/点数、policy version、provider 和 approval event refs；审计表不重复保存原始敏感内容。
- 创建者是 user、agent、tool 还是 imported process。

## 8. Memory 与 Context 设计

Ocean Partner 必须明确区分三层“记忆”：

### 8.1 Conversation context

- 由 `QueryEngine` 和 `services.compact` 管理。
- 可以 microcompact、collapse 或 summarize。
- 只负责让当前 agent loop 连贯。
- 不是科研事实数据库。

### 8.2 Research working state

- 由 workspace、artifact store、runs、reviews 和 decisions 管理。
- 结构化、可查询、不可被 conversation compaction 删除。
- active context 只保存 artifact refs，不复制完整对象。
- 每次模型调用由 `OceanContextBuilder` 选择最小必要 snapshot 注入 system/context prompt。

建议 context budget：

```text
workspace summary              <= 1,000 tokens
active artifact summaries      <= 2,000 tokens
recent decisions/reviews       <= 1,500 tokens
active skill/reference excerpts <= 2,500 tokens
retrieved provenance/details   按任务需要，默认 <= 3,000 tokens
conversation history           交给 OpenHarness compaction
```

### 8.3 Durable preferences / memory

- 保存用户绘图偏好、常用数据源、命名规范和项目长期约束。
- 不自动把所有对话写成 memory。
- 用户明确确认或系统提出可审查的 memory proposal 后再写入。
- Ocean 项目偏好建议 project-local 保存；OpenHarness 现有全局 hash memory 可作为兼容输入，而不是 artifact store。
- 每条 memory 必须包含 `scope=project|global`、source request/proposal、confirmed_by、created_at、last_used_at 和 optional expiry；project scope 不能注入其他 workspace。
- 全局 memory 不保存数据路径、未公开研究结论、credential hint 或项目敏感字段；这些内容只能留在 project-local structured state。
- TUI 提供 memory list/source/last-used/delete 和“本轮不使用”控制；删除后从后续 context assembly 消失，审计日志只保留不含原文的 deletion event。

### 8.4 Context assembly 规则

模型每一轮只获得：

- 始终生效的 research integrity / permission / approval policies。
- 当前 workspace summary。
- active artifact 的必要字段。
- 当前 request 对应的 run/spec/review。
- 与用户问题相关的少量 artifacts。
- 明确的状态词汇和 validation policy。
- 当前动作已触发的 skill 内容和少量相关 reference excerpts，并记录 resource version。

模型需要更多数据时调用 `artifact_query`；需要过程规范时调用 `skill`，需要方法知识时检索 references。不能把整个 workspace、全部 skills 或全部 Ocean 方法文档自动塞进 system prompt。

`OceanContextBuilder` 在组装后、调用 provider 前应用 ModelDataDisclosurePolicy，记录内容类型与大小而不复制敏感原文。compaction 只能压缩已允许进入 conversation 的内容，不能从本地 full logs/artifacts 重新取数补摘要。

## 9. 数据分析运行与代码正确性

### 9.1 计算任务的 Code-transparent 原则

Ocean Partner 不把科学分析预先编码成 anomaly、trend、transport、EOF 等 skills、recipes 或 model-visible tools。面对具体研究问题，agent 应使用 OpenHarness 的读写和执行能力编写真实 Python 代码：

```text
用户提出科学问题
  -> agent 检查数据结构和样本
  -> 声明分析假设与验证标准
  -> 创建 AnalysisRun
  -> 编写 analysis.py / verify.py
  -> 执行并读取错误或结果
  -> 修改代码并重跑
  -> 检查输出和 provenance
  -> 发布 artifact
  -> 用户审查科学解释
```

这里的 kernel 是 Python runtime 与成熟科学库，例如 xarray、NumPy、SciPy、Pandas、Matplotlib、Cartopy 和 GSW。第一版不自建 `compute_anomaly()`、`compute_trend()`、`compute_transport()` 等隐藏算法层。

约束：

- 每次分析使用的算法都必须能在 run directory 的代码中看到。
- agent 可以直接组合第三方库，也可以编写当前任务需要的辅助函数。
- 不要求任务匹配预定义 recipe 才能执行，开放性研究问题必须走同一条 coding loop。
- 重复且已经经过多个真实任务验证的代码，可以提升为普通、带测试的 Python utility module；它仍是可读代码，不自动变成 skill 或 model tool。
- 示例代码、论文复现代码和 plotting snippets 可以作为 reference 被检索和修改，但不能成为不可绕过的固定工作流。

utility promotion 不是隐式演化路径。候选代码必须通过 ADR/review，具有稳定的 unit/coordinate/calendar 合同、synthetic reference tests、版本化 API 和适用边界。run 必须记录导入 utility 的 version 与 source checksum；未达到这些条件的重复代码仍留在具体 AnalysisRun 中。

### 9.2 为什么仍然需要独立 AnalysisRun

code-transparent 不等于在项目根目录随意生成脚本。AnalysisRun 为模型编写的代码提供隔离、可复现性和证据链：

```text
FigureSpec / ExperimentPlan
  -> AnalysisRun created
  -> code written by agent
  -> preflight
  -> controlled execution
  -> iterative debugging
  -> outputs captured
  -> declared checks executed
  -> artifact publication
  -> human review
```

建议目录：

```text
runs/run_01J.../
  request.json
  analysis_plan.json
  inputs.json
  work/
    analysis.py
    verify.py
  attempts/
    attempt_0001/
      analysis.py
      verify.py
      stdout.log
      stderr.log
      environment.json
      verification.json
      outputs/
        manifest.json
        result.nc
        figure.png
```

`analysis_plan.json` 是对应 AnalysisPlanArtifact version 的本地快照。`work/` 是 agent 可继续编辑的副本；每次 execute 先把代码快照到新的 `attempt_XXXX/`，随后只在该 attempt 内写日志和输出。`analysis.py`、`verify.py`、每个 attempt 和 plan snapshot 都是一等研究记录，必须随结果一起保存。

### 9.3 Run 状态机

AnalysisRun 是一个 AnalysisPlan 下的可编辑 work area 和 attempts 容器，不因一次 syntax error/timeout 就整体变成不可重试的 failed run：

```text
draft -> ready -> active -> checking -> checks_passed
          ^        |            |
          |        |            +-> checks_failed -> ready
          |        |
          +--------+  attempt non-success / user continues debugging

ready | active | checking | checks_failed -> abandoned
```

每次 execution attempt 有独立状态：

```text
created -> preflight -> queued -> running -> succeeded
              |                     |
              +-> rejected          +-> failed
                                    +-> timed_out
                                    +-> resource_limited
                                    +-> cancelled
                                    +-> interrupted
                                    +-> source_changed
```

`rejected` 表示未启动子进程，例如 dependency/sandbox/input policy 不满足。其他 terminal states 都保留实际执行证据。非成功 attempt 结束后 AnalysisRun 回到 `ready`；用户可显式重试或 `abandon`。只有选定一个 `succeeded` attempt 后 run 才进入 `checking`。

状态转换必须由 `RunService` 执行并写 event，模型不能直接修改数据库状态。agent 可以反复编辑同一 run 的代码并产生 execution attempts；每次 attempt 单独记录日志、代码 checksum 和结果，不能覆盖失败证据。`checks_failed -> ready` 只允许在不改变冻结 assertions/科学方法时修复实现；方法改变必须创建新 AnalysisPlan/run。`checks_passed` 只表示已运行的检查通过，状态机和 UI 都不使用笼统的 `verified/correct` 描述。

### 9.4 Coding loop 与 Run pipeline

1. **Inspect**：读取 dataset metadata 和受限样本，确认变量、维度、坐标、单位、calendar、chunk 和缺失情况。
2. **Plan checks first**：在看到最终结果前写 `analysis_plan.json`，声明科学问题、假设、方法、预期输出和可执行检查。
3. **Create run**：创建 run ID、冻结输入 refs/materialization levels、用户 request、FigureSpec/ExperimentPlan、RuntimeProfile 和资源策略。
4. **Code**：使用只能写 run `work/` 的 scoped `read_file`、`write_file`、`edit_file` 编写 `analysis.py` 和 `verify.py`；不向模型暴露通用 Bash。
5. **Preflight**：检查 Python syntax、entrypoint、输入路径、pre-run fingerprint、输出声明、依赖和 sandbox/resource capability。
6. **Authorize**：展示冻结的输入、资源上限和执行策略；permission 可以允许本次运行，但不能突破 hard resource limit、开启任意网络或降级 publish trust gate。
7. **Execute**：在 sandbox 子进程组中运行，设置 cwd、timeout、CPU/memory/disk/process/output limits、环境白名单、只读输入 mounts 和唯一可写输出目录。
8. **Debug**：把结构化错误和日志尾部返回 agent；agent 修改代码后创建新 attempt。
9. **Capture**：保存完整 stdout/stderr、exit code、duration、environment、code checksum、post-run input fingerprints 和 backend 独立枚举的输出列表。
10. **Verify**：运行可信的通用检查和 agent 预先声明的 `verify.py`，记录每个 check 的 origin/independence/evidence level，再检查结果 metadata。
11. **Publish**：仅对 trusted sandboxed attempt，在 schema、provenance、source-stability 和 output-safety 要求满足后，事务性创建 derived data、figure 或 metric artifact version。
12. **Review**：用户批准、要求修改或拒绝；机器 pass 不能替代人工科学判断。

### 9.5 第一版 infrastructure tools

Agent 可见工具只提供横向能力，不提供具体科学算法：

- `artifact_query`：只读查询 artifact、link、run、code、verification 和 review。
- `dataset_inspect`：读取本地 NetCDF/Zarr metadata，不执行 anomaly/trend 等分析。
- `dataset_sample`：在点数和字节数上有硬限制地读取小样本，帮助 agent 理解真实坐标与数值。
- `figure_spec_version_create`：创建或修订“最终图应表达什么”的需求 contract，不规定实现算法。
- `analysis_run_create`：创建隔离 run directory，冻结 request、input refs 和执行策略。
- `analysis_run_execute`：执行 agent 编写的 entrypoint，保存每次 attempt 的完整本地记录，只向模型返回 disclosure-safe 结构化状态。
- `analysis_diagnostic_excerpt`：当结构化错误不足以调试时，请求一段有行/字节上限的 stdout/stderr 片段；backend 先脱敏和检测大块数值/表格，再根据 workspace policy 自动拒绝或请求用户确认。
- `analysis_output_inspect`：检查 NetCDF/CSV/JSON/PNG/PDF 输出是否存在、可读、非空，并提取 metadata。
- `analysis_run_verify`：执行通用 harness checks 和 run 中的 `verify.py`，生成结构化 verification report。
- `review_propose`：记录 agent reviewer 的 proposal；用户 review 只能由 `review.submit` request 写入，agent 无权把 artifact 标为 approved。

每个 tool 还必须声明 effect class 和 lock scope。`artifact_query`、`dataset_inspect`、受限 `dataset_sample` 可并发；`figure_spec_version_create` 持有 workspace mutation lock；`analysis_run_create/execute/verify` 持有 per-run lock。两个 mutating calls 不因模型在同一轮同时返回就并发执行。

所有会向模型返回 dataset values、artifact content、verification details 或 logs 的 tools 都经过同一 disclosure wrapper；local full result 与 model-visible result 是两个明确字段和 checksum，不由 tool 自由把本地对象 stringify 到返回文本。

代码编写使用 Ocean RuntimeProfile 中的 scoped file tools，不直接继承默认 Bash/Web/task tools，也不包装 `compute_*` 海洋工具。`artifact_publish` 由 backend service 独立枚举 outputs，拒绝 symlink、hardlink、device file、越界路径和超限文件，在 schema、checksum 和 provenance 完整后执行；它不作为允许模型任意写状态的自由工具。

外部数据 catalog/search/fetch 后续通过 MCP 接入，但 MCP 只负责发现、授权和传输数据，不负责替 agent 决定科学算法。

### 9.6 Code 与 output contract

以下对象各自只有一个职责：

| 对象 | 回答的问题 | 何时冻结 |
| --- | --- | --- |
| FigureRequest | 用户最初说了什么 | request 创建时 |
| FigureSpec | 用户最终希望回答/展示什么，以及明确要求的范围和约束 | 每个 spec version 创建时 |
| AnalysisPlanArtifact | 这一次 run 准备采用什么数据、方法、假设和 checks | 第一次 attempt 前 |
| Attempt snapshot | 某次执行实际运行了哪份代码和环境 | 每次 execute 前 |
| Output Manifest | 某次 attempt 实际产生了什么 | attempt 结束后 |
| VerificationRecord | 哪些声明的检查实际通过、警告或失败 | verify 后 |

变更规则：

- 用户目标、展示内容或明确范围改变，创建新的 FigureSpec version。
- 只修复语法、路径或实现 bug，保留同一 AnalysisPlan，在同一 run 创建新 attempt。
- baseline、weighting、selection、统计方法或 validation assertions 等科学方法改变，创建新的 AnalysisPlanArtifact version 和新 run，不能用调试 attempt 掩盖 post-hoc 方法变化。
- output 与 FigureSpec/AnalysisPlan 不一致时，verifier 报 warning/fail；不得在运行后静默回填旧 spec 或 plan。
- Output Manifest 只陈述实际输出，不承担解释用户意图或批准结果。

第一次 execute 后，AnalysisPlanArtifact 中的 assertion IDs 和语义被冻结。`verify.py` 可以修复实现 bug，但若新增、删除或改变 assertion 的科学含义，backend 要求创建新的 AnalysisPlanArtifact version 和新 run，并把它标记为 post-hoc revision，而不是覆盖原门禁。

`analysis_plan.json` 至少声明：

- scientific question 和待检验假设。
- input refs、变量、时空深范围和单位预期。
- 采用的方法及关键选择，例如 baseline、weighting、mask 和 calendar handling。
- 预期输出文件、变量、维度和单位。
- 在运行结果出现前确定的 validation assertions。

`outputs/manifest.json` 至少声明：

- 每个输出的相对路径、类型和用途。
- derived variable、dimensions、units 和 coordinate summary。
- 与 FigureSpec/ExperimentPlan 的对应关系。
- agent 认为成立的 checks；它们仍需由 verifier 实际执行。

stdout 中出现一个数字或文件路径不能视为正式输出。没有 manifest、代码、输入 refs 和 checksum 的文件不能发布为科研 artifact。

### 9.7 验证门禁

验证分为三层，避免让同一个模型既写答案又成为唯一裁判：

| 层次 | 责任 | 例子 |
| --- | --- | --- |
| Harness checks | 可信、通用、与具体算法无关 | parse/compile、越界写入、exit code、文件可读、checksum、图片非空 |
| Analysis checks | agent 在执行前声明并写入 `verify.py` | baseline 覆盖、坐标对齐、单位、样本数、守恒或结果范围 |
| Independent checks | 与 analysis implementation 不共享同一错误路径的证据 | synthetic/reference answer、第二实现、小样本独立复算 |
| Scientific review | 用户或后期独立 reviewer | 方法是否合理、解释是否过度、结论是否被证据支持 |

机器结果使用 `pass/warning/fail`：

- `fail`：输出保留在 attempt 中供诊断，但禁止发布为正式 Figure/Metric/DerivedDataset artifact，也不能进入 report。
- `warning`：允许展示和 review，但必须在 TUI 和 caption 上显式可见。
- `pass`：只表示通用检查和当前声明的 assertions 通过，不证明科学结论成立。

每个 VerificationRecord 保存 `check_id`、`origin=harness|agent_declared|independent_reference`、`independence=self|independent_implementation|reference_answer` 和 `evidence_level=mechanical|declared_assertion|independent_numeric`。TUI 显示具体等级，不使用“科学上已验证”的笼统标签。

第一版 verifier 不需要理解所有海洋算法。anomaly、trend、transport 等科学检查由 agent 针对当前代码生成；随着真实失败案例积累，可以把普适且与算法实现无关的检查提升为可信 harness validator。`target_quality=publication` 的结果在进入确定性报告前，至少需要 independent check 或明确 human scientific approval；两者都缺失时只能作为 exploratory evidence。

### 9.8 沙箱与权限

- v1 analysis subprocess 使用启动 `ocean` 时已经确定的 Python environment；backend 记录 interpreter、完整 environment manifest/fingerprint，并为 UI 生成关键 package 摘要。
- agent 不得在 AnalysisRun 内执行 `pip/conda/uv install`。缺失依赖返回 `dependency_missing` capability error；用户通过独立、显式确认的 environment 管理动作安装，安装完成后重启或刷新 runtime fingerprint。
- workspace 保存 `environment.lock.json` 或等价 fingerprint，用于比较不同 run 的环境变化，但第一版不为每个 run 自动创建独立 venv。
- 每个 attempt 保存完整 environment manifest：interpreter path/version、OS/architecture、所有可见 Python distributions 的 name/version/direct-url/editable source，以及可获取的 PROJ、GEOS、HDF5/NetCDF 等 native runtime/data versions。UI 可只显示关键包摘要，但 provenance 指向完整 manifest 和 checksum。
- `environment.lock.json` 是 workspace 期望环境；attempt manifest 是实际环境。两者 fingerprint 不同时在执行前显示 drift，并写入 verification/provenance；不在运行后用期望 lock 覆盖实际记录。
- RuntimeProfile 从空 allowlist 构建；不向 Ocean agent 暴露默认 Bash、Web、task、worktree、config 或未批准 MCP tools。
- run 只允许写自己的 attempt/output/temp directory；`work/` 在执行前 snapshot，运行中的子进程不能回写 agent 编辑副本。
- 冻结的 DatasetArtifact/snapshot 和 reference directories 以只读 mount 暴露；project 其他 source、home、SSH/cloud credentials、API key store 和 config paths 不可见。新的外部输入必须先成为 DatasetArtifact 并创建新 run，不在运行中动态加载。
- v1 AnalysisRun 始终禁用任意网络；remote catalog/fetch 由独立 MCP 经授权完成，下载结果注册为冻结输入后才进入 run。
- workspace 保存 versioned `ModelDataDisclosurePolicy`，分别规定 metadata、aggregate statistics、raw bounded samples、document text 和 diagnostic excerpts 的 `allow|prompt|deny`。默认不自动发送 raw samples 或原始 logs；provider/model 变更时重新评估 policy。
- 每次 provider call/tool disclosure 记录实际 policy version。运行中将 policy 改得更严时对后续披露立即生效；放宽 policy 不回溯重发旧日志/sample，需要新的显式工具请求。
- `dataset_sample` 在读取前检查 disclosure policy；它的点数/字节限制是最大值，不是默认授权。被禁止时返回 schema/可允许统计，不通过将数值放进 error message 绕过。
- 完整 stdout/stderr 永远只保存在本地 run store，不自动回填 conversation。模型默认只获得 exit/error class、受限且 project-relative 的 code location、文件数/字节数和 checks summary；原始片段通过 `analysis_diagnostic_excerpt` 显式申请。
- redaction 是 defense-in-depth，不声称能识别所有敏感科学值。因此 deny/prompt 在数据读取前执行，不只在文本生成后做 regex 脱敏。
- resource policy 至少限制 wall time、CPU time、memory、disk bytes、process count、open files、output file count/bytes 和 stdout/stderr bytes；阈值及平台实现由 OD-07/OD-08 冻结。
- 沙箱不可用时，阻止 artifact-producing AnalysisRun。v1 可以完全不提供 unsafe execution；若为调试保留用户主动的 manual action，必须走不向模型暴露的独立 UI，其输出标记 `execution_trust=unsafe`，不能 publish/approve；只能通过新的 trusted run 重现后进入正式证据链。
- subprocess 以独立 process group 启动；cancellation 先 terminate 整个 group，grace period 后 kill，并写 terminal attempt event 与 run `ready` transition。
- backend 在执行前后重新计算输入 fingerprint；运行期间数据变化时当次 attempt 进入 `source_changed_during_run`，不发布结果。
- 环境变量采用 allowlist，不能把 credential store 整体传入脚本。

OpenHarness file-tool permission 只能约束 agent 的显式 file calls，无法约束已经启动的 Python 进程。因此 AnalysisRun 的路径和网络限制必须由 subprocess sandbox 实际执行，不能只靠 system prompt 或 `PermissionChecker`。

## 10. FigureSpec 与 Plot Studio

### 10.1 FigureSpec v1

FigureSpec 至少包含：

```text
schema_version
spec_id + version
intent
  user_goal
  scientific_question
  purpose
  target_quality
figure
  type
  subtype
  title
  panels
data
  source_refs
  variables
  coordinates
  spatial_extent
  temporal_extent
  depth_selection
analysis_constraints
  selection
  aggregation
  baseline
  method_requirements
  filtering
  masking
  weighting
plot
  projection
  scales
  colormap
  colorbar
  labels
  annotations
  style
output
  formats
  dpi
  dimensions
  save_code
  save_processed_data
validation
  required_variables
  unit_expectations
  bbox
  time_range
  missing_ratio
  baseline
  output_checks
```

FigureSpec 是需求与审查 contract，不是预定义算法流程：

- 它描述用户要回答的问题、输入范围、期望图形和必须检查的条件。
- `analysis_constraints` 只记录用户明确要求或已经接受的计算约束；`method_requirements` 是可扩展的意图/约束列表，不是枚举 anomaly/trend/transport recipes。agent 实际采用的方法、假设和 checks 以对应 AnalysisPlan 为准，不在运行后回写旧 FigureSpec。
- 同一个 FigureSpec 可以由不同代码实现；每次实现必须关联具体 run、code checksum 和 verification。
- Plot Studio 展示并允许用户修改 spec，但 agent 仍通过 coding loop 决定如何使用 xarray/NumPy/Matplotlib 实现。

FigureSpec 自身是 versioned artifact。用户反馈不会覆盖旧 spec：

```text
FS001.v1 -> Review001 -> FS001.v2
                         -> Run002
                         -> Figure001.v2
```

### 10.2 Figure flow

```text
自然语言需求
  -> FigureRequestArtifact
  -> FigureSpecArtifact draft
  -> dataset_inspect
  -> spec ready
  -> agent 创建 AnalysisRun 并编写 analysis.py / verify.py
  -> execute / debug / rerun
  -> verification
  -> SpatialLayerArtifact 或 SelectionArtifact + LinkedPlotArtifact
  -> optional static FigureArtifact needs_review
  -> user ReviewArtifact
  -> approve 或生成新 FigureSpec version
```

### 10.3 Plot Studio 的两种地图交互模式

Plot Studio 同一个 MapScene 中必须支持两类一等对象。

#### A. Spatial field 直接作为地图图层

适用于 SST、SSH、chlorophyll、anomaly、trend、vorticity 等带经纬度坐标的二维场：

```text
agent 生成空间场代码
  -> outputs/spatial_field.nc 或 zarr
  -> outputs/layer.json
  -> SpatialLayerArtifact
  -> Plot Studio 直接叠加到地图
```

`SpatialLayerArtifact` 至少记录：

- data artifact ref 与 variable。
- CRS、经纬度 bounds、grid/coordinate 信息和 resolution。
- time/depth selection。
- units、missing value 和 value range。
- render mode、colormap、levels、opacity 和 colorbar label。
- source run、code checksum、verification 和 static preview URI。

第一版原生支持规则经纬度、EPSG:4326 scalar raster。agent/run 输出 `field.nc`、透明背景 `overlay.png` 和 `layer.json`；backend 通过下述 pixel-registration contract 后才创建 SpatialLayerArtifact：

```text
schema_version
crs = "EPSG:4326"
axis_order = "longitude_latitude"
source_coordinate_registration = "center" | "edge"
pixel_registration = "outer_edges"       # v1 唯一允许值
row_order = "north_to_south"
column_order = "west_to_east"
longitude_domain = "-180_180"
width + height
parts[]
  image_uri
  west_edge + east_edge + south_edge + north_edge
  width + height
nodata
  alpha = 0
resampling = "nearest" | "bilinear"
units + value_range + colormap + levels
```

对于以 cell centers 表示的规则网格，agent 在代码中显式计算 outer edges；PNG 第 0 行对应最北端像素，第 0 列对应最西端像素。每个 part 必须 `west_edge < east_edge`，跨 antimeridian 的场拆成两个非跨界 parts，不用 `west > east` 的隐式约定。backend 校验 PNG 尺寸、bounds、坐标单调性、field/overlay 角点对应、nodata alpha 和 color mapping。

v1 不实现 tile、逐像素 hover、vector field 或动态 time/depth slider。曲线、tripolar、staggered 或其他投影网格不能被当作规则网格直接显示；需要转换时，AnalysisPlan 必须声明 remapping 方法、source/target grid refs、coverage 和守恒/误差检查。未被用户接受或检查不充分时返回 `unsupported_grid`/fail，不静默插值。

离线 Plot Studio 使用打包的低分辨率 coastline GeoJSON 和经纬网，不请求公网 basemap tiles。后续引入 tile/texture 必须保持相同 SpatialLayerArtifact，不改变科研 provenance。

#### B. 地图 feature 关联可点开的图表

适用于“某站点的时间序列”“某位置的 T-S diagram”“某区域平均曲线”或“某断面的 section”：

```text
MapSceneFeature
  geometry: Point | Polygon | LineString
  label: "Station A"
  selection_ref: SelectionArtifact | null
  plot_refs:
    -> LinkedPlotArtifact(time_series)
    -> LinkedPlotArtifact(ts_diagram)
```

普通展示 marker 是 `MapSceneArtifact` 内嵌 feature，不单独制造 artifact。只有具有可复用科研含义的 station、region 或 transect 才创建不可变 `SelectionArtifact`；它记录 geometry、selection method、数据坐标约定和 provenance。

第一版先实现 `Point` marker：用户点击 marker 后，Plot Studio 在 side drawer 中打开关联的 time series 或 T-S 图。随后扩展：

- `Polygon`：点击研究区域，打开区域平均 time series、histogram 或 diagnostics。
- `LineString`：点击 transect，打开 vertical section、transport series 或 Hovmoller。
- 一个 feature 可以关联多个 plot，由用户在 drawer 中切换。

`LinkedPlotArtifact` 至少记录：

- `plot_kind`：`time_series | ts_diagram | profile | section | hovmoller | scatter`。
- structured data URI、static preview URI 和 interaction metadata。
- axes、variables、units、selection、source run 和 verification。
- 与 SelectionArtifact、dataset、FigureSpec、hypothesis 的 typed links。

图表不能只有 PNG。time series、T-S 等交互图必须保留结构化数据，使 frontend 能显示 hover、选择和后续联动；PNG/PDF 是导出和审查副本。

LinkedPlot JSON 的每个 axis/variable 都必须声明 `name`、`dtype`、`units`、optional `standard_name`、missing encoding 和 shape。时间轴使用下列两种显式编码之一：

- standard Gregorian-compatible calendar 可用 RFC 3339/ISO strings，同时保留 source calendar/units。
- `360_day`、`noleap`、`all_leap` 等 non-standard calendar 必须保留 numeric offsets + CF time units + calendar，frontend 使用 calendar-aware formatter，不经 JavaScript `Date` 强制转换。

JSON 中 missing value 统一写 `null`，NaN/Infinity 不直接序列化。uncertainty/error bounds 作为具名 companion variables 保留，不只画在 PNG 中。backend 校验所有列长度/shape、单位、calendar 和 selection ref 一致性。

v1 linked plot 使用结构化 JSON，单个交互 payload 上限为 5 MB 或 50,000 points；超限时 agent 必须生成用于交互的明确 downsampled 数据，同时把完整结果保存为独立 data artifact。downsampling 方法和完整数据 ref 必须写入 provenance。

#### MapScene 组合

`MapSceneArtifact` 保存一个可恢复、可版本化的工作场景：

```json
{
  "scene_ref": {"artifact_id": "scene_01J...", "version": 3},
  "viewport": {"west": 99, "east": 145, "south": 1, "north": 50},
  "layer_refs": [
    {"artifact_id": "layer_01J...", "version": 1}
  ],
  "features": [
    {
      "feature_id": "feature_station_a",
      "geometry": {"type": "Point", "coordinates": [121.5, 22.0]},
      "selection_ref": {"artifact_id": "selection_01J...", "version": 1},
      "plot_refs": [{"artifact_id": "plot_01J...", "version": 2}]
    }
  ]
}
```

scene 只组合 refs，不复制 spatial arrays 或 plot data。地图上的 layer/feature 选择属于 view state；当用户明确保存场景或改变 active research context 时才创建新 scene version。

静态 `FigureArtifact` 与交互对象保持区分：前者面向论文、报告和固定版本审查；`SpatialLayerArtifact`、`SelectionArtifact`、`LinkedPlotArtifact` 和 `MapSceneArtifact` 面向探索与交互。它们可以由同一个 AnalysisRun 同时产生并互相链接。

### 10.4 Agent-generated Figure 能力验收矩阵

不建设硬编码 Plot Catalog，也不要求每种图对应一个 template、skill 或 tool。以下类别是 agent coding 能力的渐进验收任务：

第一条纵向链：

- 从本地 NetCDF 生成指定 Point/station 的 time series，并通过地图 marker 打开。

第二批评测：

- map。
- anomaly map。
- vertical profile。

第三批评测：

- seasonal panel map。
- transect / section。
- T-S diagram。
- scatter / regression。

每类任务都由 agent 读取数据并生成代码完成。仓库可以提供测试 fixture、优秀代码示例和 plotting references，但它们只能作为可检索材料，不是不可绕过的执行路径。能力扩展优先改进 system prompt、环境、数据 inspection、错误反馈和 eval，不为每个失败案例新增一个 skill。

这样先验证 protocol、store、coding loop、verification 和 review，再承担 Cartopy、calendar、projection 和多 panel 的额外复杂度。

### 10.5 数据能力 v1

- 本地 NetCDF / Zarr。
- xarray dataset metadata、coordinate、unit 和 small-sample statistics inspect。
- CF-style latitude/longitude/time/depth 识别。
- 本地 subset 和 derived dataset 注册。
- remote small subset 作为后续能力，必须先估算下载体积并请求确认。
- 不自动加载整个大数组进模型 context。

`DatasetArtifact` 至少保存 URI、format、变量/坐标摘要、size、mtime、identity strength、fingerprint、materialization level 和 rerun availability。materialization level 只允许：

| Level | 含义 | 可复现承诺 |
| --- | --- | --- |
| `materialized_snapshot` | 完整输入字节保存在 project store，有全量 checksum | 只要 store 完整就可重跑 |
| `cached_subset` | 准确保存当次查询/subset 的字节、selection 和 checksum | 可重跑该 subset，不声称拥有整个原数据 |
| `immutable_remote_version` | provider 给出可验证、不可变的 version/asset identity | 在 provider 可用和许可允许时可重取 |
| `reference_only` | 只保存外部路径和 fingerprint，未保存旧字节 | 只能检测变化，不保证旧 run 可重跑 |

身份与可用性规则：

- 普通本地文件默认计算完整 SHA-256，但如果仅保留外部路径，materialization level 仍是 `reference_only`。
- 大型 Zarr v1 对 consolidated metadata（或 `zarr.json`）以及相对路径、size、mtime inventory 计算 fingerprint，并标记为 `metadata_fingerprint`，不能伪称完整 content hash。
- cached Zarr subset 记录实际读取的 variables、coordinate selections、chunk/asset identities、provider query 和输出 checksum。
- remote identity 至少保存 provider、dataset/asset ID、version/DOI/ETag 中可用的稳定字段、请求 query 和 license snapshot；只有 provider contract 证明不可变时才使用 `immutable_remote_version`。
- derived outputs 必须对 manifest 中每个文件计算完整 checksum。
- backend 在 attempt 前后计算 fingerprint；运行中变化则 fail 为 `source_changed_during_run`。
- 再次打开外部数据时 fingerprint 变化，旧 DatasetArtifact 保留并创建新 version。已有 run 仍 pin 旧 ref；若旧 ref 为 `reference_only` 且旧字节不可用，其下游 impact state 变为 `source_unavailable`，不只显示一个可忽略 warning。
- TUI 在 run 前显示预计 snapshot/subset 大小与 level；用户可选择不复制大数据，但必须看到对 rerun 的影响。

`dataset_sample` v1 最多返回 10,000 points 且序列化后不超过 1 MB；请求超限时必须要求更小 selection 或返回统计摘要，不能触发对整个 Dask/Zarr 数据集的隐式 compute。

## 11. Literature、Hypothesis、Experiment 与 Writing

这些能力保留在产品蓝图中，但在 Figure vertical slice 稳定后实施。

### 11.1 Literature Mode

- paper ingestion 和 metadata normalization。
- paper card：question、claim、method、dataset、result、limitation。
- claim 必须保留 source location 和 citation。
- gap synthesis 必须区分作者明确提出的 gap 与 agent 推断。
- Paper/Claim 可以 link 到 Dataset、Figure、Hypothesis 和 Report section。

### 11.2 Hypothesis Mode

HypothesisArtifact 至少包含：

- observation/source refs。
- hypothesis statement。
- mechanism/rationale。
- falsifiable prediction。
- competing explanations。
- proposed experiment/figure。
- required data。
- success/failure criteria。
- status 和 review。

支持从 Figure observation 创建 hypothesis，也支持从 hypothesis 推荐下一组 FigureSpec，但都需要用户确认。

### 11.3 Experiment loop

- ExperimentPlan 生成 analysis runs。
- baseline、metric、parameter sweep 和 stopping rule 结构化保存。
- 失败 run 关联 limitation 或 appendix，不静默丢弃。
- 结果必须区分 expected、unexpected、inconclusive。
- ExperimentArtifact 只引用已存在的 run/metric/figure refs。

### 11.4 Writing Mode

- Report Builder 只消费用户显式选择的 exact artifact refs，不跟随“最新版”漂移。
- claim grounding checker 检查 claim 是否有 paper/data/experiment support。
- 未 approved 的 artifact 可以进入 draft，但必须带醒目标记，不能写成确定结论。checks failed 和 invalidated 阻止确定性 export；stale/source-unavailable 需要用户创建 DecisionArtifact，分别记录保留的版本理由或无法重跑的 limitation。
- 每次 preview/export 重算 pinned refs 的 review、verification 和 impact projection，并把 reason chain 写入 draft diagnostics/reproducibility appendix。
- 自动生成 reproducibility appendix：数据、代码、参数、环境、run 和 verification。
- 支持 failed experiment summary、limitations 和 open questions。

## 12. Multi-agent 路线

Multi-agent 最终会使用，但不是第一阶段的默认能力。

### 12.1 引入条件

只有满足以下条件后才启用：

- 单 agent 的 Figure vertical slice 稳定。
- artifact/run/review 状态不依赖 conversation history。
- tool_call 和 request correlation 完整。
- cancellation、permission、budget 和错误传播可测试。
- 单 writer 事务规则已经实现。

### 12.2 第一批角色

- `LiteratureScout`：只读 paper 和外部检索，产出 candidate Paper/Claim proposals。
- `DataAnalyst`：可准备和执行受控 AnalysisRun，但不能批准 artifact。
- `FigureReviewer`：只读 spec/output/provenance，产出 Review proposal。
- `ResearchWriter`：只读 approved artifacts，产出 report draft。
- Leader/Coordinator：唯一可以接受 proposal 并提交 canonical mutation 的角色。

### 12.3 协作约束

- canonical store 采用 single-writer；worker 只写自己的 run/proposal namespace。
- agent 之间通过 artifact refs、proposal 和 event 通信，不共享无限聊天历史。
- 每个 worker 有明确 tool allowlist、token budget、timeout 和最大并发数。
- permission 不得因子 agent 而升级；worker 只能继承相同或更小权限。
- 冲突通过 expected workspace revision 检测，不做最后写入获胜。
- multi-agent 失败必须降级回 single-agent，不影响 artifact store 完整性。

### 12.4 对现有 OpenHarness swarm 的策略

- `swarm/`、`coordinator/` 和 agent definitions 暂时作为可选实现候选保留。
- 不把 `agent`、`send_message`、`team_create`、`team_delete` 放回默认 tool registry。
- 先做接口与故障测试，再决定复用 in-process、subprocess 或重写 Ocean orchestrator adapter。
- 通过 `ocean.multi_agent.enabled=false` feature flag 默认关闭。

## 13. 分阶段实施路线

### Phase 0：收敛并稳定 OpenHarness core

目标：得到一个可作为上层 App 依赖的干净 runtime 基线。

工作项：

- 关闭 OD-06，冻结 caller-supplied RuntimeProfile/registry factory、tool effect classes 和 scheduler 接口。
- 关闭 OD-07，至少冻结 walking-skeleton 支持平台的 sandbox/resource/unsafe-run 合同。
- 完成无关代码清理：ohmo、IM channels、cron、remote trigger 和依赖真实外部环境的非通用测试。
- 明确保留的 optional multi-agent 源码和默认 registry 边界。
- `build_runtime()` 支持从调用方提供的 registry/profile 构建，不强制先注册默认 tools/MCP。
- 为所有 built-in tools 补充 effect metadata；默认 scheduler 只并发 read-only calls，mutation/external IO 按策略串行。
- 修复/验证 compaction 后 conversation list ownership。
- 为 assistant turn 与 request completion 的语义差异补回归测试。
- tool stream event 增加 correlation metadata，同时保持 v1 adapter 兼容。
- QueryEngine 增加可取消 execution handle，并测试 model stream/tool subprocess 的取消传播。
- 解决 README/package build 一致性。
- 清理重复、失效 import 和 source reference；搜索时排除 `__pycache__`。
- 建立基线质量命令。

验收：

- `python -m compileall -q src tests scripts` 通过。
- 安装 dev dependencies 后完整 `pytest` 通过。
- `python -m build` 通过。
- 现有 frontend TypeScript 编译通过。
- v1 backend/TUI smoke test 通过。
- 默认 registry 不包含 agent/team/send_message/cron/remote trigger。
- 自定义 RuntimeProfile 的 API schema 中只出现 allowlisted tools/MCP，不泄漏默认 Bash/Web/task/worktree/config capabilities。
- 一轮同时返回 read + mutation + mutation tool calls 时，只读调用可并发，两个 mutation 以确定顺序执行。
- compaction 后 `QueryEngine.messages` 和保存的 session snapshot 包含同一份压缩后历史。

### Phase 0.5：Risk-burning Walking Skeleton

目标：在泛化 Protocol v2、Artifact Store 和完整 UI 之前，用最小纵向实验验证最高风险假设。这不是可发布 MVP，代码隔离在 `spikes/ocean_walking_skeleton/`，允许使用临时内部 schema，但必须保留 fixtures、安全测试、测量结果和 ADR。

工作项：

- 用 synthetic regular-grid NetCDF 构造已知 point time series 和已知角点颜色的 checkerboard spatial field。
- 在 spike 中用最小 Ocean RuntimeProfile 直接组合 QueryEngine，只暴露 scoped file、dataset inspect/sample 和 run tools；不提前声称这些临时 handlers 就是生产 `ocean_partner` API。
- 让 agent 生成 `analysis.py` / `verify.py`，在真实 sandbox/resource policy 下执行；并用 fake model/manual fixture 保持离线可重复。
- 用临时 manifest 保存 code、input identity、logs、output checksum 和 checks，不在 spike 中提前设计全部 artifact types。
- 原型化两个 graphical 动作：map marker 打开 time series，checkerboard PNG 按地理 edges 叠加。
- 关闭 OD-01，用原型结果决定 map/chart library。
- 关闭 OD-09、OD-11 和 OD-12，冻结第一版 Dataset materialization、overlay registration 和 model-data disclosure contracts。
- 用一次目标海洋研究者 task walkthrough 记录从问题到查看证据的阻塞点；不以开发者自己看到图作为唯一 UX 验收。

验收：

- model-visible schema 不包含 Bash/Web/task/worktree/config 或未允许 MCP，尝试调用时返回 unknown/denied tool。
- 测试脚本无法读取 home/credentials、写出 run root、访问网络或留下孤儿子进程。
- timeout、memory/disk/output limit 至少各有一个确定失败 fixture，不能靠人工目测。
- point series 数值与 synthetic reference answer 一致，且 input 在运行中改变时 publish 被阻止。
- adversarial analysis 即使向 stdout 打印整个 dataset/大块数值表，后续模型 tool result 仍只包含 policy-safe summary；完整日志仅在本地可查，显式 excerpt 操作留下 approval/audit record。
- marker 打开正确 series；checkerboard 的四角、南北方向和跨 antimeridian parts 在 desktop/mobile 截图中与 fixture 一致。
- walkthrough 产生可追踪发现；若用户无法理解结果的数据、代码、checks 和 trust level，不进入 Phase 1 泛化。

### Phase 1：Protocol v2 + Ocean App shell

目标：不调用真实模型，也能启动 `ocean`、握手、打开 workspace，并让 request 在断线/重启后仍具有确定结果。

工作项：

- 关闭 OD-08，记录 foreground request/attempt/cancel/recovery ADR。
- 新增 `src/ocean_partner` 和 `ocean` console script。
- 实现 v2 Pydantic union、schema export 和 generated TypeScript union。
- 在 `ocean_partner.backend` 实现 stdio v2 adapter、WebSocket adapter 和共享 event bus，不把 Ocean transport 放进 OpenHarness core。
- 实现 request router、sequence、terminal event、cancellation skeleton 和最小 durable RequestStore/idempotency journal。
- 新增 Ocean React Ink shell、transport/session/workspace reducers。
- 新增空的 graphical Plot Studio shell、packaged asset server 和 token handshake。
- 实现 `system.handshake`、`session.open`、`request.status.get`、`workspace.open`、`workspace.snapshot.get`、`plot_studio.open` 和 `system.shutdown`。

验收：

- frontend/backend 对 valid/invalid fixtures 得出一致结果。
- 每个 accepted request 只有一个 terminal event。
- mutation commit 后、terminal event 发送前注入崩溃，重连重试不重复 mutation，而是重放同一 terminal result/event ref。
- 同一 request ID 携带不同 payload 被 `request_id_conflict` 拒绝。
- sequence gap 能触发 snapshot resync。
- backend stdout 没有非协议状态数据。
- stdio TUI 与 WebSocket Plot Studio 解析相同 envelope，并看到相同 workspace revision。
- graphical server 只监听 loopback，一次性 bootstrap token 不可重放，缺少 client capability/错误 Origin 的 WebSocket 和 artifact request 被拒绝。
- Plot Studio client 无法提交 agent request 或回答 execution permission，model tool 无法伪造 human `review.submit`。
- v1 `oh` 行为和测试不回归。

### Phase 2：Artifact Store + Workspace context

目标：在无模型情况下完整创建、版本化、查询和 review artifact。

工作项：

- 关闭 OD-03，记录 project state/export/backup ADR。
- 关闭 OD-10，记录 dependency impact/staleness ADR。
- SQLite schema、migration 和 transaction layer。
- 将 Phase 1 RequestStore 纳入正式 migration；实现 immutable artifact version、intrinsic/contextual links、events、reviews、state/impact projections 和 workspace revision。
- SpatialLayer、Selection、LinkedPlot、MapScene schemas 与关系约束。
- filesystem version directory、commit intent、manifest、checksum、quarantine 和 URI resolver。
- `WorkspaceService`、`ArtifactService`、`ReviewService`。
- TUI Workspace 与 Artifact Inspector。
- `OceanContextBuilder`，按 active refs 构建受预算控制的模型 context。
- workspace-level ModelDataDisclosurePolicy、provider binding 和 disclosure audit records。
- 打包第一批 research-process skills 与 Ocean references，并通过 `extra_skill_dirs` / packaged resource path 接入 OpenHarness skill loader。

验收：

- 创建 v1/v2 不覆盖旧目录或 DB row。
- link 可双向查询。
- stale revision mutation 被拒绝。
- 同一 model turn 中的两个 domain mutations 串行执行，不绕过 workspace revision。
- store 重启后 workspace/active refs/reviews 可恢复。
- artifact 与 event 在同一 transaction 中提交。
- review/verification 更新 projection，但不改写旧 ArtifactVersion manifest。
- upstream supersede/reject/tombstone/source-unavailable 后，下游 impact projection 可确定重建，Report preview 显示完整 reason chain。
- projection 可以从权威 DB records/events 重建，并得到相同状态。
- 大文件只保存引用和 checksum，不进入 DB JSON payload。
- workspace/DB/log 的本地 file modes 符合 OD-03，portable export 不泄漏 absolute home/credential paths 或未批准 disclosure content。
- system prompt 只列 capability-enabled skill metadata；完整 skill/reference 仅在匹配任务时按需加载，并记录本次 run 使用的资源版本。Literature/remote skill 在其 Open Decision 关闭前不可见。
- 第一方 skill audit 确认不存在固定 `compute_*` 调用链、审批越权或隐藏分析代码。

### Phase 3：AnalysisRun executor + verification

目标：让 agent 在受控目录编写和执行 Python 分析，完整保留每次失败、修改和成功的证据。

工作项：

- RunService 与分离的 AnalysisRun/Attempt state machines。
- 把 walking-skeleton executor 收敛为正式 sandbox adapter、resource policy 和 RuntimeProfile integration，不保留第二条绕过路径。
- prepare/preflight/authorize/execute/capture/verify pipeline。
- timeout、resource limit、cancel、process-group cleanup、interrupted recovery 和 fail-closed sandbox policy。
- environment capture、完整 logs、output manifest 和 checksums。
- `dataset_inspect`、`dataset_sample`、run、output inspect 等 infrastructure tools 注册。
- scoped file tools 只能写 run `work/`；Ocean RuntimeProfile 不包含 Bash/Web/task/worktree/config。
- 通用 harness validators、agent-authored `verify.py`、verification origin/independence metadata 和 image validators。
- 输入 pre/post fingerprint、source mutation failure、output file enumeration 与 symlink/hardlink/device/size rejection。
- disclosure-safe execution result、`analysis_diagnostic_excerpt`、stdout/stderr local-only 存储和 sample/log policy enforcement。
- `ocean doctor`/handshake capabilities 报告 interpreter、Ocean extras、sandbox backend、resource-limit support 和不可用原因，不到第一次 execute 才暴露环境问题。

验收：

- 成功、syntax error、nonzero exit、timeout、resource limit、cancel、source changed、sandbox unavailable 都有确定 terminal state。
- backend restart 将遗留 `running` attempt 标记为 `interrupted`，保留对应 run 并恢复为 `ready`。
- run 不能写出允许目录。
- sandbox 不可用时 artifact-producing run 无法通过通用 permission 继续；unsafe output 不能 publish/approve。
- `ocean doctor` 的 machine-readable result 与 handshake capability 一致，对缺失 sandbox/maps/zarr/gsw 给出可行的 capability error。
- agent 可以依据 stderr 修改代码并创建新的 execution attempt，旧 attempt 不被覆盖。
- checks failed 的输出保留在 attempt 中，但不能发布为正式科研 artifact；warning/pass 仍需独立人工 review。
- 模型可见摘要与完整日志分离，完整日志始终可追溯。

### Phase 4：Plot Studio 第一条纵向链

目标：本地 NetCDF -> time series FigureSpec -> code -> run -> LinkedPlotArtifact -> 地图 marker 点开图表 -> review -> revision。

工作项：

- 在实现前关闭 OD-02，冻结首个 real-model eval manifest 和 gate。
- FigureRequest、FigureSpec、FigureArtifact schemas。
- 本地 dataset inspect 与 bounded sample。
- agent 使用 scoped file tools 从零编写 `analysis.py` 和 `verify.py`，不依赖内置 time-series kernel 或强制 template。
- graphical Plot Studio 的 MapScene、Point marker 和 linked time-series drawer。
- TUI 的 spec/code/run/checks/version views 与 `Open Plot Studio` command。
- 用户 review 创建新 spec version。

验收场景：

```text
用户选择 tests fixture 中的本地 NetCDF
-> 请求一个指定站点的 SST 月序列
-> 系统生成 FigureSpec v1
-> 用户选择/确认 Dataset materialization level 和 rerun 含义
-> agent 检查数据并编写 analysis.py / verify.py
-> 执行 analysis.py
-> 保存 structured series data、PNG/PDF、code、spec、environment、logs 和 provenance
-> 创建 Point SelectionArtifact、scene feature 与 LinkedPlotArtifact
-> 用户在地图点击 marker 打开 time series
-> verifier 返回 pass/warning/fail
-> 用户要求修改时间范围和颜色
-> 生成 FigureSpec v2、LinkedPlotArtifact v2 和 FigureArtifact v2
-> v1 仍可查看、比较和引用
```

### Phase 5：开放式 Ocean coding coverage + Data Resolver

目标：证明 agent 在结论需要计算时，能用开放式编码覆盖常见海洋分析与绘图任务，并安全处理本地数据和小 remote subset；不为这些任务建立固定 workflow catalog。

工作项：

- 在接入 remote data 前关闭 OD-04。
- 建立 map、anomaly map、vertical profile、seasonal panels、section、T-S、scatter/regression 的 reference evals。
- 实现规则经纬度 scalar `SpatialLayerArtifact` 和符合 OD-11 pixel-registration contract 的 image-overlay rendering。
- 扩展 Selection geometry 到 Polygon/LineString，并支持 T-S、profile、section 等 linked plots。
- 实现 MapScene layer visibility、opacity、feature selection 和 artifact inspector。
- 为 agent 提供可检索的库文档、优秀代码示例和失败案例，而不是 method-specific skills。
- dataset inspector 显式暴露 CF coordinates、calendar、depth positive direction、longitude convention 和 units；reference eval 检查 agent 代码正确处理它们。
- bbox/time/depth/missing ratio/baseline checks。
- remote catalog/search/fetch MCP，下载前显示许可和体积。
- quicklook 和 derived dataset artifact。

验收：

- anomaly map 明确 baseline 和 weighting。
- longitude 0..360 与 -180..180 不导致 silent crop。
- non-standard calendar 有明确处理或 warning。
- remote fetch 未经授权不会发生。
- spatial field 可直接显示在地图，pixel edges、row/axis order、antimeridian parts、nodata、units、colorbar 和 source run 可检查。
- 点击 Point/Polygon/LineString 可以打开正确版本的 linked plot，重启后 scene 关系仍可恢复。
- 每类任务至少有一个 synthetic numeric reference answer 和 output validation；测试必须经过 agent 生成代码的 run path，不依赖产品内置 `compute_*` 实现。

### Phase 6：Literature + Hypothesis

目标：把图和数据结果接回科学问题，而不是停留在绘图工具。

工作项：

- 关闭 OD-05，落实 literature source/PDF/citation/prompt-injection contract。
- Paper/Claim/Observation/Hypothesis artifacts。
- paper card、claim source location 和 gap synthesis。
- `literature-evidence-synthesis` 与 `ocean-question-and-scale-framing` skills 的按需加载。
- OD-05 关闭前不暴露 `literature-evidence-synthesis`；关闭后再通过 RuntimeProfile capability gate 启用。
- Hypothesis Board、falsifiable prediction 和 competing explanations。
- Figure observation -> Hypothesis proposal。
- Hypothesis -> next FigureSpec/Experiment proposal。

验收：

- agent 推断与论文原文 claim 明确区分。
- 纯论文分析可以在不创建 AnalysisRun、不写 Python 代码的情况下完成，并仍保留 source/citation evidence。
- hypothesis 缺少 falsification criteria 时不能进入 ready。
- proposal 未经用户接受不修改 active hypothesis。

### Phase 7：Experiment + Writing

目标：完成从 hypothesis 到 experiment，再到可追溯报告的研究循环。

工作项：

- ExperimentPlan、metrics registry、parameter runs 和 failure capture。
- approved 且 impact state 可接受的 exact artifact refs 进入 Report Builder。
- claim grounding checker。
- limitations、failed experiments 和 reproducibility appendix。
- Markdown/HTML first export；DOCX/PDF 作为后续 adapter。

验收：

- report 中每个 figure/metric/claim 能回溯到 artifact refs。
- report preview/export 每次检查 pinned refs 的 review/verification/impact state，stale/invalidated/source-unavailable 不能静默输出为当前结论。
- failed/rejected/interrupted attempt 和 abandoned run 不会被写成 positive result。
- reproducibility appendix 包含输入、代码、环境、参数和 verification。

### Phase 8：Multi-agent（feature flag）

目标：让角色并行提高吞吐，而不是改变可信度规则。

工作项：

- 评估现有 swarm/coordinator 的复用成本。
- single-writer proposal/commit 协议。
- worker permission、budget、timeout、cancel 和 failure propagation。
- LiteratureScout、DataAnalyst、FigureReviewer、ResearchWriter。
- TUI 显示 worker 状态和 proposal，不展示不可控的内部聊天洪流。

验收：

- 同一任务 single-agent 与 multi-agent 产生相同结构约束的 artifacts。
- worker crash 不损坏 workspace。
- 并发 proposal 冲突被 revision check 捕获。
- 关闭 feature flag 后系统完整可用。

## 14. 测试策略

### 14.1 默认测试不依赖真实模型

- agent loop 使用 fake streaming API client。
- fake model 只验证 tool/run/protocol plumbing，不能证明 agent 具有海洋科研编码能力。
- dataset 使用小型 synthetic xarray fixtures。
- plot 检查数值结果和 manifest，不只做脆弱的像素 golden test。
- 网络、MCP 和 remote catalog 使用 fake server。
- real-model E2E 放在显式 harness-eval suite，不进入普通 CI；它报告 reference tasks 的成功率、重试次数、代码质量、数值误差和 token/cost。

### 14.2 测试层级

Unit：

- schemas、分离的 AnalysisRun/Attempt state transitions、URI、checksum、FigureSpec validation。
- dataset inspection、bounded sampling、materialization levels、pre/post fingerprint、output manifest 和 generic validators。
- context budget 和 artifact retrieval。
- MapScene refs、geometry、SpatialLayer/LinkedPlot payload validation、pixel-edge/row-order/antimeridian-parts rules，以及 CF calendar-aware time-axis encoding。
- lifecycle/review/verification/impact projections 和 reason-chain traversal。
- RuntimeProfile allowlist、model/tool budgets、tool effect classification、lock scope 和 resource policy validation。
- expected environment lock 与 actual attempt manifest fingerprint/drift detection。
- ModelDataDisclosurePolicy、provider binding、sample/excerpt decision 和 disclosure audit payload validation。
- output enumeration 对 symlink、hardlink、device file、越界路径和 size/count limits 的拒绝。
- skill metadata、trigger routing、reference resolution 和 resource version recording。

Contract：

- Python/TypeScript protocol fixtures。
- request terminal event 不变量。
- event sequence、revision、durable duplicate request 和 `request_id_conflict`。
- request/mutation commit 后、terminal send 前崩溃的 replay fixture。
- stdio 与 WebSocket transport 对同一 envelope 的一致行为。
- skill 不能覆盖 system/backend policy，agent proposal 不能产生 approved projection。
- capability-disabled skill/MCP 不出现在 model-visible registry。

Integration：

- fake model -> tool -> run -> artifact -> protocol event。
- SQLite transaction rollback、migration、commit-intent finalize/quarantine 和 projection rebuild。
- 同一 model turn 中 read-only tools 可并发，workspace/run mutations 串行且不产生 lost update。
- sandbox/permission/cancel/timeout、CPU/memory/disk/process/output limits 和 process-group cleanup。
- model turn/token/tool/cost budget 耗尽产生唯一 `budget_exhausted` terminal result，保留已完成 tool/attempt 证据。
- home/credential read、network access、write escape 和孤儿子进程的 adversarial fixtures。
- dataset attributes/file names、PDF text 和 MCP responses 中的 prompt-injection fixtures 无法更改 RuntimeProfile、human actor、disclosure policy 或 artifact gate。
- input 运行中变化会 fail 且禁止 publish；unsafe execution output 无法 approve。
- analysis 向 stdout/stderr 打印原始数组、表格、absolute paths 或 secret-like strings 时，model-visible result 仍符合 disclosure policy；full logs 只在本地 store。
- `dataset_sample` 和 `analysis_diagnostic_excerpt` 的 allow/prompt/deny 流程、硬上限、provider change 和 approval audit。
- upstream supersede/reject/tombstone/source-unavailable 对下游 impact state 的确定传播。

TUI：

- reducer tests。
- ready/snapshot/render/review/error flows。
- terminal width 下无状态丢失。
- assistant turn complete 不会提前结束 request busy。

Graphical Plot Studio：

- MapScene/layer/feature/linked plot reducer tests。
- spatial overlay outer edges、north/south row order、axis order、antimeridian parts、nodata alpha、layer visibility、opacity 和 colorbar state。
- checkerboard/corner-value fixture 在 desktop/mobile viewport 的 screenshot 与 canvas-pixel 检查。
- Point/Polygon/LineString selection 打开正确 artifact version。
- standard/non-standard calendar linked series 使用正确 formatter，不被 JavaScript `Date` 静默改日期。
- WebSocket reconnect 后通过 scene snapshot 恢复状态。

Scientific validation：

- 使用已知解析解或构造数组定义区域平均、anomaly、seasonal aggregation 等 reference tasks。
- 让 agent 生成并执行代码，再比较 numeric reference answer；产品本身不提供对应 `compute_*` kernel。
- eval 覆盖 latitude weighting、depth direction、longitude wrapping、calendar 和 missing mask。
- 图片可读、非空白、合理尺寸和声明的 panel 数。
- VerificationRecord 正确区分 harness、agent-declared 和 independent-reference checks；self-check 不伪装成 independent evidence。

E2E：

- 一条完全离线的 local NetCDF -> map marker -> linked time-series flow。
- 一条 local NetCDF -> SpatialLayerArtifact -> map overlay flow。
- 一条在 model request 中取消 AnalysisRun，确认整个 process group 结束、request terminal 唯一且已完成 attempts 保留的 flow。
- 一条 external `reference_only` dataset 改变后下游变为 `source_unavailable`、materialized snapshot 仍可重跑的 flow。
- 一条 paper-only evidence synthesis flow，证明没有计算需求时不会创建 AnalysisRun 或无意义代码。
- 一条允许真实模型但禁止网络下载的 agent loop flow。
- 一条 `metadata_only` workspace 禁止 raw dataset/log 进入模型 context，但仍能使用 schema 和允许统计完成安全失败/提示的 flow。
- 一条 remote subset 的显式授权 flow。

### 14.3 Real-model Eval Gate

OD-02 关闭时必须冻结一个 versioned eval manifest：

```text
benchmark_id + version/hash
model/provider/profile
system prompt + skill/reference versions
max turns / tokens / wall time / cost
task-specific numeric tolerances
required artifacts and verification checks
repeat count and aggregate pass threshold
allowed retries and failure taxonomy
```

不论最终成功率阈值如何设定，以下条件是硬门禁：

- sandbox、permission、citation 和 artifact integrity violation 必须为 0。
- 宣称成功的任务必须具有完整 code、attempt、output manifest、provenance 和 verification。
- numeric task 必须落入该 benchmark 预先声明的 tolerance，不能由运行后的模型修改标准。
- 每次 eval 保存失败代码、日志和 failure category，用于改进 prompt/environment/inspection；不得通过新增 method-specific skill 来掩盖失败。

### 14.4 Researcher UX 与 Trust Calibration Gate

- Phase 0.5 至少完成一次非实现者视角的目标海洋研究任务 walkthrough；MVP-A、MVP-B 和 Integrated v1 各至少复查一次上一阶段发现。
- 参与者必须能找到数据身份/materialization level、代码、checks origin、warning、review 和 impact reason，不能只看一张图就完成任务。
- 记录 time-to-first-evidence、定位失败 attempt 所需时间、对 `checks_passed` 的理解、发现 stale/source-unavailable 的成功率和主要阻塞点。
- 参与者若把 self-authored check 误解为独立科学验证，该阶段不通过；先修正文案/信息架构，不用更多绿色状态补救。
- 访谈与 walkthrough 记录不默认保存敏感科研数据；只保存经参与者同意的结构化发现。

### 14.5 每阶段质量命令

最终仓库应提供统一命令，例如：

```text
make lint
make typecheck
make test
make test-contract
make test-e2e-offline
make build
```

Python、TypeScript、protocol schema 和 package build 都进入 CI。

## 15. 可观测性与审计

每个 request/run 至少可查询：

- request ID、session ID、workspace ID。
- principal/client capabilities、canonical payload hash、idempotency state 和 terminal result ref。
- model/provider、usage、turn count。
- RuntimeProfile/version、model-visible tool/MCP list、tool effect class、lock wait、duration 和 permission decision。
- run phase、execution trust、sandbox/resource policy、actual resource usage、exit code 和 log URIs。
- input materialization levels、pre/post fingerprints、artifact versions 和 workspace revision changes。
- ModelDataDisclosurePolicy/version、provider、披露类型/字节/点数、approval refs 和 sanitized-content hash，不复制原始敏感片段。
- verification origin/independence/evidence level、review status 和 impact state/reason chain。

日志不得包含 API key、credential path content 或大块原始科研数据。用户导出诊断包时默认只包含 metadata、schema、events 和经过大小限制的日志尾部。

## 16. 依赖与打包

OpenHarness base 依赖保持轻量。Ocean 能力使用 optional extra：

```text
ocean-core:
  numpy, pandas, scipy, xarray, matplotlib, pillow, h5netcdf

ocean-zarr:
  zarr, fsspec

ocean-maps:
  cartopy, pyproj

ocean-physics:
  gsw
```

打包要求：

- wheel 同时包含 `src/openharness` 与 `src/ocean_partner`。
- 新增 `ocean = ocean_partner.cli:app`。
- `ocean doctor` 在不调用模型和不启动 AnalysisRun 的情况下报告 runtime/sandbox/optional-extra capabilities。
- Ocean TUI 与 Plot Studio frontend 源码/构建产物有明确 packaging 规则。
- `ocean` 能启动 packaged Plot Studio assets，不依赖开发机上的 Next/Vite dev server。
- 缺少 maps/zarr extra 时给出 capability error，不在运行中静默安装依赖。

## 17. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 为做 Ocean App 大改 OpenHarness core | 只允许四个通用扩展点，并由 v1 regression tests 保护 |
| 默认 Bash/Web/task/MCP 绕过 AnalysisRun | Ocean RuntimeProfile 从空 allowlist 组合，model-visible schema 做快照与 adversarial tests |
| 同一 model turn 的并发 mutation 破坏状态 | tool effect scheduler + workspace/per-run lock + expected revision，仅只读调用可并发 |
| 协议快速失控 | Pydantic 单一源、generated TS、fixtures、terminal event 不变量 |
| mutation commit 后崩溃导致重复执行 | durable RequestRecord、payload hash、operation checkpoint 和 terminal result replay |
| 对话压缩丢失研究状态 | artifact/workspace 独立存储，context 只注入 refs 和摘要 |
| 模型生成代码不稳定或产生错误结论 | code-transparent AnalysisRun、attempt history、预先声明 checks、独立 review 和真实任务 eval |
| self-authored verify 被误解为独立证明 | 记录 origin/independence/evidence level，UI 使用 `checks_passed` 而非 `scientifically verified` |
| skill/tool 数量随分析方法爆炸 | Ocean tools 只做横向基础设施；新科学方法默认由 agent 写代码，不新增纵向 skill/tool |
| 大数据拖垮 TUI/模型 context | 只传 metadata、small sample 和 URI；AnalysisRun store 保存完整输出 |
| 外部数据有 checksum 但旧字节已不存在 | materialization level、pre/post fingerprint、snapshot/subset 选择和 source-unavailable impact |
| 上游证据变化而报告仍显示旧结论 | typed-link impact projection，report pin exact refs 并在 preview/export 重检 |
| SQLite 与文件写入不一致 | staging + checksum + atomic rename + durable commit intent + deterministic finalize/quarantine |
| PNG overlay 上下翻转、偏半格或日期变更线错位 | pixel-registration contract + checkerboard/corner fixtures + screenshot/canvas-pixel tests |
| sandbox 只限路径而资源/子进程失控 | fail-closed sandbox、resource policy、process-group kill；unsafe output 禁止 publish/approve |
| 脚本通过 stdout/tool result 把本地数据发给远程模型 | ModelDataDisclosurePolicy、full logs local-only、safe structured result、sample/excerpt permission 和 disclosure audit |
| 长计算与 TUI-owned backend 生命周期冲突 | v1 明确 bounded foreground attempt，退出则 interrupted 且不自动 resume；持久作业作为后续 ADR |
| 在第一个用户价值前过度搭建基础设施 | Phase 0.5 walking skeleton 先验证 runtime/sandbox/numeric/map/UX，再泛化 schema |
| Cartopy 等依赖安装困难 | time-series 先行，maps 作为 optional extra |
| multi-agent 并发写坏状态 | 后置、single-writer、revision check、feature flag |
| terminal 无法可靠预览图片 | URI + 外部查看器为基线，inline preview 为可选能力 |
| 本地 graphical server 被非预期访问 | 仅监听 loopback、一次性 bootstrap、transport-bound client capability、Origin/CSP、artifact URI allowlist 和短生命周期 |
| 真实 API 测试不稳定且昂贵 | fake client 默认；real-model suite 显式运行 |

## 18. 分层 Definition of Done

### 18.1 Foundation MVP-A：Map-linked time series

Phase 4 完成需要：

1. 用户运行 `ocean`，TUI 与 backend 完成 v2 handshake，并可打开同一 session 的 local Plot Studio。
2. Ocean RuntimeProfile 只暴露 allowlisted scoped tools/MCP；同一轮 mutating tool calls 串行，request 在注入崩溃后不重复 mutation。
3. 用户打开 project-local workspace，选择本地 NetCDF，明确看到并确认 Dataset materialization level/rerun 含义和 ModelDataDisclosurePolicy；raw sample 不因尺寸小就被默认发给模型。
4. 用户提出一个指定位置的 time-series 图需求。
5. agent 按需加载 `ocean-dataset-diagnosis` / `ocean-analysis-design`，创建 FigureRequest、FigureSpec 和 AnalysisPlan，但不选择预定义 analysis recipe。
6. dataset inspector 返回变量、坐标、单位、范围和 missing statistics。
7. 系统创建 AnalysisRun，agent 使用 scoped file tools 编写 `analysis.py` 和 `verify.py`，随后在 fail-closed sandbox/resource policy 下执行和调试。
8. 每个 attempt 的代码、stdout/stderr、环境、sandbox/resource policy、pre/post input fingerprints 和输出 checksums 被保存；full logs 只留本地，模型只获得 policy-safe result/excerpts，且 disclosure 可审计。
9. verifier 生成结构化 pass/warning/fail report，并对每个 check 显示 origin/independence/evidence level；UI 不把 `checks_passed` 表述为科学正确。
10. 只有 trusted attempt 能通过 output safety 和 commit-intent 流程创建 LinkedPlotArtifact、Point SelectionArtifact、scene feature 和静态 FigureArtifact v1。
11. 用户在地图点击 marker 打开结构化 time series，并能查看 spec、code、run、verification 和 trust/impact state。
12. 用户提交修改意见，形成 ReviewArtifact 和 FigureSpec v2。
13. v2 重新运行并生成新 linked/static artifacts，v1 仍可访问和比较；upstream 变化时两版的 impact 可独立重算。
14. MVP-A researcher trust walkthrough 通过，参与者不把 self-check 误解为独立科学验证。

### 18.2 Plot Studio MVP-B：Spatial field

Phase 5 完成需要：

1. agent 对二维场编写另一段分析代码，产生 `field.nc`、`overlay.png` 和 `layer.json`。
2. backend 验证 outer pixel edges、north-to-south rows、axis order、antimeridian parts、nodata alpha、PNG/field dimensions 和重采样声明后创建 SpatialLayerArtifact。
3. layer 的 units、style、source run、code、verification、materialized data ref 和 impact state 可检查。
4. MapScene 同时保存 layer refs、内嵌 features 与 selection/plot refs，backend 重启后可恢复。
5. Point/Polygon/LineString selection 能打开对应版本的 time series、T-S、profile 或 section。
6. fake-model offline E2E 与关闭后的 real-model reference eval 都达到各自 gate。
7. checkerboard/corner fixture 在 desktop/mobile 的 screenshot 和 canvas-pixel 验证通过，无上下翻转、偏半格或 antimeridian 错位。
8. MVP-B researcher walkthrough 能追溯 layer 的 source data、remapping/registration、code、checks 和 warnings。

### 18.3 Integrated Product v1

Phase 7 完成后才称为 Ocean Research Partner v1：

- 用户可以在不写代码的情况下完成论文阅读、evidence synthesis、研究问题 framing 和 hypothesis proposal。
- 当结论依赖计算时，agent 进入 code-transparent AnalysisRun，并保留完整 evidence chain。
- Plot Studio 同时支持 spatial layers 和 map-linked plots。
- Report Builder 只引用 exact refs，并在 preview/export 阶段重检 review、verification 和 impact state；stale/invalidated/source-unavailable 不能静默成为当前结论。
- conversation compaction 或 backend restart 不丢失 workspace、RequestRecord/terminal result、artifact versions、reviews、scene、已完成/interrupted run attempts 和 provenance，且不重复 domain mutation。
- artifact-producing 计算全部来自 allowlisted RuntimeProfile 和 trusted sandboxed run；unsafe output 无法通过审批或报告门禁。
- Dataset/derived artifacts 明确显示 materialization/reproducibility level，不把 fingerprint 误表述为已保存旧数据。
- workspace 的 ModelDataDisclosurePolicy 约束 dataset samples、paper text 和 diagnostics 进入模型 provider；本地执行不被误表述为数据绝对不离机。
- 所有第一方 skills 只规定研究过程与 Ocean quality gates，不包含 method-specific 执行 workflow。
- Integrated v1 researcher trust gate 通过，用户能区分 self-check、independent evidence、human approval 和 dependency impact。
- multi-agent 不是 v1 的完成条件，保持 feature flag 关闭也能完成整个研究循环。

## 19. 接下来立即执行的工程顺序

按以下顺序创建 issue/commit，避免同时铺开所有领域模块：

1. 完成 Phase 0 清理并恢复全量测试/build 基线。
2. 修复 compaction conversation ownership，并给 tool/turn stream events 增加 correlation fields，保持 v1 adapter 兼容。
3. 关闭 OD-06/OD-07；实现 caller-supplied RuntimeProfile、effect-aware scheduler、cancellable execution 和基础 sandbox/resource fixtures。
4. 执行 Phase 0.5：用 synthetic NetCDF 跑通 scoped coding -> sandbox -> numeric output -> marker-linked plot/checkerboard overlay。
5. 根据 spike 关闭 OD-01/OD-09/OD-11/OD-12，保留 ADR、security/disclosure/numeric/map fixtures 和第一次 researcher walkthrough 发现。
6. 关闭 OD-08；写 Protocol v2 Pydantic models、request/terminal/cancel/recovery fixtures 和 schema exporter。
7. 实现 durable RequestStore/idempotency journal，覆盖 mutation-commit/terminal-send crash window。
8. 生成 TypeScript union，实现 stdio/WebSocket adapters、event bus、TUI/graphical shell handshake 和 reducer contract tests。
9. 关闭 OD-03/OD-10；实现 SQLite migration、workspace snapshot、commit intents、immutable artifact versions 和 state/impact projections。
10. 打包 System Policy、当前 capability 允许的 Research Skills/Ocean References，并接入按需 context loading。
11. 把 spike executor 收敛为唯一 AnalysisRun path：attempt snapshots、resource policy、pre/post fingerprints、output safety 与 origin-aware checks。
12. 关闭 OD-02，冻结首个 real-model eval manifest 和 gate。
13. 实现 dataset inspect/sample/materialization UI，以及生产级 point time-series AnalysisRun、Point selection 和 linked plot。
14. 完成人工 review、FigureSpec/AnalysisPlan revision、impact 重算、版本比较和 MVP-A trust walkthrough。
15. 实现符合 pixel-registration contract 的 SpatialLayerArtifact 与离线 map overlay，通过 checkerboard/canvas-pixel tests 和 MVP-B walkthrough。
16. 用真实模型运行 point time-series 与 spatial-field reference eval，记录失败模式后再扩展分析覆盖面。

在第 16 项完成前，不开始 Literature UI、Report Builder 或 multi-agent 并发执行。
