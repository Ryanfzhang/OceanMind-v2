# OceanX 安装：一个 oceanx 环境

在仓库根目录执行。`environment.yml` 在同一个 `oceanx` 环境中安装 Python 3.11、Node.js 24，并通过 `requirements.txt` 安装 Python 依赖。

首次创建环境：

```bash
conda env create -f environment.yml
conda activate oceanx
```

已有 `oceanx` 环境时，直接更新，不需要删除或重建：

```bash
conda env update -n oceanx -f environment.yml
conda activate oceanx
```

这条安装命令同时安装 OceanX 主程序和科学计算依赖，不需要额外创建 `ocean` 环境，也不需要再执行 `pip install -e .`。项目仍只使用 `oceanx` 和 `oceanx-bench` 两个环境；Node.js 与 Python 共用 `oceanx`。
OceanX 默认科学计算环境名称已改为 `oceanx`。如果终端以前设置过旧环境覆盖，清除它：

```bash
unset OCEAN_SANDBOX_PYTHON OCEAN_CONDA_ENV
```

## Linux 系统组件

代码执行需要 bubblewrap、libseccomp 和可用的用户命名空间。无 sudo 时，先在当前环境安装 Conda 包，无需第三个环境：

```bash
conda install -c conda-forge bubblewrap libseccomp -y
command -v bwrap
```

Conda 包来源：https://github.com/conda-forge/bubblewrap-feedstock 。宿主机仍需允许用户命名空间。
若选择系统包，Debian/Ubuntu 上可由管理员执行：

```bash
sudo apt-get install bubblewrap libseccomp2
```

其他发行版安装对应系统包。检查：

```bash
ocean doctor
ocean sandbox-self-check
```

以 `passed: true` 为准。失败时先处理输出中的具体原因，再启动研究任务。
macOS 使用对应系统沙箱，不安装 Linux 的 bubblewrap。

## 桌面前端

激活 `oceanx` 后检查 Node。macOS/Linux 的 `command -v node` 应指向当前 Conda 环境；Apple Silicon Mac 的架构应为 `arm64`。

```bash
command -v node
node -p 'process.version + " " + process.arch'
cd frontend/ocean-desktop
npm ci
npm start
```

如果之前手动把 Homebrew 或 Codex 的 Node 路径放在 `PATH` 最前面，请重新打开终端并激活 `oceanx`；若启动配置中也有此覆盖，请移除该覆盖。切换 Node 架构后必须重新执行 `npm ci`，以重装 esbuild、Electron 等平台依赖。该操作保留 `package-lock.json`，不会删除研究数据或后端 sidecar。

Node.js 由 Conda 管理，前端依赖由 `npm ci` 按 `package-lock.json` 安装；不要通过 `pip install nodejs` 安装 Node。

## 模型配置和启动

日常 OceanX 在前端设置 API 地址、密钥和模型；继续保留原有角色配置功能。
命令行部署可用 `ocean configure-models` 的 JSON 输入接口。
**benchmark 独立使用根目录 `benchmark.yaml`，不会读取或改写这些日常模型设置。**

运行 benchmark 不需要再安装此 `oceanx` 环境：只需按
[benchmark 安装教程](benchmarking/INSTALL.md) 创建 `oceanx-bench`，其中已经包含 OceanX。
只在既使用日常 OceanX 又运行 benchmark 时保留这两个环境。
