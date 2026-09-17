---
name: tft-update
description: 更新 TFT 赛季数据包 (版本更新后手动运行)。拉取 CDragon 最新数据, 与当前数据包 diff, 汇报棋子/装备/海克斯变更, 重建常驻知识包和搜索索引。
---

# TFT 数据更新流程

按顺序执行, 每步出错要停下来报告而不是继续:

1. **拉取最新数据**
   ```bash
   python scripts/fetch_data.py
   ```

2. **重建数据包** (会自动打印与旧包的变更报告)
   ```bash
   python scripts/build_pack.py
   ```
   把变更报告(棋子费用/羁绊变化、新增/移除)原样转述给用户。这正是用户关心的
   "莫甘娜5费改4费"类信息。如果显示"无棋子变更", 也要告知。

3. **重建搜索索引**
   ```bash
   python scripts/build_search_index.py
   ```

4. **投放池核查** (必做)
   ```bash
   python scripts/check_pool.py
   ```
   CDragon 静态数据包含已注册但实际不投放的神器/光明装 (Riot 按版本轮换掉落池,
   且国服和外服池子可能不同步, 如死亡之蔑外服有国服无)。此脚本用两个来源交叉验证:
   tftable.cc (国服, 神器以它为准) + tactics.tools (外服统计, 覆盖光明装)。
   如果报告"疑似不在池"或"数量偏差":
   - 向用户转述可疑名单, 结合用户实战体感确认
   - 确认后编辑 `data/pool_overrides.json` 的 exclude 名单, 重跑步骤 2、3
   - 如果误报源于装备改名 (CDragon 旧 apiName vs tt 新名), 在 check_pool.py 的
     SYNONYMS 里补对照, 不要加进 overrides
   另外注意 build_pack.py 输出中 "pool_overrides 的 XX 不在数据包中" 告警:
   说明被排除的装备官方已改名/删除, 也要核查覆盖表。

5. **审计抽查** (可选, 数据大改版时): `python scripts/build_pack.py --audit`
   检查分类是否有杂质 (未翻译占位名、旧赛季条目混入等)。
   重点看海克斯那行的 **"仍有未回填占位符 N"**: 海克斯描述里 `@TotalValue@` 之类
   占位符要回填成真实数值, 否则同族的 扩展包/扩展包+/扩展包++ 描述会一模一样, Agent
   无法区分。build_pack 已自动处理: CDragon 里同名海克斯常有 DA_* 与 TFT_Augment_*
   两份, DA_* 数值常被哈希成 `{4b65cc7c}` 无法回填 —— 脚本会在该份残留占位符时改选
   同名里占位符最少的变体(优先规范 TFT_Augment_)。若首选本身已能回填(带实时数值)则
   不换, 避免误取往季旧值。N 一般 60~70(那些是 `@StartingGold@` 等 CDragon 静态数据
   根本没给的运行时/全局量, 无法回填, 属正常); 若 N 突然暴涨到几百, 说明这套变体择优
   逻辑对新版本失效了, 要去 `scripts/build_pack.py` 的 `extract_augments` 排查。

6. **Meta 阵容层** (必做)
   ```bash
   python scripts/fetch_meta.py
   ```
   从 tftable.cc 抓当前版本阵容梯度/羁绊强度/神器纹章梯度, 重新生成
   knowledge/meta_comps.md。注意输出末尾的"未映射 id"警告: 出现新装备改名时
   在脚本的 NEW2OLD 表补对照。之后重跑步骤 2 (纹章配方会回填进数据包)。
   若脚本报"只成功解析 N 个阵容, 放弃写入", 说明 tftable 改版了, 旧 Meta 保留,
   告知用户 Meta 层暂时过期。

7. 提醒用户: 后端进程需要重启才会加载新数据包。

## 换赛季迁移

新赛季时: 修改 `config.yaml` 的 `set` 字段和 `scripts/build_pack.py`、
`scripts/build_search_index.py` 顶部的 `SET_NUMBER`, 然后走上面的流程。
若 CDragon 的 setData 里找不到新 `TFTSetXX` mutator, 先用一段 python 打印
`d['setData']` 里所有 mutator 名确认。`knowledge/general_strategy.md` 是
跨赛季通用层, 不需要动 (除非机制大改)。
