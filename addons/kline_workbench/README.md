# 兰 K线工作台｜lan.kline 3.0.0

只读 lan.marketdata HTTP v2 的15m及按查看需求聚合的30/60m，通过既有官方 Ext Data/16槽需求桥显示。后端没有 SDK、行情写库或独立采集器；官方宿主固定 v0.3.36，Page Pack v1。

- 缓存容量为15m≤250，30m≤125，60m≤62。缺口显示空白，不跨午休或交易日拼造。只有15m足够233根时显示末尾真实MA233；30/60m MA233隐藏并明确数据不足，MA55同样要求连续窗口。
- MA55/题材名称链接默认打开15m，保留原 event_id、确认/支撑/防守、来源、因子与风险。图中分别定位支撑测试和原确认结束点；不能证实当前价格基准一致时保留原防守文字而不画价格线。
- 只展示公共包的已保存 OHLC/MA，浏览器不重算权威行情。不等待完整250根才画部分历史；源错误保留当前有效图，恢复后清除错误。
- QUANTDASH_LEGACY 历史明确注明迁移来源和重建状态；新数据标记 TickFlow，不把旧历史冒充新供应商。

构建：pnpm --dir addons/kline_workbench build，随后由 nga Python 运行 build_backend.py。与公共包2.0.0、MA55 0.4.0同步安装、重启和刷新页面。原 workbench.sqlite3 与 rolling.duckdb 不删除；本包不再读写它们。

验证：pnpm test；nga Python 的 unittest addons.kline_workbench.tests.test_backend；实际图表/深链和只读服务异常另做现场验证。官方管理器 previous_version 和一致性备份提供回退。
