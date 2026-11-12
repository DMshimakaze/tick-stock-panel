# 兰 MA55 R1｜lan.ma55 0.4.0

生产路径：lan.marketdata HTTP v2 完整15m → SharedStore 消费收据 → 冻结 pipeline.process_symbol / engine → 原 state.sqlite3 → 官方扩展数据展示表。兼容官方 TSP >=0.3.36,<0.3.37，extension API / Page Pack v1。

唯一 BUY 规则仍为 MA55_15M_MINIMAL_V5_R1，原 engine.py、pipeline.py 与状态 revision4/物理schema1保持原样。要求真实 source_count=3、因子有效、完成时段和连续56根。250根缓存不替代跨窗口的结构锁、推动记忆或 volume slots；同源正常追加继续使用持久策略状态。

- 行情数据由公共包唯一生产，MA55不含 SDK 或采集器。只在公共15m revision改变后读取变更证券，收据、信号、状态和待发布记录原子提交，重启可幂等追赶。
- 信号库继续为 `data/user_data/lan_ma55/state.sqlite3`，signal_event 原确认 payload/event_id/首次时间不改；跨源或历史更正保留确认，在策略适配层重建窗口。
- 重叠核实的源切换继续保留因果记忆；未核实的切换仅使用同源的新窗口。旧锁不能复现时保持待验，只有锁之后真实的离开/失效证据才允许重新激活。源不可用时保留原锁及已保存结果，不生成新 BUY。
- 原 QuantDash Collector / 预算 / LiveStore 采集入口已移除。离线 OfflineService 和冻结测试继续只接收显式输入，不进入生产 ZIP。
- 历史 BUY 深链保留 event_id、确认、支撑、防守、因子及来源信息。已发布 summary/day/history 契约继续供 lan.theme 消费。

构建：先 pnpm --dir addons/lan_ma55 build，再用 nga Python 运行 build_backend.py。与公共包2.0.0及K线3.0.0一起通过官方管理器安装并重启。读取认证可用私有 api_session，官方 Ext Data 发布用现有配置；不把凭据发到浏览器。

测试：本目录 tests_backend / Node 测试；冻结研究 oracle 与私人样本仅从显式只读参考读取。缺少对应冻结包的用例标记 skip，不能算作真实 BUY 通过。运行数据、备份和商业价格不进 Git。
