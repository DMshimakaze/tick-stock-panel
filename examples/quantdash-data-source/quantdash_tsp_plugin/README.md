# QuantDash 外部数据源插件

QuantDash v1.2.6 通过 TSP 官方 Provider 协议运行在干净的 v0.3.32 宿主上。宿主基线为 `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`；插件实现来自个人开发分支 `e96354cb657aef32983061233444dd6acf71ec24`。

## 环境与启动

本地工程固定使用 conda `nga`：`D:\Python_Envs\envs\nga\python.exe`，SDK 为 `quantdash==0.1.0`，Polars 为 `>=1.44,<1.45`。沿用已安装的依赖、凭据和数据目录。

```powershell
Set-Location 'E:\tick stock panel\backend'
& 'D:\Python_Envs\envs\nga\python.exe' -m uvicorn app.main:app --env-file 'E:\tick stock panel\.env' --host 127.0.0.1 --port 3018
```

需要前端开发服务时，在 `frontend` 目录运行 `pnpm dev --host 127.0.0.1 --port 3011`。本地个人启动器可以负责启动这两个进程；官方 `dev.ps1` 没有 `-PythonExecutable` 参数。

## 安装与能力

通过「设置 → 导入与备份」安装外部 Provider ZIP，ZIP 根目录直接包含 `plugin.yaml`、`provider.py`、`client.py`、`mapping.py` 和本文。已安装并启用的 v1.2.6 继续使用，无需重复安装或重新填写 Key。插件更新使用官方安装、版本管理与重启流程。

| 能力 | 插件输入与宿主职责 |
| --- | --- |
| `daily` | 批量原始日K，`adjust='none'`；宿主负责存储、复权及指标 |
| `minute` | 原始 1m，北京时间 naive；最多 100 代码/批、12 日/窗口 |
| `realtime` | 股票、ETF 快照；指数补充与自选/详情定向补拉 |
| `adj_factor` | 真实事件的 `symbol,trade_date,ex_factor`；由宿主累计并投影 |
| `depth5` | 已授权的单标的五档聚合，数量单位为手 |
| `full_minute` | 复用官方 `get_minute` 修复轮，由宿主枚举及落盘 |

`client.py` 管理 SDK 与共享请求预算，`mapping.py` 转换字段、单位和时间，`provider.py` 实现 Provider 协议。日K和分钟K始终交付原始价；前复权使用官方 `adj_factor`、`enriched` 与分钟读取路径。

请求默认共用进程内 12 次/滚动 60 秒预算，预留 6 次给股票、ETF、指数快照；实时轮询建议至少 30 秒。五档按股消耗请求，预算不足结束本轮并报告缺失。全量分钟采用修复轮，其盘中并发时效需在交易时段验证；现有自动开关按用户配置保留。

## 复权与覆盖边界

沿用现有因子文件。源遗漏代码表示尚未核实，明确返回空列表才表示无事件；缺事件时保留已有数据，接受原始K线展示。只处理用户要求的样本，避免因缺因子扩大请求范围。

v1.2.6 保留的 `audit_adj_factors` 与 `adjustment_reference` 是插件内的诊断方法。旧分支的宿主统一审计接口、重试台账与定向历史重算补丁未移植；干净宿主使用原有同步入口。SDK 和行情数据分别受供应商许可约束，交付仅包含适配器源码、测试和脱敏结果。

## 定向验证

```powershell
Set-Location 'E:\tick stock panel\examples\quantdash-data-source'
& 'D:\Python_Envs\envs\nga\python.exe' -m pytest tests -q -p no:cacheprovider
& 'D:\Python_Envs\envs\nga\python.exe' .\tsp_quantdash_live_smoke.py --tsp-root 'E:\tick stock panel' --datasets daily
```

单元测试使用假 SDK/Polars，验证映射、预算和协议；原生加载及有界实拉使用真实环境，结果分别记录。复权回归读取 `600519.SH`、`588170.SH`、`159516.SZ` 的现有本地数据，验证官方公式、最新锚点与分钟读取。

停用、回退和卸载均使用官方插件管理器。旧开发分支、已安装旧插件版本和本地数据保留作回退基线。验收结果见 [`PLUGIN_ONLY_RESULT_20261107.md`](../../../docs/quantdash/PLUGIN_ONLY_RESULT_20261107.md)。
