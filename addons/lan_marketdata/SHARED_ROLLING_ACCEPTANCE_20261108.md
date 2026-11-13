# 三包集成验收｜2026-11-08

正式安装运行：**lan.marketdata 1.0.1、lan.ma55 0.3.2、lan.kline 2.0.0**。公共DuckDB是唯一自有行情writer；MA55 SQLite保存信号/状态；工作台发布只读视图。

目标分支 `feature/tsp-shared-rolling-marketdata-ma55-r1-20261108`，个人Fork `DMshimakaze/tick-stock-panel`；起点 `c476c87546c2cda030305a6bb9f477f1141a54ad`。宿主基线 `ca3a44252edb414e3f4c1df1b7c381bad9f55c8b`（v0.3.32）。冻结R1源为研究main提交 `5d1d0f6e8913f528110c93e054429f050b9851f6`，由GitHub只读取出；研究工作区原有修改保留。

## 数据、算法和旧库

- SQLite backup取得一致快照。导入5,578只股票的891,202根完成15m，以及原工作台3股真实5m/可信日K。指定样本预热后库存897,238根、5,609个证券/周期序列，超容量0；停机只读SQL核对物理DuckDB为165,687,296字节。
- 公共采集器对000006.SZ、002261.SZ、588170.SH一次批量返回各3072根5m，窗口2026-08-03—11-06。各股落盘5m144、15/30/60m各256；60m MA233与冻结研究源码Indicators复算误差小于1e-12。ETF tick=.001、股票=.01，均取得显式因子覆盖。
- 首次真实冒烟3次HTTP：元信息、因子、5m各1。ETF首次查看暴露重复冷预热，修复并回归于1.0.1；修复前多出ETF两次5m。公共采集器现场累计5次HTTP、3次5m、0次429、0次1m。已预热ETF复访不重抓。
- 旧MA55在开发、切换前正常续跑，其原检查点增加20次5m；回退演练旧工作台为一个未跟踪图表标的补元信息/因子/5m/日K各1次。这些既有后台及回退流量与新插件冒烟分开记录。原1m表4800根不变，新增请求审计仅5m和日K。
- 正式扫描5,891/5,891：有效5,365、预热不足68、缺口458、失败0。全部5,891个工作状态为R1/revision4，物理schema仍为1。股票分母不受16个图表会话槽限制。
- 19,086条旧事件逐条比较event_id、源版本、first_seen_at和完整payload，变化/缺失0。正式库20,513条事件，新增1,427条R1历史重放。拓维11/4仅11:15有R1确认，11:00、11:30无新增确认；来源REPLAY。
- 旧event_risk缺失0；130条仅更新data_pending_at，其中126条增加PRICE_BASIS_CHANGED原因，其余风险事实未变；保存时updated_at正常刷新。旧首次确认不被风险状态更新改写。11/4历史视图保留3,458条WATCH状态、459条缺口及真实扫描5,890/5,891进度，未把旧未完成轮次冒充COMPLETE。
- MA55不启动原collector，不再生成recent_bar/collection_journal行情；工作台ZIP不含collector/store，不启动SDK或行情SQLite writer。历史源文件留作离线导入/回退。

## 实机、认证和回退

- 从MA55名称打开000006.SZ的真实15m，携带确认10:15、支撑09:45、防守价和因子。蜡烛、量柱、MA55/233、原结束点箭头与防守线可见；30/60m切换不重新生成BUY。
- 最终0.3.2实机从拓维信息R1历史名称直达15m：11/4 11:15确认、11:00支撑、防守20.84；256根真实K、MA55/233、成交量、原结束点箭头及防守线均可见。关闭第二个ETF图表后后台继续运行。
- 新股票深链无需搜索；ETF显示三位报价精度和60m MA233。快速切股/双页面由请求序号、client和scope隔离；真实会话与截图保存在本机证据目录。
- 官方管理器实际回退MA55到0.2.10、工作台到1.1.2并停用公共包，再恢复最终三包。旧工作台原行值无删除/改写；回退仅增加上述一个标的历史。最终MA55上一版0.2.10，工作台上一版1.1.2。
- 真实宿主中间件在独立loopback HTTP服务验证：无密码200；有效read:ext Token访问扩展路由403；启用密码后无会话401；官方登录会话200。生产密码未改。未注册API在此宿主可能返回SPA HTML/200，消费者明确报数据源不可用。

## 定向验证

Python使用已有nga，Node/pnpm与SDK依赖沿用现有安装。均退出0。

| 实际命令 | 结果 |
| --- | --- |
| `-m pytest -q -p no:cacheprovider addons/lan_marketdata/tests_backend` | 19 PASS；含530交易日落盘裁剪/重启、事务回滚、API、429、ETF复访和认证 |
| `-m pytest -q -p no:cacheprovider addons/lan_ma55/tests_backend` | 63 PASS，0 SKIP；挂载只读R1归档、冻结包及拓维输入 |
| `-m unittest addons.kline_workbench.tests.test_backend` | 4 PASS |
| `node --test addons/lan_ma55/tests/results.test.mjs addons/lan_ma55/tests/page.test.mjs` | 13 PASS |
| `pnpm --dir addons/kline_workbench test` | 20 PASS；保留的旧工具测试不作为生产采集证据 |
| 两页面pnpm build、三套build_backend.py | PASS；官方预检/安装200，enabled与runtime_version一致 |
| 相关ruff check、git diff --check | PASS |
| 官方基线对backend/app、frontend/src、dev.ps1差异 | 空 |

R1覆盖15正向、7抑制/噪声、2负窗口、逐根特征/结构/事件及拓维256根滚动/JSON重启。源码对照仅使用Git自身blob标识。

## 实际覆盖与证据

全池15m复用了近期历史；完整60m MA233只对已预热证券成立。312个非快照股票缺当前上市状态证明、47股缺tick，保留分母并暂停不可靠BUY；其余缺口/预热按实数展示。因子缺响应为FACTOR_PENDING。

本轮为周日，真实图表截至11月6日。盘中完成点推进以定向模拟验证，尚无本轮未来交易时段现场证据。全市场深历史未在验收中刷满，继续由有界可恢复队列推进。

本机 `%TEMP%/tsp-shared-marketdata-20261108` 保存一致旧SQLite快照、before-counts.json、real-samples.json、migration-check.json、runtime-acceptance-final-bounded.json、final-bounded-inventory.json、reference-ma-check.json、rollback-verified.json、JUnit和ui-*.jpg。最终R1链路截图为ui-ma55-r1-history.jpg、ui-tuowei-r1-15m.jpg。物理文件字节数与K行数分开记录。行情、库、ZIP、截图、日志、凭据、SDK及用户任务文件均不提交。

有界三证券冒烟结束后，移除本机collection_symbols验收限制并重启，恢复FULL_MARKET生产调度；warm_scope仍为FULL_MARKET_BOUNDED_PAGES。该阶段计数另存full-market-startup.json，不能把后续正常队列请求混入上述5次冒烟计数。

回退顺序：官方管理器停用lan.marketdata，回退MA55 0.2.10及工作台1.1.2并重启。需要恢复升级前工作状态时，停机另存当前R1信号库，再恢复一致旧MA55 SQLite快照；保留两份。原工作台库和QuantDash Provider持续保留。
