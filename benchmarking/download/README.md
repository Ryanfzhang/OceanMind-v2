# 两条命令下载全部非 CMOMS benchmark 数值数据

这是 **2026-09-08-accessible-v1** 版本的统一入口。已同步简化 Q13–Q22、Q28–Q30，去掉无法直接获取的原始航次/作者数据依赖；Q23/Q24 保留可公开获取的产品。CMOMS 不下载、不转换、不复制。

## 首次安装与账号准备（只做一次）

统一环境安装见 [benchmark 教程](../INSTALL.md)。下载与测评都在 `oceanx-bench` 中运行。

服务器需已有 `curl`，不需要 sudo。CMEMS 需要账号，ERA5 改用 Google 公共镜像匿名下载：

- 运行 `copernicusmarine login`，本地保存 Copernicus Marine 凭据。
- ERA5 无需 `.cdsapirc` 或 Google 账号，直接读取 [Google ARCO-ERA5](https://github.com/google-research/arco-era5) 的 HTTPS 数据块。
- 不把凭据放进数据目录、仓库或聊天。无需 Earthdata 账号，也不需要作者给原始 glider 文件。

账号未配置、网络不通或提供方服务异常不能靠脚本绕过；会报错并保留已验证的文件，重跑即可继续。

## 实际下载：这两条命令

选择一个新的空数据根目录，避免与旧版不同区域/版本的档案混放。以下都从仓库根目录运行：

```bash
python -u benchmarking/download/download_all.py public --output /srv/ocean-benchmark-data --execute
python -u benchmarking/download/download_all.py services --output /srv/ocean-benchmark-data --execute
```

将 `/srv/ocean-benchmark-data` 换成你有写权限的目录，例如 `/home/mafzhang/ocean-benchmark-data`。

**不需要再填写产品 ID、变量、年份、经纬度或逐个 query 下载。** [public_manifest.json](public_manifest.json) 已固定这些要求并合并重复需求：

| 阶段 | 下载内容 | 主要服务的 query |
| --- | --- | --- |
| public | MODIS 2003–2017、NOAA 月风 2003–2012、OISST 1982–2023 | Q19–Q24、Q28 |
| services | 墨西哥湾 GLORYS 2011–2017；东海 GLORYS + ERA5 的 1993–2011 基线和 2023 事件期 | Q13–Q18、Q24、Q29–Q30 |

两阶段均成功后覆盖 **15/15 个非 CMOMS query 的数值数据需求**。这是当前简化版的数据覆盖，不是旧版原始航次复现，也不代表评分答案已生成。

下载规模较大，特别是 OISST 原始日文件和 GLORYS 多年三维场。OISST 每天获取一次原始全球文件，裁出研究区并分别保存 SST/海冰，随后清除本次临时原文件；不会长期保存一份额外全球副本。下载保持原始 packed 数据、时间分辨率和缺测标记，不做平均或插值。网络传输量会大于最终区域子集。

## 目录和完成判定

```text
ocean-benchmark-data/
  MODIS_Aqua/chlorophyll/2003/*.nc
  NOAA_Blended_Wind/windspeed/2003/*.nc
  NOAA_Blended_Wind/u_wind/2003/*.nc
  NOAA_Blended_Wind/v_wind/2003/*.nc
  NOAA_Blended_Wind/mask/2003/*.nc
  OISST/sst/1982/*.nc
  OISST/ice/1982/*.nc
  CMEMS_Gulf/thetao/2011/*.nc
  CMEMS_Gulf/so/2011/*.nc
  CMEMS_Gulf/zos/2011/*.nc
  CMEMS_ECS/thetao/1993/*.nc
  CMEMS_ECS/uo/1993/*.nc
  CMEMS_ECS/vo/1993/*.nc
  ERA5/ssr/1993/*.nc
  ERA5/str/1993/*.nc
  ERA5/sshf/1993/*.nc
  ERA5/slhf/1993/*.nc
  _download_all/coverage.json
  _download_all/data_bindings.json
  _download_all/masks.json
```

Gulf/ECS 分开命名，避免同一个 CMEMS 变量目录混入不同空间网格。年目录下保留原生时间分辨率：月海色每月一份，OISST 每日一份，GLORYS/ERA5 每月一份且文件内仍逐日/逐小时。坐标和深度随变量保留。

查看 `_download_all/coverage.json`：
- `all_numerical_inputs_complete: true`：两阶段的所有数值文件已经通过下载时校验。
- 每题的 `missing_groups`：哪组未完成；不会把失败或缺月记成成功。
- 任一组失败，命令返回非零。重复相同命令按哈希跳过已完成文件；损坏或与请求冲突的文件不覆盖。

需要下载前查看固定计划，去掉 `--execute`，无需联网。需要下载后重新检查所有文件的请求/哈希：

```bash
python benchmarking/download/download_all.py verify --output /srv/ocean-benchmark-data
```

`verify` 不联网、不补下丢失文件；丢失/损坏返回失败。正常下载过程已执行 NetCDF、时间/变量校验；报告不等于科学参考答案验证。

## 运行 benchmark 时只指定数据位置

统一下载器同时生成全部 15 题的目录映射。以下命令保持英文 query 原文，只引用目录，不复制数据：

```bash
python benchmarking/server/prepare_queries.py \
  --data-root /srv/ocean-benchmark-data \
  --bindings /srv/ocean-benchmark-data/_download_all/data_bindings.json \
  --tasks Q13 Q14 Q15 Q16 Q17 Q18 Q19 Q20 Q21 Q22 Q23 Q24 Q28 Q29 Q30 \
  --output /srv/ocean-runs/non-cmoms.jsonl
```

然后使用现有 `ocean batch --queries ... --output ...`。Q28–Q30 的论文阅读包是另外的文献输入：在 bindings 的对应对象加入 `papers` 路径，并给两个被测 agent 相同授权全文。数值下载脚本不假装已经提供论文全文或 evaluator 标准答案。评分规则/原文参考图不能放进 agent 的输入目录。

## 代码与范围

- `download_all.py`：唯一推荐的 benchmark 批量入口，两阶段、固定清单、去重、逐题完成报告。
- `public_manifest.json`：所有产品、变量、时间、区域、题目映射，无待人工选择的数据产品占位符。
- `download_data.py` / `download_services.py`：底层适配器，也保留手动下载能力；不要把它们旧的默认选项当作当前 benchmark 全量入口。
- `ncei_oisst.py`：直接获取 NOAA 原始文件，不依赖缺日的 OISST ERDDAP 镜像。
- [数据与原论文对照表](../preparation/DATA_PREPARATION.md)：列明简化点和原论文差异。
- 同目录 `_collection.json` / 回执 / 计划仅保存元数据，不复制科学数据。脚本不改 OceanX 的 UI、Coordinator 或 Expert 运行链路。

GLORYS 固定 `202311` 版本，依据[官方服务状态表](https://marine.copernicus.eu/media/pdf/6713/open)。版本退役会失败，不静默换成另一产品。官方 SDK 的排队和重试不保证墙钟完成时间。

## 测试

```bash
python -m pytest benchmarking/tests
```

离线测试验证目录、清单闭合、任务/评分哈希、原始数据裁剪保真、重跑和失败状态，不代表已在服务器完整下载。CMEMS 需账号；公共入口也受网络和提供方服务状态影响。

## ERA5 Google 镜像与旧下载续接

命令、变量、时间范围、路径及请求指纹保持兼容。已有 CDS 文件和回执通过 SHA256 校验后直接跳过；仅缺失文件使用 Google 镜像。计划中的 `era5:reanalysis-era5-single-levels` 是保留的逻辑产品标识，实际传输来源写入新 NetCDF 和下载回执的 `source_url`、`download_provider`、`source_metadata`。

Google 数据保持每小时、0.25°、J/m²，不做插值、平均或符号转换；只取正式 ERA5 覆盖，不取 ERA5T。镜像与 CDS 可有微小编码差异，并非保证逐位一致。

每次读取一个全球小时块（实测约 3 MB），仅把区域子集保存到磁盘，传输量大于最终文件。每个变量/月在 `.nc.part.google` 中记录已完成小时，网络错误重试，重跑继续未完成小时；每天打印进度。完整月份验证后才生成正式文件和 SHA256 回执。不要删除 checkpoint 或新建数据根目录来重跑。CMEMS 并行逻辑不变。

本轮在线检查：NOAA 原始 OISST 文件返回 HTTP 200 / NetCDF；2023-08-01 的一度范围 SST/海冰裁剪及重复运行跳过均通过。该低纬度样本的海冰变量为缺测，原样保留，不把缺测伪造为零或用它误删有效 SST。此小样本不代表全部年份已下载。
# Parallel CMEMS / ERA5 downloads

Service downloads now default to two concurrent monthly chunks. For example:

```bash
python -u benchmarking/download/download_all.py services \
  --output /import/home4/share/mafzhang --execute --workers 2
```

`--workers 1` restores serial execution; a larger positive value explicitly requests
more concurrency. The standalone `download_services.py` supports the same option.
Only service chunks are parallelized: groups, public downloads and offline verification
retain their ordering. ERA5 reads Google objects directly without the CDS queue;
additional workers still depend on bandwidth and provider throttling.

Workers use separate processes for provider/NetCDF isolation. Only the parent writes
the group progress report; per-file receipts and hashes keep their existing format.
Verified existing files are skipped, corrupt/unmanaged files are not overwritten,
and individual failures are recorded while other chunks continue. Progress is a count
of completed/verified files, not a position in month order. A group is complete only
when all files succeed. Re-run the same command to verify existing files and download
the remaining ones. ERA5 resumes saved hourly checkpoints; other interrupted service chunks may need restarting.

The updated unified downloader allows one `public` and one `services` process at
the same time against the same archive. Run the two commands in separate terminals.
Each phase has its own lock; shared coverage/bindings/masks are refreshed under a
short lock from the latest reports. Duplicate runs of the same phase are rejected.
`verify` and standalone/legacy downloaders retain exclusive archive access.

同步新版 `download_all.py` 后，需要先停止并重启正在运行的旧版 ERA5 进程一次，
它仍持有旧的独占锁。不要删除锁文件。然后分别在两个终端执行：

```bash
python -u benchmarking/download/download_all.py services --output /import/home4/share/mafzhang --execute --workers 2
python -u benchmarking/download/download_all.py public --output /import/home4/share/mafzhang --execute
```

已完成文件仍校验跳过；ERA5 从小时 checkpoint 续传。并行下载会共享网络带宽。
