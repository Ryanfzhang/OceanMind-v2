# OceanX 安装：一个 oceanx 环境

在仓库根目录执行。Python 3.11 是当前 OceanX 和核心依赖的要求。

```bash
conda create -n oceanx python=3.11 -y
conda activate oceanx
python -m pip install -r requirements.txt
```

这条安装命令同时安装 OceanX 主程序和科学计算依赖，不需要额外创建 `ocean` 环境，也不需要再执行 `pip install -e .`。
OceanX 默认科学计算环境名称已改为 `oceanx`。如果终端以前设置过旧环境覆盖，清除它：

```bash
unset OCEAN_SANDBOX_PYTHON OCEAN_CONDA_ENV
```

## Linux 系统组件

代码执行需要 bubblewrap、libseccomp 和可用的用户命名空间。这是系统组件，不是第三个 Conda 环境。
Debian/Ubuntu 上由有权限的管理员执行：

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

## 模型配置和启动

日常 OceanX 在前端设置 API 地址、密钥和模型；继续保留原有角色配置功能。
命令行部署可用 `ocean configure-models` 的 JSON 输入接口。
**benchmark 独立使用根目录 `benchmark.yaml`，不会读取或改写这些日常模型设置。**

运行 benchmark 不需要再安装此 `oceanx` 环境：只需按
[benchmark 安装教程](benchmarking/INSTALL.md) 创建 `oceanx-bench`，其中已经包含 OceanX。
只在既使用日常 OceanX 又运行 benchmark 时保留这两个环境。
