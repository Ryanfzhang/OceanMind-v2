# 在 Linux 服务器下载 benchmark 数据

独立脚本：**不导入 OceanMind、不调用 LLM、不启动桌面端，也不改变多智能体交互**。把本目录复制到服务器即可运行；数据写到你指定的目录，不复制 CMOMS。

## 已接通与尚未接通

默认可下载三种产品，均通过公开 NOAA ERDDAP 下载，不需要账号：

| 配置 ID | 产品 / 版本 | 默认时段 | 默认空间范围 |
| --- | --- | --- | --- |
| `modis_monthly` | MODIS-Aqua 月平均叶绿素，约 4 km，R2022 产品系列 | 2003-01—2022-12 | **必须指定** `--modis-bbox W E S N` 或配置 `bbox` |
| `seawifs_monthly` | SeaWiFS 月平均叶绿素，约 9 km，R2018.0 | 1997-09—2010-04 | 104–120°E、1–25°N |
| `blended_wind_monthly` | NOAA Blended Sea Winds v2，月平均、0.25°，风速 / u / v / mask | 1997-09—2010-04 | 104–120°E、1–25°N |

这是**当前自动下载部分，不是所有非 CMOMS 数据已经齐备**。Argo/APEX、GMOG、Gulf 高度计/再分析、作者 NEMO、ETOPO5、细时间采样信息和模拟实际强迫仍在配置的 `pending` 中，附缺少的选择或获取条件。脚本不会替你选择 GLORYS、生成未经确认的论文浮标样本或用新水深产品替换 ETOPO5。`--products gulf_reanalysis` 等待定项会报错，不会假装完成。

MODIS R2018 的 OceanWatch 镜像元数据实际截止 **2022-04**，所以本脚本明确选用了可覆盖后续时段的 R2022 入口，**不是混用两个版本拼接**。ERDDAP 的产品系列可能继续更新，计划和下载文件会记录当前提供方元数据 / `processing_version`，每份文件另外有 SHA-256。它们不能自动视为原论文处理版本；所有参考数值应基于实际下载文件重算。月份完整性以时间轴检查为准，不只看目录标题或起止年份。

## 1. 安装

服务器需要 Python **3.10+** 和 `curl`。在复制过来的 `download/` 目录中运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python download_data.py --list
```

不需要安装整个 OceanMind。配置文件不存账号密码；当前三个公开入口无需 Earthdata / Copernicus 凭据。

## 2. 先预览，不下载数据场

下面 **104–121°E、1–25°N 只是 MODIS 下载外包矩形示例**，不是冻结的分析 mask。根据最终所需区域修改四个数。脚本保留框内全部原分辨率像元，不抽样、不平均、不插值；坐标中心落在框内的像元才会保留。

```bash
.venv/bin/python download_data.py \
  --output /data/oceanmind-benchmark \
  --modis-bbox 104 121 1 25
```

预览会联网读取产品定义和真实坐标轴，写入 `_runs/<run-id>/` 下的计划与报告，但不下载叶绿素/风数据场。它检查：

- 变量和维度是否存在，纬度是否倒序、经度是 0–360 还是 −180–180，风的额外高度轴是否为指定值。
- 所请求的每个月是否真的在时间轴中；不会把缺失月份匹配到相邻月份。
- 每个文件的实际时间戳、网格范围、变量、来源 URL 和输出路径。月平均的时间戳不一定是月初。

**发现缺月时退出码为 2**，具体月份写在 `missing_periods`。这不是 Python 崩溃，也不代表要填零。确认允许先下载其余月份后，使用下一步的 `--allow-missing`。

## 3. 下载 / 中断后继续

```bash
.venv/bin/python download_data.py \
  --output /data/oceanmind-benchmark \
  --modis-bbox 104 121 1 25 \
  --execute --allow-missing
```

去掉 `--allow-missing` 时，任一所选产品的时间轴有缺月，就在数据传输前停止；有其他预检查错误时，即使加了该参数也不开始传输。允许缺月只允许下载存在的文件，**不会自动补齐、填零或把不完整档案标成完成**；退出码仍为 2。

按月份切成独立 NetCDF 文件，串行下载以减少服务器压力。每次请求有连接 / 总时限和最多 3 次重试。完成后校验 NetCDF 能否完整读取、变量、维度、坐标端点和实际时间，记录有效值数量及 SHA-256。全缺测月份会保留并记录有效数为 0，不伪造观测。

这里的断点续传是**按已完成月份续下**：已通过哈希检查的文件跳过；`.nc.part` 是尚未完成的单月文件，重跑时仅重下这个月。动态 ERDDAP 响应不保证支持 HTTP Range，所以不承诺从半个文件的字节位置续传。已有文件若损坏、缺少回执或与请求不符，脚本会报错，**不会覆盖你的文件**；人工检查并移走冲突文件后再重跑。同一输出目录有进程锁，防止两个下载进程互相覆盖。

后台执行示例（日志先保存在当前目录）：

```bash
nohup .venv/bin/python -u download_data.py \
  --output /data/oceanmind-benchmark \
  --modis-bbox 104 121 1 25 \
  --execute --allow-missing > download.log 2>&1 &
tail -f download.log
```

## 4. 单产品 / 小范围测试 / 修改时段

只下载已有固定范围的 Q23 风数据：

```bash
.venv/bin/python download_data.py \
  --output /data/oceanmind-benchmark \
  --products blended_wind_monthly --execute
```

首次运行建议先测试一个月、一度见方（**测试数据不能冒充完整 benchmark 数据**）：

```bash
.venv/bin/python download_data.py \
  --output /data/oceanmind-smoke \
  --products modis_monthly --bbox 116 117 18 19 \
  --start 2022-12 --end 2022-12 --execute
```

`--bbox` 只允许用于一个显式选定的产品。`--start` 和 `--end` 必须一起提供，并覆盖本次所选产品的配置日期；不要给多个传感器设置它们不共有的年代。`--limit 1` 可仅下载每个产品第一个可用时间片，报告会明确 `limited: true` 和 `complete: false`。

如果 Q22 确定需要 2002 年 MODIS 数据，可在单选 MODIS 的命令中加 `--start 2002-07 --end 2022-12`，或者修改配置。不要写 2002-01 然后假定不存在的早期月份有数据。Q05/Q24 直接使用共同档案中的 2011–2022 子集，不另复制一份。

## 输出与复现记录

```text
/data/oceanmind-benchmark/
├── modis_monthly/
│   ├── 2011-01_<request-hash>.nc
│   └── 2011-01_<request-hash>.receipt.json
├── seawifs_monthly/
├── blended_wind_monthly/
└── _runs/<run-id>/
    ├── modis_monthly.plan.json
    ├── seawifs_monthly.plan.json
    ├── blended_wind_monthly.plan.json
    └── report.json
```

哈希后缀区分数据集、版本、坐标范围和变量选择，避免改变参数后覆盖不同子集。计划包含提供方完整元数据及坐标快照；回执包含来源、字节数、SHA-256、有效值计数。每次运行保留报告，数据文件不为审计额外复制。退出码：`0` 为所选操作成功（可能仅预览或测试），`1` 为错误，`2` 为时间覆盖缺失，`130` 为用户中断。是否得到全部请求数据应查看 `mode`、`limited`、各产品及总 `complete`，不能只看退出码。

下载矩形不是最终研究区：海陆、陆架、断面、近岸 / 深盆 mask 仍需按准备表冻结。月平均文件的缺测标记只支持月尺度有效覆盖统计，不提供月内采样日期。该脚本不进行重网格、科学 QC 筛选、浮标样本选择、缺测填补、论文检索或标准答案生成。

## 测试与来源

2026-09-06 实测：三个入口各下载了一份一度见方、单月 NetCDF，变量 / 坐标 / 时间及完整读取校验均通过；重跑 MODIS 样本确认跳过已验证文件。未下载完整科学数据集。完整计划的时间轴检查结果：MODIS 2003–2022 为 **240/240 月**，Blended Winds 为 **152/152 月**；SeaWiFS 为 **148/152 月**，该镜像缺 **2008-02、2008-03、2008-06、2009-05**。这是提供方目录缺月，不是脚本会自动修复的问题；可先下其余月份，之后另行确认是否需追查同版本原始文件。脚本每次都会重新检查，不把本次清单硬编码成永久事实。

离线测试：

```bash
.venv/bin/python -m unittest discover -s . -v
```

覆盖缺月、真实月时间戳、倒序纬度、0–360 经度、额外高度维度、输入错误、不同子集文件隔离、部分文件重试、哈希校验及 HTML / 错误 NetCDF 拒收。

已核对的提供方文档（入口状态仍会在每次运行时重新检查）：

- [ERDDAP griddap 请求语法](https://coastwatch.noaa.gov/erddap/griddap/documentation.html)
- [MODIS R2022 月产品定义](https://coastwatch.pfeg.noaa.gov/erddap/info/erdMH1chlamday_R2022SQ/index.html)
- [SeaWiFS R2018 月产品定义](https://oceanwatch.pifsc.noaa.gov/erddap/info/sw_chla_monthly_2018_0/index.html)
- [NOAA Blended Sea Winds 月产品](https://coastwatch.noaa.gov/erddap/griddap/noaacwBlendedWindsMonthly.html)
