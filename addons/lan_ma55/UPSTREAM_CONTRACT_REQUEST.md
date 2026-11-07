# MA55 全市场后台消费所需最小官方契约

本提案针对官方v0.3.32。**以下签名均是建议，当前未实现，不是可调用API。** 本轮不修改任何宿主源码，也不新增行情供应商。

## 现场缺口

1. `backend/app/extensions/contracts.py::RepositoryAccess` 仅有 `get_name_map`。它无法给出保证属于股票资产维表的完整全集，也没有历史或增量分钟读取。
2. `GET /api/kline/minute-range` 是单标的最近1–20日本地读取。数千股逐次调用不构成高效全市场后台契约。
3. `POST /api/kline/minute-batch` 在缺口/空分区时会补拉；`prefer_local`不保证零供应商请求，开放Token网关也未放行这个POST。
4. `GET /api/screener/market-snapshot` 是enriched快照，并不承诺完整上市A股全集。
5. Page Pack的 `ctx.api` 未公开请求自身后端路由的通用方法，不能用第二套fetch或改SDK绕过。

## 请求一：正式资产全集

建议在只读 `ExtensionContext.repository` 提供：

```python
list_symbols(asset_type="stock")
```

应返回官方维表中的稳定symbol、名称、资产类型、维表截至日期和原始报价tick。股票与ETF分别请求；不能用名称map、模糊搜索、快照或自选代替全集。维表为空/尚未就绪应明确返回状态，不能把空结果当作全市场扫描完成。

## 请求二：严格本地、官方复权的批量完成K读取

建议提供等价的窄接口：

```python
get_completed_minute_batch(
    symbols, start, end, *, adjusted=True, local_only=True
)
```

消费方需要的返回合同：

- 只读官方已落盘数据，不补历史、不调用Provider、不回退取源、不改变源设置。`local_only=True`必须是强保证。
- symbols有明确批量上限，历史预热与之后的增量可分批消费；给出源数据截至时间、每股实际覆盖、缺口、完成标志及健康/延迟状态。
- OHLC和amount由官方统一投影为同一复权基准，volume保持官方单位。提供每股每交易日与该基准一致的调整比例/原始tick映射，及能识别历史重投影的基准修订信息；缺失必须明确不可计算。
- 北京时间墙钟、分钟起止标记和午休边界明确，提供用于连续性判断的真实交易日序列或等价前序完成点。
- 可以直接返回官方完成15m，但必须明确1m→5m→15m的来源完整性、OHLCV聚合合同。上游无需实现MA55规则。

消费者只有看到数据就绪且健康，才推进已完成15m水位。暂停或缺口时记录未完成数量；补到的历史只能作为REPLAY/DELAYED，不能发成刚发生的实时提醒。

## 请求三：页面到自身后端的受控桥

建议在Page Pack `ctx.api` 提供仅限当前插件命名空间的GET请求能力，复用宿主会话、认证、错误和取消规则。例如由宿主绑定插件ID后允许读取自身 `/api/extensions/<id>/status|events|symbols|cycles`，拒绝访问其他插件或管理路由。

本轮已经完成只读事件/状态接口，无需为MA55开放写入TSP数据、调用源SDK或任意URL的权限。得到此桥后，页面才可转为纯结果展示，停止浏览器内的BUY生产。当前旧观察页面仍独立工作，新后端没有行情接线，不存在两条真实后台BUY写链。

## 接线验收

只在上述合同正式发布后单独接线：以官方股票维表确定分母；有界预热56根连续15m；随后消费增量并补处理短停机；单写者更新派生SQLite；分页/浏览器关闭不影响扫描；以真实一轮的源请求增量、数据覆盖与延迟验收。当前 `FULL_MARKET_BLOCKED_BY_HOST_CONTRACT` 保持成立。
