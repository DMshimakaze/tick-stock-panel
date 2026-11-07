# TSP v0.3.32 + QuantDash 纯插件化验收

2026-11-07（北京时间）。**官方宿主核心源码差异为 0；沿用已安装的 QuantDash v1.2.6。**

## 交付与运行

- 官方基线：`ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`（v0.3.32）。
- 个人 Fork 分支：`DMshimakaze/tick-stock-panel:feature/tsp-plugin-only-v0.3.32`；保留规范提交 `2795ccf74e0fa6e3792c84bbde9b423b651273a9`。
- 插件源码提交：`1385c90147d1ad2100c9a949b88e7d69b782fda7`；从旧分支 `e96354cb657aef32983061233444dd6acf71ec24` 仅提取插件目录。本文随后单独提交，便于直接引用源码 SHA。
- 实际后端解释器：`D:\Python_Envs\envs\nga\python.exe`；`quantdash==0.1.0`、Polars `1.44.1`。后端 `127.0.0.1:3018`、前端 `127.0.0.1:3011` 均已运行，health 为 v0.3.32。
- 原生注册表显示插件 `available=true`；安装状态 `active=1.2.6`、`enabled=true`、无 pending。运行模块与清单和已安装版本一致，仅更新说明文档及 smoke 使用示例；未升级、重装或生成新 ZIP。
- daily、adj_factor、minute、realtime、depth5、full_minute 六项路由仍为 `quantdash_user`。保留用户实际设置，包括全量分钟自动刷新开启；未重新配置 Key。

## 实际验证

- `nga python -m pytest tests -q -p no:cacheprovider`（插件目录）：**47 passed，12 subtests passed**；使用假 SDK 的协议、映射、预算测试。
- 官方 `frontend` 执行 **一次** `pnpm build`：通过；既有 chunk 大小与 Browserslist 提示保留。
- 原生加载 smoke `--datasets daily`：真实有界请求返回 30 行，20 个量价样本无不一致，`SUMMARY: PASS`。
- 读取指定本地日分区及原有因子，调用官方 `_apply_adj_factor`；同时检查真实 API 和界面。日K API 三样本均为 HTTP 200、134 行 enriched，最新锚点误差为 0。

| 样本 | 本地事件边界日K | 官方复权计算最大误差 | 分钟与图表 |
| --- | --- | --- | --- |
| `600519.SH` | 3 行、2 个事件 | `2.28e-13` 以下 | 本地 241 根；最新价格不变，日K/分时显示正常 |
| `588170.SH` | 3 行、1 个拆分事件 | `0` | 官方按需读取 11-06 单日分钟，图表及最新锚点正常 |
| `159516.SZ` | 5 行、2 个拆分事件 | `0` | 官方按需读取 11-06 单日分钟，图表及最新锚点正常 |

缺因子的真实本地样本 `000002.SZ` 直接保留原始价，计算正常。ETF 无本地分钟分区，历史跨拆分分钟投影未验；本轮为休市日，盘中实时、五档及全量分钟的并发时效待交易时段验证。未发起全市场审计、历史重算或行情重拉，原始K线、因子、凭据和已安装包保持原样。

## 相对官方的差异文件（12 个）

- `AGENTS.md`（既有长期规范）。
- `examples/quantdash-data-source/quantdash_tsp_plugin/`：`plugin.yaml`、`provider.py`、`client.py`、`mapping.py`、`README.md`。
- `examples/quantdash-data-source/tests/`：`test_client.py`、`test_external_loader.py`、`test_mapping.py`、`test_provider.py`。
- `examples/quantdash-data-source/tsp_quantdash_live_smoke.py`。
- `docs/quantdash/PLUGIN_ONLY_RESULT_20261107.md`（本文）。

`git diff --name-only ca3a44252edb414e3f4c1df1b7c381bad9f55c8b -- backend/app backend/tests frontend/src dev.ps1` 无输出；`git diff --cached --check` 通过。Git 忽略的个人启动器改为直接调用 nga + uvicorn 和现有 Vite，已实际启动；本地 `AGENTS.override.md` 继续保留。旧分支、旧插件、回退包和原有未跟踪 `REVIEW_SUMMARY.md` 均保留。

## 恢复官方行为后的差异

- 旧宿主的统一因子审计、重试台账和配套历史重算入口未移植；使用官方现有因子同步及读取链路，接受源覆盖限制。
- 官方个股分析搜索为 `stock,index`，不含 ETF；已用自选搜索的独立预览正常查看两只 ETF，未添加自选。
- 五档 `DepthService.finalize()` 恢复官方逻辑：监控关闭不再额外阻断自动定版。原配置保留；若要求恢复旧联动，需要官方提供扩展点或调整宿主，当前未越界修改。
