# MA55 原生15m生产与人工复核候选验收（2026-11-13）

**已安装运行 lan.ma55 0.9.3 / lan.kline 3.3.0。MA55生产只请求原生15m；新输入实际驱动独立候选，旧行情、冻结BUY和风险保留。** 21:03回读为原生目标到达 **7,290/7,661**、实际候选分析 **5,602/7,661**、当日候选 **379**。本轮在收盘后执行，这379条均为REPLAY人工复核候选。

开发分支 `feature/tsp-ma55-native15-production-20261113`，起点 `8ca95893a6a27e4a4d123a8fb008a099845c6713`；开始时fetch与远端HEAD一致，原未跟踪 `REVIEW_SUMMARY.md` 保留。宿主仍为官方v0.3.36 / `a7868b783fc6057913bbc0a8d2b69817ad9b7d9b`。只读取得v0.3.37 / `40a967d50aca42de76fb163207fd1ee5b3c3fb9f`，相关ExtensionContext、扩展包及Page SDK契约文件无差异，未升级宿主。环境为既有nga、TickFlow SDK 0.1.25。

## 生产链与隔离

- `collector.py` 的生产 `fetch()` 使用 `klines.batch(period="15m",adjust="none",as_dataframe=False,batch_size=100,max_workers=1)`；交易日探测也使用完成原生15m。旧5m调用改名为显式离线 `fetch_five()`，生产worker无调用。
- `native15_shadow.py` 复用为输入规范化模块；真实收盘标签、完成余量、午休、去重冲突、OHLCV、量的手→股/份及金额元在边界校验，坏证券与同批其他证券隔离。SDK现装源码及[官方源码](https://github.com/tickflow-org/tickflow/blob/main/tickflow/resources/klines.py)均给出每HTTP最多100证券。
- 同一既有SQLite新增 `native15_bar / native15_instrument_progress / native15_signal_state / native15_candidate / native15_candidate_risk` 等命名空间。原生柱为 `TICKFLOW_NATIVE_15M / source_count=1 / NATIVE_15M_COMPLETED`；原 `bar15` 约束及计数3保留，没有覆写旧来源。
- `native15_signal_adapter.py` 直接复用冻结 `engine.minimal_features / engine.advance`，规则引用 `MA55_15M_MINIMAL_V5_R1 @ 5d1d0f6e8913f528110c93e054429f050b9851f6`。独立家族 `NATIVE15_REVIEW_V1`，保存输入合同、来源、基准/因子修订、支撑、确认、首次看到时间和风险。结构状态增量持久化；修订后旧锁须由真实历史复现，证据不足则保留锁并暂停该股新候选。
- `native15_owner.py` 在原单writer连接内原子提交行情、状态、候选和收据。`market_store.py / indicator_cache.py` 复用读取、投影和六SMA实现，来源分表隔离缓存；30/60m由连续2/4根原生15m生成。深历史不阻挡最新行情和最低候选预热。
- 新增只读 `/v1/candidates`；`/v1/events` 保持旧冻结BUY。`/v1/bars` 默认原生，`source=archive` 明确读取旧行情。新Ext Data为 `lan_ma55_native15_{summary,day,history,prices}_v1`，Theme旧BUY投影未注入候选。

## 调度与实际覆盖

A最新目标 > B约64根最低预热（至少56根连续合法输入）> C约1280根图表深历史。批次最多100证券。A/B使用原8 RPM总分钟池；C还受原2 RPM冷池约束。总配置上限12，未提高账号配额。持久last_attempt保证公平；失败300秒后同目标可重新入队，连续三次后延长冷却；429服从Retry-After，401/403阻断供应商调用。

生产从原生覆盖0起步，实际读到723/713、4419/3178，最终7290/5602。旧5m的3433/1726没有计入原生覆盖。固定15:00目标在收盘后持续增长，重启后继续原水位。后台不依赖网页存活；页面10秒只读发布结果。

21:03同一7661证券范围的互斥分类：

| 状态 | 证券数 |
| --- | ---: |
| 目标已保存且实际候选分析完成 | 5602 |
| 已保存目标，因子未核实 | 1567 |
| 已保存目标，连续策略预热不足 | 119 |
| 已保存目标，策略输入OHLCV无效 | 2 |
| 源端尚未返回目标K | 371 |

源端缺返回未被推断为停牌或退市。状态另提供未取得行情的因子缺失，因此总factor_pending为1930；它与上述互斥分类口径不同。MA233历史就绪495只，其余由C队列继续维护。

## 真实源与性能证据

维护窗口内关闭生产owner后，用同一SDK执行普通股票、旧BUY证券、除权股票、沪ETF、深ETF、新股缺因子及已知缺口七类样本：**3次真实HTTP均200，429=0，自动重试=0**，耗时406/282/312ms。原生15m长窗回放14:45、15:00两个真实完成目标，均保存7证券，4证券满足当前策略分析，产生25条可追溯REPLAY候选；因子和预热不足如实保留。这是收盘后有来源回放。

截至生产收据取样，原生15m **103 HTTP**、因子87 HTTP，全部200；生产5m/1m请求均0。原生批次平均86.63、最大100，滚动60秒峰值8；HTTP延迟p50/p95约328/469ms。因子峰值13 RPM，平均17.56证券/请求。响应未提供配额头，账号总共享池仍为UNKNOWN。官方公开接口回读行情1.0秒、全量分钟3秒，原设置保留。

同机100证券×1280根、两个新增桶，比较SDK→解析/合法性→写入/扫描/缓存→Ext Data全链。合成HTTP无网络时延，且两条路径均有cProfile：

| 路径 | 两轮全链耗时 | SDK解析/合法性 | HTTP数 |
| --- | --- | --- | ---: |
| 原5m生产路径 | 3.033 / 3.531s | 13.11 / 12.02ms | 2 |
| 原生15m路径 | 2.370 / 2.261s | 8.14 / 8.12ms | 2 |

平均全链3.282→2.316s，约减少29.4%；请求数相同。新路径每轮100股写入/缓存约1.40/1.44s，候选扫描约0.323/0.302s，提交约0.604/0.474s，Ext Data约5ms。真实网络延迟使用前述独立收据。

5600证券×1280根联合负载复用现有官方quote1s、全量minute3s模拟、HTTP读取与最多64条SSE，商业HTTP=0：

| 新目标 | 保存/分析 | 计算墙钟 | 请求节奏 | 保守相加 | 100股事务p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 5600/5600 | 225.24s | 440s | 665.24s | 2495.79ms |
| 2 | 5600/5600 | 252.63s | 440s | 692.63s | 3511.29ms |

两轮均低于900s目标。1/4/8/16客户端bars+events读取p95为109/262/724/1018ms，无读取错误。扫描阶段100股批次p95约244ms。峰值RSS1.23GiB、采样WAL25.15MiB、DB1.40GB，CPU1172.41s，含seed总墙钟774.39s。保存7,179,200根原生柱；重启目标覆盖5600，quick_check=ok，合成旧确认/风险不变。联合负载覆盖原生稳态路径；其后补充的输入修订旧锁和跨来源列表切换分支由定向回归及实际浏览器覆盖。

## 保全、安装、界面与回退

使用SQLite backup API留存一致性快照。最终旧 **48,982 signal_event + 48,982 event_risk全字段逐主键SHA256相等，删除0、修改0、新增旧事件0**。旧业务表行数均相等；只有meta/runtime_meta增加新命名空间。quick_check=ok。

官方管理器inspect→浏览器候选模块加载→install→重启，最终活动版本MA55 0.9.3、Kline3.3.0、Theme1.0.7。MA55包24文件、Kline包8文件与安装目录逐字节相等，后端与当前源码相等，无待生效版本。

实际浏览器验证暗亮主题1366/900/390，五卡/三加二/两列均无水平溢出；手机关闭宿主导航后内容可用。候选直接定位真实确认及支撑，原生图15/30/60m为64/32/16根，缺MA233显示—。ETF价格按三位显示，候选深链正确。旧冻结入口514条当天确认，归档样本图仍为250根、8个历史BUY，来源明确为RAW_5M。切换候选/冻结历史时先清除另一来源列表，避免加载中误标。

隔离环境实际官方管理器0.9.3→0.8.2回退成功，用户数据保留。旧0.8.2代码打开新库一致性副本，旧确认/风险逐行不变，原生行情/候选行数不变，旧图可读，quick_check=ok。生产未切回旧版。

此次常规CTRL_BREAK关闭0.9.2后，Uvicorn已完成shutdown，原进程退出码3仍占写入租约。先另存一致性快照并quick_check，再结束该残留进程，租约释放，0.9.3恢复运行。退出阶段根因未在本轮修改宿主处理。

## 定向验证与边界

- Python主套件（MA55后端/行情及Kline后端）：110 passed / 27 skipped；随后新增同目标300秒冷却、跨重启恢复用例，native专项11 passed。27项为未挂载私人参考包/固定版本输入。
- Node：MA55 14 passed、Kline39 passed；两插件Vite build、nga build_backend、Ruff、git diff --check通过。
- 宿主 `backend/app / frontend/src / dev.ps1` 相对v0.3.36差异0；`engine.py / pipeline.py`相对起点差异0。研究仓库未修改。
- 下一根真实盘中自动到达、实时LIVE告警时效、账号总池、正式停牌身份及长期RSS平台尚未验证。缺因子和源缺返回保留逐股原因；新候选仍属人工复核版本。

私人证据位于忽略的 `data/private/ma55-native15-production-20261113/`：生产前后快照及事实、real-source-evidence-091、production-evidence、final-readback、rollback-evidence、production-comparison、stress-native5600、layouts和浏览器截图。原始行情、数据库、凭据、日志、截图、包及任务文件不提交。
