# Benchmark 安装和运行：一个 oceanx-bench 环境

所有命令从仓库根目录执行。本流程只需要 `oceanx-bench`：下载数据、运行 OceanX、监督 Claude Code、绘图和评测都使用它。

## 1. 安装一次

```bash
conda create -n oceanx-bench python=3.11 -y
conda activate oceanx-bench
python -m pip install -r benchmarking/requirements.txt
```

已包含 OceanX 主程序和科学依赖，无需另建 `ocean`，无需再执行 `pip install -e .`。
Linux 无 sudo 时，在当前 `oceanx-bench` 环境执行：

```bash
conda install -c conda-forge bubblewrap libseccomp -y
command -v bwrap
```

宿主机需允许用户命名空间；详见[OceanX 教程](../INSTALL.md#linux-系统组件)。
Claude Code 是外部 CLI，需预先安装并能执行 `claude --version`；它的科学计算同样使用本环境。

## 2. 只修改一份 YAML 切换模型

根目录有本地 `benchmark.yaml`；新 clone 时复制模板：

```bash
cp -n benchmark.example.yaml benchmark.yaml
chmod 600 benchmark.yaml
```

填写：

```yaml
model: deepseek-v4-pro  # 示例；填写供应商实际支持的模型 ID
oceanx_api: openai     # 可选 openai / anthropic
max_tokens: 32768
openai:
  url: "https://YOUR_PROVIDER/v1"
  api_key: "YOUR_KEY"
anthropic:
  url: "https://YOUR_PROVIDER/ANTHROPIC_COMPATIBLE_ENDPOINT"
  api_key: "YOUR_KEY"
```

OceanX 的所有模型角色读取 `oceanx_api` 对应条目。Claude Code 只支持 Anthropic 协议，始终读取 `anthropic` 条目。
**两条接口必须提供同一个 `model`，可以使用不同凭据。** 如果供应商只支持一种协议，可让 OceanX 也选择 `anthropic`，只填写这个条目即可；仅有 OpenAI 接口不能直接运行 Claude Code。
不再继承 `.bashrc` 的模型/接口/密钥，不读取 OceanX 日常 API profile，也不写回它们。无需 `source ~/.zshrc`。
`benchmark.yaml` 已被 Git 忽略；不要放到模型的数据目录、结果目录或提交到 Git。

启动前检查（无模型请求）：

```bash
python benchmarking/server/check_setup.py --agent both
```

检查输出包含接口地址、模型、科学 Python 和沙箱结果，不包含密钥。实际 API 可用性仍由供应商决定；先运行一题确认，而不是一次启动全部题目。
可用 `--agent oceanx` / `--agent claude` 单独检查。换配置文件可加 `--config /absolute/benchmark.yaml`，两个 runner 也支持同一选项。

## 3. 下载或继续下载

CMEMS 需要 `copernicusmarine login`；ERA5 使用 Google 公共镜像，无需 CDS 账号。
在两个终端激活 `oceanx-bench`，可同时执行：

```bash
python -u benchmarking/download/download_all.py public --output /import/home4/share/mafzhang --execute
python -u benchmarking/download/download_all.py services --output /import/home4/share/mafzhang --execute --workers 2
```

已校验文件跳过，Google ERA5 按小时续传。数据细节见[下载说明](download/README.md)。

## 4. 同一批 query，同时运行两组

先用一题确认，再扩大列表。不要把仍缺数据的题目加入批次：

```bash
python benchmarking/server/prepare_queries.py \
  --data-root /import/home4/share/mafzhang \
  --bindings /import/home4/share/mafzhang/_download_all/data_bindings.json \
  --tasks Q19 \
  --output ~/oceanx-bench-inputs/pilot.jsonl
```

两个终端均执行 `conda activate oceanx-bench`，然后分别运行：

```bash
python benchmarking/server/run_oceanx.py \
  --queries ~/oceanx-bench-inputs/pilot.jsonl \
  --output ~/oceanx-bench-runs/pilot
```

```bash
python benchmarking/server/run_claude.py \
  --queries ~/oceanx-bench-inputs/pilot.jsonl \
  --output ~/claude-bench-runs/pilot \
  --allow-tools Read Glob Grep Bash Write Edit NotebookEdit WebSearch WebFetch
```

无需 `--model`，模型统一来自 YAML。数据和 query 相同，输出目录分开。
两个 runner 自动选择当前 Python，检查科学依赖；OceanX 还会在模型调用前检查沙箱。
Claude 的工具列表是明确授权，其 Bash 没有 OceanX 的系统沙箱；使用只读数据挂载和独立结果目录。

## 5. 结果与重跑

每题结果在 `Qxx/attempt-*/`：查看 `answer.md`、图、`analysis.ipynb` 和日志。
OceanX 自动收集到 `collected/`；Claude 保存同样的回答/图/笔记本及原始事件。
`completed` 是对话完成状态，**不能当作科学任务成功或评分**；阻塞说明仍要判为未完成。
重新分析一份这样的回答请用新输出目录；`--resume` 会跳过运行状态已 completed 的题目。
正式评分见 [evaluation/README.md](evaluation/README.md)。不要修改历史结果来掩盖失败。
