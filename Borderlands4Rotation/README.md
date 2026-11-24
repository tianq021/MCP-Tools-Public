# Borderlands 4 莫里斯黑市（Maurice's Black Market）位置速查

一个本地单页应用：**打开就显示本周（当天）黑市刷新位置**，左侧可以翻看历史周次和所有已知地点。

```
双击打开： web\index.html
```

![主页](reports/page_home.png)

## 这个页面有什么

- **主页**显示本周的中文地点、地图路径、跑图路线、每周盛大安可首领和万用任务，以及地点实景、官方线索图和刷新倒计时。
- **左侧栏**：周次按时间倒序排列，标明位置确认状态。地点库按区域列出 24 个已知点位，支持按周次、日期和地点名搜索。
- **无广告**：页面只包含本地数据与本地图片（官方线索图 + 社区实景图），没有任何第三方网页、导航、评论区、推荐位。

## 刷新机制（已交叉确认）

| 项目 | 内容 | 来源 |
|---|---|---|
| 换位置 | 北京时间通常在周五凌晨。页面按太平洋时间周四 09:00 估算，夏令时期间约为 00:00，标准时期间约为 01:00 | Gearbox 周更公告与 Steam 发布时间 |
| 解锁 | 通关主线 + 完成 UVHM「モーリスの恵み / Maurice's Bounty」 | 官方 + Wiki |
| 可见性 | 所有玩家位置相同；**不显示地图标记，靠近才显形** | 官方 |
| 冷却 | 打开一次后 30–35 分钟；冷却结束商品换一批 | Wiki / FPS Index |
| 库存 | 随机 2 种传奇 × 4 件 = 8 件；不含职业模组与强化部件 | Wiki |

周次以北京时间的周五至下周四编号。2025-09-12 是第 1 周起点，2026-11-13 至 11-19 是**第 62 周**。周次日期窗口用于整理数据，刷新时间以游戏服务器为准。

## 目录结构

```
web/
  index.html          生成的单页应用（唯一需要双击打开的文件）
  assets/hints/       每周官方线索图（缩略 480px / 大图 1400px）
  assets/locations/   已知地点的社区实景图
data/
  clean/              干净的结构化数据（页面数据源）
    locations.json    24 个已确认地点：区域链、中文路线、实景图、出现过的周次
    weeks.json        各周日期窗、官方活动、线索图、位置（若已确认）
    meta.json         刷新机制、来源清单、无广告说明
  manual_weeks.json   人工确认的周次位置（覆盖自动结果，推荐改这里）
  weeks_observed.json 历史观测（只增不改）
  locations.json      旧版地点库（作为证据保留）
  raw/                原始网页 / 官方公告 JSON / 图片存档（证据，只增不删）
  extracted/          旧版整页正文抽取（含站点导航与广告，已不再是数据源）
src/
  build_data.py       原始存档 → data/clean/*.json（只抽结构化事实，丢弃广告/导航/评论）
  build_site.py       data/clean → web/index.html
  make_assets.ps1     原始图片 → web/assets 缩略图
  predict.py          旧版预测脚本（保留）
reports/              验证截图与报告
```

## 为什么以前的数据里有广告？

`data/extracted/*.txt` 是早期用“整页转文字”的方式从 FPS Index 博客和 Gamerch Wiki 抓的：里面混进了站点导航（HOME / ABOUT US / PRIVACY POLICY）、Gamerch 的 PR 广告位（“マブラヴ ガールズガーデン”等游戏广告）、评论区、推荐链接。

现在改成了**结构化抽取**：`build_data.py` 只取地点名、区域链（例：`フェードフィールド＞骨身海岸＞プランジフォード`）、官方每周活动的 BOSS / 百搭任务 / 线索图 URL，其余内容全部丢弃。原始 HTML 仍留在 `data/raw/` 作为证据，但**不会进入页面**。

## 每周更新

1. 每周五刷新后，找到社区确认的位置（B 站实拍、Ruliweb、Game8 等）。
2. 编辑 `data/manual_weeks.json`：

```json
{
  "weeks": {
    "63": {
      "location_key": "plungeford",
      "status": "reported",
      "note": "来源：……"
    }
  }
}
```

3. 如需更新官方活动和线索图，先合并新的 Steam 公告到 `data/raw/steam_news.json`，按 `data/raw/steam_hints/index.json` 的格式登记图片，并将原图保存为 `data/raw/steam_hints/w<两位周次>_<图片文件名>`。运行 `./src/make_assets.ps1` 生成本地图片。
4. 在项目根目录重新生成页面。周次由日期和已录入的数据决定，添加新周时无需修改脚本。

```powershell
python src\build_data.py
python src\build_site.py
```

`location_key` 可用值见 `data/clean/locations.json`（如 `plungeford`、`elpis_orbital_shatterfield`、`darkloom_biosphere`）。

## 数据来源

| 编号 | 来源 | 用途 |
|---|---|---|
| S1 | Steam 官方每周活动公告 | 每周线索图、大 BOSS、百搭任务（无广告） |
| S2 | Gamerch 日文攻略 Wiki | 过去地点区域链 + 社区实景图（仅取地点事实） |
| S3 | FPS Index | 2025 年第 1–9 周英文路线说明（仅取正文） |
| S4 | B 站周更视频（是咕咕不是姑姑咕） | 第 1–61 周周次与日期窗校验 |
| S5 | Eurogamer | 2026-02 当周位置与刷新时间参考 |
| S6 | bfcom.eu（德文） | 2025-11-14 当周 Fades District，印证第 10 周 |
| S7 | [MentalMars 黑市跑图实录](https://mentalmars.com/borderlands-4/loot/black-market/) | 第 62 周位置与升降梯前隐藏房间的路线 |
| S8–S9 | [中文地图](https://www.gamersky.com/handbook/202509/2015766.shtml)及[首领与任务攻略](https://www.gamersky.com/handbook/202509/2013599.shtml) | 对照简体中文专名 |

## 已知局限

- 第 62 周位置为厄尔皮斯轨道破碎区，B 站实拍与 MentalMars 路线一致。
- 第 10 周位置为推测（Gamerch 记录顺序 + 德文媒体同区域描述）。
- 第 11–61 周大多尚待确认具体位置。页面会显示已经收录的官方线索图和活动内容。
- 部分地点、首领、任务与词缀采用参考译名。地点仍可用英日文原名搜索，活动原文可在名称上悬停查看。译名集中维护在 `data/zh_cn.json`。
- 社区称约 53 个地点循环，未获官方证实；目前整理出 24 个已确认点位。
