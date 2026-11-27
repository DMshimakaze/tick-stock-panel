# 大盘量能 · Market VolPulse

TSP v0.4.1 的独立 `market.volpulse` 0.1.0 扩展。左侧导航打开「大盘量能」，可查看沪深两市和四个指数的累计成交额、上一交易日同期、全天预测、边际量比和异动。数据单位为人民币元，页面以亿元显示。

## 行情与算法

- 沪深两市总额为 `000001.SH + 399106.SZ`。上证、深成、创业板和科创综指分别展示，沪深总额不含北交所。
- 交易日与上一交易日取自 TSP 已安装的沪深市场规则包。日历覆盖不足时暂停行情采集。插件独立 SQLite 保留最近 30 个已确认交易日，预测读取最近最多 10 个完整日。
- 腾讯提供批量快照及五日／单日分时。东财按分钟增量恢复同源累计，创业板成交额按来源分别计算。新浪备用快照连续两个已完成交易日核对通过后，才参与对应标的的腾讯同期比较。
- 09:25 竞价终态单独显示。连续时轴为 09:31–11:30 和 13:01–15:00，盘中未闭合点独立标记。收盘预测以最终闭合分钟成交额为准。
- 异动默认要求量比与斜率同时达标。后台在 SQLite 中保存事件和冷却状态。设置里的「盘中页内通知」仅控制当前页面的提示，历史查看只读本地缓存。

公式沿用 [ashare-volpulse v0.1.5](https://github.com/LostMoonkin/ashare-volpulse/tree/v0.1.5)，commit `9e9df02d25a639b867ae39fc40ea05414eb71dc9`。原始 MIT 声明见 `LICENSE-UPSTREAM.txt`。本插件的口径差异、对照结果及部署边界见 [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md)。

## 测试与构建

在插件目录使用宿主的 `nga` 环境及 pnpm 9.10.0。

```powershell
python -B -m unittest discover -s tests -v
node --test tests/*.test.mjs
pnpm install --frozen-lockfile
pnpm build
python -B build_backend.py --destination <交付路径>/market-volpulse-0.1.0.zip
```

构建输出 ES 模块以及独立安装 ZIP。`preview/` 是源码布局演示，实际安装包只收录清单、后台、前端模块和许可证。

## 隔离安装

在隔离 TSP 的「设置 → 导入与备份」选择 ZIP，预检、确认信任并保存。通过该实例的管理会话准备三个固定 Ext Data 表和专用 Token，然后重启单 worker 宿主。

```powershell
python -B -m backend.bridge --data-dir <隔离数据目录> --url http://127.0.0.1:<后端端口>
```

已设置面板密码的实例追加 `--session`，在交互提示中输入已授权的管理会话凭据。后台通过 `read:ext`、`write:ext` 调用 Ext Data HTTP，凭据保存在插件独立运行目录。管理会话可访问 `/api/extensions/market.volpulse/v1/status` 查看最近成功、请求计数、覆盖和下一次计划。停用通过「已安装扩展」操作，重启生效，SQLite 继续保留。

### 正式实例

0.1.0 已在本机正式 TSP v0.4.1 启用，菜单为「大盘量能」。TSP v0.4.1 的 Ext Data 写入会删除宿主共享策略结果缓存并清理内存行情视图。正式安装在收盘后完成，持续盘中发布的宿主负载待观察。

本机有界验证使用三个公开端点。生产使用与行情再分发应分别核对数据方的适用条款和授权，尤其是东财对行情再传播的限制。[东方财富法律声明](https://about.eastmoney.com/home/legal) · [新浪财经用户协议](https://finance.sina.com.cn/roll/2021-05-12/doc-ikmxzfmm2033220.shtml)
