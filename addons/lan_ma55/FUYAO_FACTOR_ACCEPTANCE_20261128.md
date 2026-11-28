# MA55 Fuyao 除权因子定向验收（2026-11-28）

正式 TSP 继续运行 MA55 **0.13.9**。Fuyao 对原来缺因子键的 26 只股票均返回了匹配代码的日K，**完整因子覆盖 0 只，确认无事件 0 只，实际修复 0 只**。沪深主板各 1 只、创业板 4 只、科创板 16 只、北交所 4 只继续显示 `FACTOR_PENDING`。

## 数据来源和接入结论

- 正式宿主的 `adj_factor` 选用 Fuyao，日K和分钟K选用 TickFlow。MA55 的独立采集器通过 TickFlow `ex_factors` 获取因子。此前已保存的 TickFlow 原始回执缺少全部 26 个证券键。
- 本轮实际取得 29 次逐股事件回执、5 次历史日K批量回执、1 次制品授权和 1 次因子制品下载，全部 HTTP 200。26 个目标均按现行完整代码查询上市日到目标日，业务码为 `3002`，且 `data:null`。2026-11-27 版事件制品同样没有这些证券的记录。5 个交易日的有效价格覆盖全部 26 只，仍缺逐股事件覆盖确认。[Fuyao 事件契约](https://fuyao.aicubes.cn/docs/api-reference/corporate-actions/) · [Fuyao 历史日K契约](https://fuyao.aicubes.cn/docs/mcp/tools/get_a_share_prices_historical_batch/)
- 沪市和北交所正对照共返回 8 条逐股事件，与制品中的相同日期和基础条款逐条匹配。两个跨除权日样本用原始前收盘与按分舍入的参考价重建单次比值，和 TickFlow 的比值差均小于百万分之一。Fuyao 历史接口在这两个样本除权前的全部观测点采用每股现金减法，与 MA55 的比例投影不同。深市负对照的既有事件早于本轮窗口，窗口内原价与前复权一致。
- TSP v0.4.1 的公开 API 与扩展上下文没有供 MA55 消费宿主规范因子事件链的接口。`tsp_factors` 的沪市、北交所研究计划预览均返回 200，预览只返回计划。公开的独立 Fuyao 适配器仍要求证券覆盖证据。官方 Provider 的空表也可由制品或行情读取失败产生。两条路径均无法为本轮 26 只提供合格证据，因而本轮仅完成诊断和正式页面验收，保留现有安装包。

## 正式验收

目标为 **2026-11-27 15:00（北京时间）**，620 只的证券集合与六周期状态逐项一致。原生 15m 到达 619 只，尚缺 30 只就绪包括因子待核实 26、回放缺柱 2、已核实停牌 1、历史零量 1。

| 周期 | 正式就绪／620 | 本轮实际恢复 | 26 只中上市至目标最多仍不足 150 柱 |
| --- | ---: | ---: | ---: |
| 15m | 590 | 0 | 0 |
| 30m | 589 | 0 | 1 |
| 60m | 588 | 0 | 2 |
| 120m | 585 | 0 | 8 |
| 日K | 575 | 0 | 12 |
| 周K | 497 | 0 | 17 |

正式浏览器逐周期点击了缺因子和正对照样本，并点击深市、北交所对照及 MA55 均线开关。缺因子样本显示原价和待核实，已有有效链的对照显示 TickFlow 前复权。管理器显示 0.13.9 已加载，既有 0.13.8 回退可用，VolPulse 0.1.0 页面正常打开。0.13.9 当前运行自 2026-11-28 11:43:46，安装包源码为 `9cb87c68e6ab5260cc00ca06b3f6411fc72bf40c`。只读计数与上一轮相同，包含冻结事件和风控、原因子、原生 15m 及原始日K。本轮周末验收使用最近完成目标。宿主最新任务的 `sync_daily` 失败单独留证。

`nga` 定向回归通过 15 项，其余 35 项被筛除。相关模块 Ruff 和 `git diff --check` 通过。当前变更为本摘要，官方 v0.4.1 宿主核心、VolPulse 和 MA55 冻结算法均为零差异。

```text
python -B -X utf8 -m pytest -q -p no:cacheprovider --basetemp <scratch> addons/lan_ma55/tests_market/test_collector.py addons/lan_ma55/tests_backend/test_native15.py addons/lan_ma55/tests_backend/test_workbench_owner.py -k factor
python -B -X utf8 -m ruff check --no-cache addons/lan_ma55/backend/collector.py addons/lan_ma55/backend/native15_store.py addons/lan_ma55/backend/market_grid.py addons/lan_ma55/backend/runtime.py addons/lan_ma55/tests_market/test_collector.py addons/lan_ma55/tests_backend/test_native15.py addons/lan_ma55/tests_backend/test_workbench_owner.py
git diff --check
```
