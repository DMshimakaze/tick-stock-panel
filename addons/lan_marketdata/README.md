# 公共滚动行情｜lan.marketdata 1.0.2

三个自有扩展中唯一的QuantDash行情生产者。MA55只消费完成15m，工作台只展示已保存15/30/60m。宿主固定官方v0.3.32。

## 运行与数据

- 官方startup在 `context.data_dir/user_data/lan_marketdata` 建立单writer DuckDB及OS lease。写入、指标关闭窗口、ACK、裁剪和水位同事务提交，浏览器关闭不停止后台。
- 每股5m最多144根；15/30/60/120m、daily、2d各最多256根。指标状态另保留最多233个真实收盘。按实际落盘行数验收，不依赖文件压缩或页面裁剪。
- 请求固定5m、adjust=none，最多100证券/批、5000根/证券；HTTP hooks按实际调用分类计数，滚动预算和429检查点可恢复。当前宿主分钟预留6，共享上限24，较大的宿主预算会缩减共享额度。
- 北京时间、60秒完成余量、午休分段。3/6/12/24根形成分钟周期、48根形成日K，2d按真实日历组合。缺K保留缺口，量从QuantDash手转为股。
- QFQ只用显式证券响应的真实事件链，历史asof只纳入当时生效事件。缺覆盖为FACTOR_PENDING；原始tick来自元信息。历史更正需有界重建，无法证明的指标暂停。
- 最新全池5m优先，先预热15m，再逐页补64交易日（3072根5m）以得到256根60m及MA233。120m、2d按已有历史形成。已预热证券首次查看不重复冷启动。
- 已登记但未预热的股票查看请求进入现有64项/10分钟需求集合，重复请求合并。最新全池5m仍先完成；历史阶段优先所查看证券，并用同窗口其他股票补足最多100股的网络批量。

## DuckDB内存与恢复

私有 `user_data/lan_marketdata/settings.json` 可设置 `"duckdb_memory_limit": "16GB"`。默认 `8GB`，仅接受整数 `1GB`—`32GB`（大小写不敏感）；非法值明确报ERROR。线程固定2。预算是上限，不是预分配；`8GB`按DuckDB十进制单位在连接实际显示为`7.4 GiB`，`16GB`显示为`14.9 GiB`。

`/status`中的`duckdb_memory_limit`、`duckdb_threads`、`duckdb_buffer_bytes`来自连接实际查询，另列配置值及`database_connected`。writer退出后保留最后成功水位及错误，STARTING、RECOVERING_DB、ERROR在读取接口返回明确503；状态接口始终可查。

网络仍为100股/最多5000根5m。计算/事务按单股进行，指标使用完整输入；SQL临时注册每周期最多144/256根保留尾部，注册资源在finally释放，已处理网络数据及时移除。判空只读一条元信息，不解码全池指标历史。未修改insertion-order设置。

单股DuckDB OOM先回滚，该股K/水位/ACK不推进；保留独占lease，关闭重开本包连接，等待5秒、15秒后最多重试2次，复用已收到的网络批次。`db_retry_attempt`为当前尝试，`db_retry_count`为累计恢复次数，`last_error`保留原因。耗尽或初始化失败转ERROR，不删除、重建或重新导入数据库。回退1.0.1会恢复其1GB限制，仅供紧急隔离；数据先停机另存。

## 正式HTTP v1

前缀 `/api/extensions/lan.marketdata/v1`，仅注册GET。

| 路径 | 内容/上限 |
| --- | --- |
| `/status` | writer、预算、请求计数、最近提交、股票池及最新真实交易日 |
| `/universe?offset=0&limit=100` | symbol稳定顺序、generation、元信息、修订、日历；不输出指标状态和事件链 |
| `/bars?symbols=000006.SZ&timeframes=15m,30m,60m&asof=...` | 最多32证券、每周期256根、合计8192根；含来源截至、完成点、source_count、price_basis、因子覆盖、tick、股单位、缺口和预热状态 |
| `/inventory?symbol=000006.SZ` | 实际落盘行数、容量、首尾完成点 |

认证由宿主处理。v0.3.32的read:ext Token对自定义扩展路由返回403；本机无密码模式及合法面板会话可用。两个消费者的私有settings可提供 `api_session`，只发送宿主tf_session cookie。页面继续使用ctx.api。会话失效时显示DATA_SOURCE_UNAVAILABLE并保留结果，不绕过鉴权。

## 安装、迁移、回退

先安装公共包，再安装MA55和工作台，重启后端并刷新网页。构建入口 `build_backend.py`，ZIP留在忽略的dist。

`import_legacy.py` 只用于显式离线导入一致SQLite快照；正常运行不读取其他插件数据库。MA55快照必须声明已验证RAW_5M_TO_QFQ_15M来源；工作台仅接收明确QuantDash/RAW及有效格点的数据。原库只读，原主键幂等导入并在新库实际裁剪。旧1m保留。

```powershell
& $PY -B -X utf8 -m addons.lan_marketdata.import_legacy --destination $NewDuckDB --ma55-backup $MA55Snapshot --kline-backup $KlineSnapshot
& $PY -B -X utf8 -m pytest -q -p no:cacheprovider addons/lan_marketdata/tests_backend
& $PY -B -X utf8 addons/lan_marketdata/build_backend.py
```

私有settings的 `collection_symbols` 用于限定现场验收证券；省略即完整股票池。`warm_symbols` 可限制深预热，默认逐页推进全池。分母独立于图表会话。

回退旧两个生产者前，先在官方管理器停用公共包，再回退MA55与工作台并重启。停机后可另存R1信号库、恢复升级前一致SQLite快照；新旧副本都保留。详见[集成验收](SHARED_ROLLING_ACCEPTANCE_20261108.md)。
