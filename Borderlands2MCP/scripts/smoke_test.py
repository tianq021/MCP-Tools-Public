"""端到端冒烟测试：在存档副本上验证读/改/写/导入导出全链路。

用法：python scripts\smoke_test.py
不会碰真实存档（只读取，全部操作在 .local\tmp 的副本上）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import itemcodes  # noqa: E402
from bl2toolkit.paths import find_steam_id_dir, list_saves  # noqa: E402
from bl2toolkit.saves import SaveFile  # noqa: E402

TMP = ROOT / ".local" / "tmp"
BLACK_GUN = "BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)"  # 超级哈罗德（参考文本）


def main() -> int:
    ok = True

    print("== 1. 代码解码/编码 ==")
    info = itemcodes.decode(BLACK_GUN)
    print("   level:", info["level"], "parts:", len(info["parts"]))
    print("   ", itemcodes.describe(BLACK_GUN))
    re_code = itemcodes.encode(info)
    same = itemcodes.with_key_zero(re_code) == itemcodes.with_key_zero(BLACK_GUN)
    print("   重新编码一致:", same)
    ok &= same

    save_dir = find_steam_id_dir()
    if save_dir is None:
        print("!! 找不到存档目录，跳过存档相关测试")
        return 0 if ok else 1
    saves = list_saves()
    print(f"\n== 2. 存档列表（{save_dir}） ==")
    for s in saves:
        sf = SaveFile(s)
        sm = sf.summary()
        print(f"   {sm['slot']}: {sm['name']} {sm['class']} Lv{sm['level']} "
              f"技能点={sm['skill_points']} 已用={sm['skill_points_used']} "
              f"银行={sm['items']['bank']} 物品={sm['items']['items']} 武器={sm['items']['weapons']}")

    TMP.mkdir(parents=True, exist_ok=True)
    src = saves[0]
    work = TMP / "work.sav"
    shutil.copy2(src, work)
    print(f"\n== 3. 技能点修改（副本 {work.name}） ==")
    sf = SaveFile(work)
    before = sf.summary()
    sf.set_skill_points(120)
    sf.save(backup=False)
    after = SaveFile(work).summary()
    print(f"   修改前技能点={before['skill_points']}，修改后={after['skill_points']}")
    ok &= after["skill_points"] == 120
    ok &= after["level"] == before["level"] and after["name"] == before["name"]

    print("\n== 4. 导出物品代码 ==")
    codes = sf.export_codes()
    for section, lst in codes.items():
        print(f"   {section}: {len(lst)} 条")
    ok &= all(len(v) > 0 for v in codes.values())

    print("\n== 5. 从另一个角色同步武器（同步武器） ==")
    target = TMP / "target.sav"
    shutil.copy2(saves[0], target)
    tf = SaveFile(target)
    before_counts = tf.item_counts()
    # 从零（Save0006）取前 5 条武器同步进萨尔瓦多的银行
    source_sf = SaveFile(saves[3])
    source_codes = source_sf.export_codes()["weapons"][:5]
    expected = sum(1 for c in source_codes if not tf.has_code(c))
    imported = tf.import_codes(source_codes, where="bank", dedupe=True)
    tf.save(backup=False)
    after_counts = SaveFile(target).item_counts()
    print(f"   导入: {imported}（应导入 {expected} 条）；银行 {before_counts['bank']} -> {after_counts['bank']}")
    ok &= imported.get("bank", 0) == expected
    ok &= after_counts["bank"] == before_counts["bank"] + expected

    print("\n== 6. 黑枪代码注入 + 去重 ==")
    gun_work = TMP / "gun.sav"
    shutil.copy2(saves[0], gun_work)
    gf = SaveFile(gun_work)
    n1 = gf.import_codes([BLACK_GUN], where="bank", dedupe=True)
    n2 = gf.import_codes([BLACK_GUN], where="bank", dedupe=True)
    gf.save(backup=False)
    print(f"   第一次导入 {n1}，重复导入 {n2}（应为 0）")
    ok &= n1.get("bank") == 1 and n2.get("bank") == 0

    print("\n== 7. 数据一致性（写回后重新解析） ==")
    final = SaveFile(gun_work)
    print(f"   最终: {final.summary()['name']} 银行={final.item_counts()['bank']}")
    ok &= final.item_counts()["bank"] == SaveFile(src).item_counts()["bank"] + 1

    print("\n结果:", "全部通过 ✅" if ok else "有失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
