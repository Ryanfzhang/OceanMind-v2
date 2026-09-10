# OceanX benchmarking

30 个题：6 个基础分析（Q01–Q06）、18 个 autoresearch（Q07–Q24）、6 个 idea/假设检验（Q25–Q30）。当前非 CMOMS 子集是 **Q13–Q24、Q28–Q30，共 15 题**。

## 目录：每类内容只在一个地方维护

| 目录 | 内容 |
| --- | --- |
| [download/](download/README.md) | 两阶段下载入口；`public_manifest.json` 是非 CMOMS 产品/变量/时段/题目映射的唯一机器清单 |
| [server/](server/README.md) | 环境依赖、共享目录接入、生成 query JSONL、OceanX 与 Claude Code 批量运行 |
| [tasks/](tasks/) | 每题的英文 `task_info.json`；`target_study/checklist.json` 和参考图仅供评判 |
| [evaluation/](evaluation/README.md) | 评分协议、通用 idea 评分项、结果打包和 judger |
| [preparation/](preparation/DATA_PREPARATION.md) | 数据与原论文对照表，以及 [idea 题的 related work](preparation/IDEA_TASKS.md) |
| tests/ | 所有 benchmark 测试；不混在下载/评分入口旁边 |

不把原始 NetCDF、运行结果、缓存或 API Key 放进本目录；共享数据只保留一份。不要将整个仓库或 `target_study/` 挂载给被测 agent。

## 下载到 /import/home4/share 后怎么跑

前提：两个下载阶段成功，服务器 OceanX/科学 Python 环境和 API 已配置，
`ocean doctor` 与 `ocean sandbox-self-check` 通过。所有命令从仓库根目录运行。

```bash
# 下载完成后，对现有文件做一次完整校验
python benchmarking/download/download_all.py verify --output /import/home4/share

# 生成 Q13–Q24 的原始英文 query + 共享目录引用，不复制数据
python benchmarking/server/prepare_queries.py \
  --data-root /import/home4/share --preset autoresearch \
  --output "$HOME/oceanx-bench-inputs/autoresearch.jsonl"

# 正式启动 OceanX；此步骤会调用已配置的模型并消耗 API
python benchmarking/server/run_oceanx.py --queries "$HOME/oceanx-bench-inputs/autoresearch.jsonl" \
  --output "$HOME/oceanx-bench-runs/autoresearch-r1"
```

`--data-root` 必须是下载时 `--output` 的同一个目录。若使用了
`/import/home4/share/ocean-data` 子目录，上面也要改成这个子目录。
运行结果保存在自己的目录，不放入共享变量目录。

## “能运行”不等于“已经能正式评分”

| 范围 | 下载齐后还需要什么 |
| --- | --- |
| Q13–Q24：12 个 autoresearch 题 | OceanX 模型配置和服务器执行环境检查通过，即可开始运行 |
| Q28–Q30：3 个 idea 题 | 还需分别配齐 2 / 3 / 3 篇已选 related-work 全文；`--preset non-cmoms` 会检查缺失 |
| Q01–Q12、Q25–Q27 | 依赖 CMOMS，不包含在这两条下载命令里 |
| 正式评分 | 独立数值参考答案/容差和输入指纹仍需验证；当前评分文件是 `draft`，不能直接当最终标准答案 |
| Claude 对照实验 | `server/run_claude.py` 可用同一 JSONL 逐题运行 Claude Code + DeepSeek 并保存结果；跨系统打包/评分仍待接入 |

完整的安装、单题试跑、15 题运行、续跑和结果位置见 [server/README.md](server/README.md)。
结果打包与评分命令只在 [evaluation/README.md](evaluation/README.md) 维护。

## Query 与评分版本

非 CMOMS：`2026-09-08-accessible-v1`。Q13–Q22 是公开数据上的论文方法简化版，
其原论文截图保留为 context only，不要求复现原航次或原文数值；Q23/Q24 保留对应公开产品分析。
Q28–Q30 不再依赖作者重建文件或原始浮标/glider 数据。

CMOMS/base 任务保持原版本和设计。每个 query 的文字、论文依据及评分项分别在该题目录里。
旧运行必须保留原 query/data 哈希，不得改标签后混入新版结果。所有 30 个评分文件仍是草案。

## 测试

```bash
python -m pytest benchmarking/tests tests/test_oceanx/test_batch.py
```

这是代码/协议测试，不调用付费模型、不证明论文结论或服务器环境可用。
