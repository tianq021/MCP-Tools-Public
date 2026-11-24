#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_data.py — 从原始网页/存档中提取“干净”的结构化数据，生成 data/clean/*.json

为什么需要这一步：
  原始来源（FPS Index 博客、Gamerch 日文 Wiki、Ruliweb 论坛）都是带广告/导航/评论区/推荐位的整页 HTML。
  直接抓正文会把广告一起存进数据里（之前 extracted/*.txt 就是整页文字，含 Gamerch 的 PR 广告位和评论区）。
  本脚本只提取结构化事实（地点名、区域链、每周官方活动、线索图 URL），丢弃导航、广告、评论、推荐链接。

输入：
  data/raw/fpsindex_all_weeks.html      （周 1-9 位置，人工已核对，实际用 data/weeks_observed.json）
  data/raw/gamerch_wiki.html            （过去地点列表：日文名、区域链、实景图）
  data/raw/steam_news.json              （Steam 官方每周活动公告，含官方线索图，无广告）
  data/weeks_observed.json              （已核对的历史周次）
输出：
  data/clean/locations.json
  data/clean/weeks.json
  data/clean/meta.json
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CLEAN = ROOT / "data" / "clean"
CLEAN.mkdir(parents=True, exist_ok=True)

BEIJING = timezone(timedelta(hours=8))
WEEK1_START = date(2025, 9, 12)          # 第 1 周的北京时间日期窗口
WEEK_START_UTC = datetime(2025, 9, 11, 16, 0, tzinfo=timezone.utc)  # = 北京时间 2025-09-12 00:00

# Gamerch「過去のブラックマーケットの場所」的记录顺序 → 地点 key
GAMERCH_ORDER_KEYS = [
    "plungeford", "punitive_manufactory", "crumblehole", "rattlehead_crest",
    "carcadia_district", "ruined_sumplands", "elpis_orbital_shatterfield",
    "heart_of_the_mountain", "maestro_wreck", "darkloom_biosphere",
    "stopflow_bower", "windspear", "punitive_manufactory", "ascension_plaza",
    "boneview_coast", "embracers_cliff", "elpis_orbital_shatterfield",
    "stoneblood_forest", "hals_handbasket", "flarefeel", "terminus_district",
    "elpis_orbital_shatterfield", "lawless", "tonidge_peel", "fade_district",
    "boglight_vigil", "killing_floor",
]

EN_NAME = {
    "plungeford": "Plungeford",
    "punitive_manufactory": "Punitive Manufactory",
    "crumblehole": "Crumblehole",
    "rattlehead_crest": "Rattlehead Crest",
    "carcadia_district": "Carcadia District",
    "ruined_sumplands": "Ruined Sumplands",
    "elpis_orbital_shatterfield": "Elpis Orbital Shatterfield",
    "heart_of_the_mountain": "Heart of the Mountain",
    "maestro_wreck": "Wreck of the Maestro",
    "darkloom_biosphere": "Darkloom Biosphere",
    "stopflow_bower": "Stopflow Bower",
    "windspear": "Windspear",
    "ascension_plaza": "Ascension Plaza",
    "boneview_coast": "Boneview Coast",
    "embracers_cliff": "Embracer's Cliff",
    "stoneblood_forest": "Stoneblood Forest",
    "hals_handbasket": "Harl's Handbasket",
    "flarefeel": "Flarefeel",
    "terminus_district": "Terminus District",
    "lawless": "Lawless",
    "tonidge_peel": "Tonidge Peel",
    "fade_district": "Fade District",
    "boglight_vigil": "Boglight Vigil",
    "killing_floor": "Killing Floor",
}

REGION_EN = {
    "フェードフィールド": "Fadefields",
    "フェード・フィールド": "Fadefields",
    "ターミナス・レンジ": "Terminus Range",
    "カルカディア・バーン": "Carcadia Burns",
    "ドミニオン": "Dominion",
    "エルピス": "Elpis",
}

# 中文攻略说明（人工整理，取自 FPS Index 英文说明 / Gamerch 日文说明的要点，不含广告与评论区内容）
HOWTO_CN = {
    "1": "到海岸骨境东部，滑翔到最高建筑的顶层平台。靠近后贩卖机才会显形。",
    "2": "从遗产杰作安全屋（Heritage Opus）前往尖牙攀岩，进入瀑布后的洞穴。沿路清怪并向上攀爬，到惩罚工厂顶层找贩卖机。",
    "3": "前往卡卡地亚焚毁区南部的崩塌洞。先占领乡野投掷教团发射井（Rustical Hurl Order Silo），再借滑索过去。",
    "4": "从朝阳安全屋（Someday Rise）前往崇拜者的绞索西侧，进入响颅峰的建筑并上到顶层。",
    "5": "从藏身处安全屋（The Lockaway）前往卡卡地亚区的哀鸣核心南侧，用钩爪爬到建筑顶部。",
    "6": "前往废墟洼地南部，用 ECHO 定位器跟随路线标记。",
    "7": "从堕落王冠的传送点进入厄尔皮斯轨道破碎区，沿主路走到地图末段。在通往首领战的达尔升降梯前停下，滑进旁边的隐藏房间就能找到黑市。",
    "8": "到大山之心，从圣所遗骨（Bones of Sanctuary）北侧的安全屋出发。钻过窄缝、攀崖，再跳到对面继续向上，贩卖机在顶端。",
    "9": "先完成支线「野蛮打捞」，再进入嚎叫森林的大师号残骸。",
    "10": "贩卖机在圣城隐没区的暗土生物球内部。可通过支线「坏种第二章」解锁入口。",
    "11": "在巨物遗址与英勇砥石之间寻找止流亭。",
    "12": "前往风之矛，在圣职者驻地（Vestal Garrison）与索普检查站（Checkpoint Thorp）之间寻找贩卖机。利克托黑站点是附近的另一个点位。",
    "13": "同第 2 位：牙々洞の道の洞穴内、ピュニティブ・マニファクトリウム顶层（这一周是同一地点的再次出现）。",
    "14": "前往圣城的飞升广场。",
    "15": "在隐没旷野的桑迪托别墅一带寻找观骨海岸。",
    "16": "前往卡卡地亚焚毁区的侧倾斜面，寻找拥抱者断崖。",
    "17": "同第 7 位：地图左上エルピス軌道シャターフィールド（再次出现，路线同上）。",
    "18": "前往终点山脉的石血森林，在封圣之杯附近寻找贩卖机。",
    "19": "前往废墟洼地北部的哈尔的手篮。",
    "20": "前往隐没旷野的饥荒平原，在弗莱尔费尔寻找贩卖机。",
    "21": "从美食广场安全屋（The Food Court）前往圣城终点区，在凯洛斯第一新闻网附近寻找贩卖机。",
    "22": "同第 7 位：エルピス軌道シャターフィールド（再次出现）。",
    "23": "前往终点山脉的无法之地，在弃置地一带寻找贩卖机。",
    "24": "前往卡卡地亚焚毁区的巨物遗址，按实景图寻找贩卖机。",
    "25": "前往圣城的隐没区，按实景图寻找贩卖机。",
    "26": "前往隐没旷野的侵蚀高原，在沼光岗哨寻找贩卖机。",
    "27": "前往隐没旷野的侵蚀高原，在屠宰场寻找贩卖机。",
}


def strip_tags(s: str) -> str:
    s = re.sub(r"\[/?(?:p|b|i|u|h1|h2|h3|img|url|dynamiclink|list|table|tr|td|\*)[^\]]*\]", " ", s)
    s = re.sub(r"\[img[^\]]*\]", " ", s)
    s = re.sub(r"\{STEAM_CLAN_IMAGE\}[^\s\]]+", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("[", " ").replace("]", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_gamerch_locations() -> list[dict]:
    html = (RAW / "gamerch_wiki.html").read_text(encoding="utf-8", errors="replace")
    m_sec = re.search(r"過去のブラックマーケットの場所</h2>", html)
    seg = html[m_sec.end():] if m_sec else html[html.rindex("過去のブラックマーケットの場所"):]
    parts = re.split(r"<h3 ", seg)
    out = []
    for chunk in parts[1:]:
        m = re.match(r"[^>]*>([^<]+)</h3>", chunk)
        if not m:
            continue
        jp = m.group(1).strip()
        if "モーリスの自動販売機とは" in jp:
            continue
        # 区域链：出現在「出現場所」后的 X＞Y＞Z
        chain_m = re.search(r"出現場所\s*([^\s<]+(?:＞[^\s<]+)+)", re.sub(r"<[^>]+>", " ", chunk))
        chain = chain_m.group(1).split("＞") if chain_m else []
        # 图片：该小节最后一张 data-original 图（周囲の様子）
        urls = re.findall(r'data-original="(https://cdn\.gamerch\.com/[^"]+/entry/[^"]+)"', chunk)
        img_url = urls[-1] if urls else None
        out.append({"jp": jp, "chain": chain, "img_url": img_url})
    return out


def parse_weeks_observed() -> dict[int, str]:
    data = json.loads((ROOT / "data" / "weeks_observed.json").read_text(encoding="utf-8"))
    return {w["week_number"]: w["location_key"] for w in data.get("weeks", []) if w.get("location_key")}


def parse_steam_weeks() -> dict[int, dict]:
    raw = (RAW / "steam_news.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    weekly = [n for n in data["appnews"]["newsitems"] if "Weekly Activities" in n.get("title", "")]
    weekly.sort(key=lambda n: n["date"])
    out: dict[int, dict] = {}
    for n in weekly:
        week = (n["date"] - int(WEEK_START_UTC.timestamp())) // 604800 + 1
        c = n.get("contents", "")
        boss_m = re.search(r"Weekly Big Encore Boss(?:es)?[^\[]*\[/h2\](.*?)(?=\[h2\]|$)", c, re.S)
        wc_m = re.search(r"Weekly Wildcard Mission:\[/h2\](.*?)\[/p\]", c, re.S)
        imgs = re.findall(r"\{STEAM_CLAN_IMAGE\}/45157740/([0-9a-f]+\.(?:jpg|png|jpeg))", c)
        bosses = []
        if boss_m:
            for block in re.split(r"\[/(?:p|h3)\]", boss_m.group(1)):
                name = strip_tags(block)
                if name:
                    bosses.append(name)
        out[week] = {
            "title": n.get("title", ""),
            "published": datetime.fromtimestamp(n["date"], tz=timezone.utc).isoformat(),
            "boss": " / ".join(bosses),
            "wildcard": strip_tags(wc_m.group(1)) if wc_m else "",
            "hint_img": imgs[-1] if imgs else None,
        }
    return out


def week_dates(week: int) -> tuple[str, str]:
    start = WEEK1_START + timedelta(days=(week - 1) * 7)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def main() -> int:
    gamerch = parse_gamerch_locations()
    observed = parse_weeks_observed()
    steam = parse_steam_weeks()
    zh = json.loads((ROOT / "data" / "zh_cn.json").read_text(encoding="utf-8"))
    boss_names = {name.casefold(): cn for name, cn in zh["bosses"].items()}
    manual_path = ROOT / "data" / "manual_weeks.json"
    manual = json.loads(manual_path.read_text(encoding="utf-8-sig")).get("weeks", {}) if manual_path.exists() else {}
    manual_weeks = {int(week): entry for week, entry in manual.items()}
    today = datetime.now(BEIJING)
    current_week = (today.date() - WEEK1_START).days // 7 + 1
    last_week = max([current_week, *steam, *observed, *manual_weeks])

    # ---- locations ----
    locations = []
    merged: dict[str, dict] = {}
    for order, g in enumerate(gamerch, start=1):
        if order - 1 >= len(GAMERCH_ORDER_KEYS):
            break
        key = GAMERCH_ORDER_KEYS[order - 1]
        if key in merged:
            merged[key]["orders"].append(order)
            continue
        region_jp = g["chain"][0] if g["chain"] else ""
        chain_cn = zh["locations"][key]
        name_cn = chain_cn[-2] if key in {"carcadia_district", "windspear", "stoneblood_forest", "terminus_district", "lawless"} else chain_cn[-1]
        loc = {
            "key": key,
            "order": order,
            "orders": [order],
            "jp": g["jp"],
            "en": EN_NAME.get(key, g["jp"]),
            "cn": name_cn,
            "chain_cn": chain_cn,
            "chain_jp": g["chain"],
            "region_jp": region_jp,
            "region_en": REGION_EN.get(region_jp, region_jp),
            "target_jp": g["chain"][-1] if g["chain"] else g["jp"],
            "howto_cn": HOWTO_CN.get(str(order), ""),
            "img": f"assets/locations/{order}.jpg",
            "week_seen": None,
            "confidence": "community",
            "sources": ["Gamerch 日文攻略 Wiki（S2）"],
        }
        merged[key] = loc
        locations.append(loc)

    key_to_loc = {l["key"]: l for l in locations}
    for week, key in observed.items():
        if key in key_to_loc:
            key_to_loc[key].setdefault("weeks", []).append(week)
            if key_to_loc[key]["week_seen"] is None:
                key_to_loc[key]["week_seen"] = week

    # ---- weeks ----
    weeks = []
    for week in range(1, last_week + 1):
        start, end = week_dates(week)
        s = steam.get(week, {})
        boss = s.get("boss", "")
        wildcard = s.get("wildcard", "")
        mission, _, traits = wildcard.removeprefix("Mission: ").partition(" (")
        mission_cn = zh["missions"].get(mission, mission)
        modifiers_cn = [zh["modifiers"].get(trait, trait) for trait in traits.removesuffix(")").split(", ")] if traits else []
        item = {
            "week": week,
            "start": start,
            "end": end,
            "official": bool(s),
            "boss": boss,
            "boss_cn": "、".join(boss_names.get(name.casefold(), name) for name in boss.split(" / ")),
            "wildcard": wildcard,
            "wildcard_cn": f"{mission_cn}（{'、'.join(modifiers_cn)}）" if modifiers_cn else mission_cn,
            "hint_img": s.get("hint_img"),
            "hint_thumb": f"assets/hints/w{week:02d}.jpg" if s.get("hint_img") else None,
            "hint_full": f"assets/hints/full/w{week:02d}.jpg" if s.get("hint_img") else None,
            "location_key": None,
            "location_status": "unknown",
            "location_note": "",
        }
        if week in observed:
            item["location_key"] = observed[week]
            item["location_status"] = "verified"
            item["location_note"] = "两来源交叉确认（FPS Index + Gamerch）"
        elif week == 10:
            item["location_key"] = "darkloom_biosphere"
            item["location_status"] = "tentative"
            item["location_note"] = "推测位置为暗土生物球。Gamerch 将它列为第 10 个地点，德文攻略 bfcom.eu 当周只确认到同一区域的隐没区。"
        elif week == 22:
            item["location_key"] = "terminus_district"
            item["location_status"] = "reported"
            item["location_note"] = "Eurogamer 于 2026 年 2 月 10 日记录，贩卖机位于终点区的凯洛斯第一新闻网附近。"
        weeks.append(item)

    # 应用人工核对记录。
    for w in weeks:
        ov = manual_weeks.get(w["week"])
        if ov:
            w["location_key"] = ov["location_key"]
            w["location_status"] = ov["status"]
            w["location_note"] = ov["note"]
            loc = key_to_loc.get(ov["location_key"])
            if loc is not None:
                ws = loc.setdefault("weeks", [])
                if w["week"] not in ws:
                    ws.append(w["week"])
                ws.sort()
                if loc["week_seen"] is None:
                    loc["week_seen"] = w["week"]

    # ---- meta ----
    meta = {
        "generated_at": today.isoformat(timespec="seconds"),
        "week1_start": WEEK1_START.isoformat(),
        "current_week": current_week,
        "reset": {
            "rule_cn": "北京时间通常在周五凌晨刷新，倒计时按太平洋时间周四 09:00 估算。",
            "weekday_cn": "周五",
            "source_note": "估算时间为美国夏令时期间 00:00、标准时期间 01:00。实际生效时间以游戏服务器为准。",
        },
        "facts": [
            {"k": "解锁条件", "v": "通关主线并完成终极秘藏猎人模式的「莫里斯的赏金任务」。"},
            {"k": "位置可见性", "v": "所有玩家位置相同；机器不显示地图标记，靠近后才会显形；物品阵容每个玩家不同。"},
            {"k": "冷却", "v": "打开一次（买不买都算）后 30–35 分钟冷却；冷却结束商品会换一批。"},
            {"k": "库存", "v": "随机 2 种传奇装备 × 各 4 件 = 共 8 件；不含职业模组与强化部件；价格 45 万–100 万美元级。"},
            {"k": "地点池", "v": f"目前从社区记录中整理出 {len(locations)} 个点位。"},
        ],
        "sources": [
            {"id": "S1", "name": "Steam 官方每周活动公告", "url": "https://steamcommunity.com/games/1285190/announcements", "trust": "官方", "note": "每周线索图与活动内容，无广告。"},
            {"id": "S2", "name": "Gamerch 日文攻略 Wiki", "url": "https://gamerch.com/borderlands4/941373", "trust": "社区（较可靠）", "note": "过去地点区域链与实景图；页面含站点广告，已只提取地点事实。"},
            {"id": "S3", "name": "FPS Index", "url": "https://fpsindex.com/all-maurices-black-market-locations-in-borderlands-4-bl4-all-weeks/", "trust": "社区（较早停更）", "note": "2025 年第 1–9 周的英文路线说明；页面含导航/评论，已只提取正文。"},
            {"id": "S4", "name": "B 站周更视频（是咕咕不是姑姑咕）", "url": "https://space.bilibili.com/43953352", "trust": "社区", "note": "第 1–61 周的周次与日期窗口，用于校验周次起算。"},
            {"id": "S5", "name": "Eurogamer", "url": "https://www.eurogamer.net/borderlands-4-maurice-black-market-vending-machine-location", "trust": "媒体", "note": "2026-02 当周位置与刷新时间参考。"},
            {"id": "S6", "name": "bfcom.eu（德文）", "url": "https://bfcom.eu/s/borderlands-4/259089-borderlands-4-maurice-schwarzmarkt-standort-14--21-november-2025--fades-district", "trust": "媒体", "note": "2025-11-14 当周写作 Fades District，用于印证第 10 周。"},
            {"id": "S7", "name": "MentalMars 黑市跑图实录", "url": "https://mentalmars.com/borderlands-4/loot/black-market/", "trust": "社区", "note": "第 62 周位置及进入隐藏房间的路线；页面随周次更新。"},
            {"id": "S8", "name": "游民星空中文地图与收集攻略", "url": "https://www.gamersky.com/handbook/202509/2015766.shtml", "trust": "社区", "note": "用于核对大区及部分兴趣点的简体中文名称。"},
            {"id": "S9", "name": "游民星空中文首领与任务攻略", "url": "https://www.gamersky.com/handbook/202509/2013599.shtml", "trust": "社区", "note": "用于核对部分每周活动的首领与任务名称。"},
        ],
        "ads_note": "旧版数据里出现广告，是因为早期把整页网页正文（含站点导航、PR 广告位、评论区、推荐链接）直接存成了 extracted/*.txt。现在改为只抽取结构化字段（地点名/区域链/官方每周活动/线索图），原始 HTML 仅作为证据存档，不再进入展示层。",
    }

    (CLEAN / "locations.json").write_text(json.dumps({"locations": locations}, ensure_ascii=False, indent=2), encoding="utf-8")
    (CLEAN / "weeks.json").write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8")
    (CLEAN / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"locations={len(locations)} weeks={len(weeks)} steam_weeks={len(steam)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
