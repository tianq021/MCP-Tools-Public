"""已加载牧场设施的单项只读检查。"""
import argparse
import sys
from collections import Counter

from tidy_vacpack import backpack, compatible_food, corral_diets, food_group_of, report, rpc, storage_slots


# Keep the response small: return the location and evidence for each match.
CHECKS = {
    "find_full_refinery_resources": (
        "sr_refinery", "resources", lambda r: r["count"] > 0 and r["free_space"] == 0,
        ("item_id", "count", "free_space")),
    "find_key_reward_gordos": (
        "sr_gordos", "gordos", lambda r: r["drops_key"] and not r["popped"],
        ("gordo_id", "loaded", "zone", "position", "eaten", "target_count")),
    "find_unplanted_gardens": (
        "sr_plots", "plots", lambda r: r["type"] == "GARDEN" and r["crop"] == "NONE",
        ("plot_id", "loaded", "zone", "upgrades")),
    "find_gardens_without_sprinklers": (
        "sr_plots", "plots", lambda r: r["type"] == "GARDEN" and "SPRINKLER" not in r["upgrades"],
        ("plot_id", "loaded", "zone", "crop")),
    "find_gardens_without_soil": (
        "sr_plots", "plots", lambda r: r["type"] == "GARDEN" and "SOIL" not in r["upgrades"],
        ("plot_id", "loaded", "zone", "crop")),
    "find_rising_plorts": (
        "sr_market", "prices", lambda r: (r["change"] or 0) > 0,
        ("item_id", "name", "unit_price", "change")),
    "find_falling_plorts": (
        "sr_market", "prices", lambda r: (r["change"] or 0) < 0,
        ("item_id", "name", "unit_price", "change")),
    "find_sell_now_plorts": (
        "sr_market_advice", "advice", lambda r: r["advice"] == "SELL_NOW",
        ("item_id", "name", "unit_price", "price_ratio", "saturation_ratio", "change")),
    "find_wait_plorts": (
        "sr_market_advice", "advice", lambda r: r["advice"] == "WAIT",
        ("item_id", "name", "unit_price", "price_ratio", "saturation_ratio", "change")),
    "find_craftable_gadgets": (
        "sr_gadgets", "gadgets", lambda r: r["can_fabricate"],
        ("gadget_id", "name", "inventory", "costs")),
    "find_stocked_gadgets": (
        "sr_gadgets", "gadgets", lambda r: r["inventory"] > 0,
        ("gadget_id", "name", "inventory")),
    "find_unowned_unlocked_blueprints": (
        "sr_gadgets", "gadgets", lambda r: r["blueprint_unlocked"] and not r["blueprint_owned"],
        ("gadget_id", "name", "blueprint_price")),
    "find_empty_gadget_sites": (
        "sr_gadget_sites", "sites", lambda r: r["active"] and r["gadget_id"] is None,
        ("site_id", "zone", "position", "distance")),
    "find_empty_feeders": (
        "sr_feeders", "feeders", lambda r: r["food_count"] == 0,
        ("plot_id", "zone", "feeder_id", "food_count")),
    "find_low_feeders": (
        "sr_feeders", "feeders", lambda r: 0 < r["food_count"] < r["items_per_feeding"],
        ("plot_id", "zone", "food_id", "food_count", "items_per_feeding")),
    "find_stocked_collectors": (
        "sr_collectors", "plort_storage",
        lambda r: any(s["accessible"] and s["count"] > 0 for s in r["slots"]),
        ("plot_id", "zone", "storage_id", "slots")),
    "find_hungry_corrals": (
        "sr_corrals", "corrals", lambda r: r["loaded"] and r["hungry_slime_count"] > 0,
        ("plot_id", "zone", "hungry_slime_count")),
    "find_feral_corrals": (
        "sr_corrals", "corrals", lambda r: r["loaded"] and r["feral_count"] > 0,
        ("plot_id", "zone", "feral_count")),
    "find_corrals_without_feeders": (
        "sr_corrals", "corrals", lambda r: r["loaded"] and not r["feeders"],
        ("plot_id", "zone", "slime_count")),
    "find_corrals_without_collectors": (
        "sr_corrals", "corrals", lambda r: r["loaded"] and not r["collectors"],
        ("plot_id", "zone", "slime_count")),
    "find_ready_extractors": (
        "sr_extractors", "extractors", lambda r: r["loaded"] and r["ready"],
        ("site_id", "zone", "position", "gadget_id", "queued_output")),
    "find_depleted_drones": (
        "sr_drones", "drones", lambda r: r["loaded"] and r["has_charge"] is False,
        ("site_id", "zone", "position", "has_charge")),
}


def plort_space(item):
    if not item.endswith("_PLORT"):
        raise ValueError("请输入结晶编号")
    slots = [dict(storage_id=dest[0], slot=dest[1], count=s["count"], free=s["free_space_for_item"])
             for dest, s in storage_slots(item)
             if s["free_space_for_item"] > 0 and (not s["count"] or s["item_id"] == item)]
    slots.sort(key=lambda s: s["count"], reverse=True)
    return dict(item_id=item, free_total=sum(s["free"] for s in slots), slots=slots)


def validate_food(item):
    if food_group_of(item) == "OTHER":
        raise ValueError("请输入食物编号")
    catalog = rpc("sr_item_catalog", {"category": "food", "query": item, "limit": 200})
    if not any(entry["item_id"] == item for entry in catalog["items"]):
        raise ValueError("找不到指定食物编号")


def food_corrals(item):
    validate_food(item)
    rows = []
    for plot, feeder, diets in corral_diets():
        if compatible_food(item, diets):
            favorites = [d["item_id"] for d in diets if item in d["favorites"]]
            rows.append(dict(plot_id=plot, feeder_storage_id=feeder, favorite_for=favorites))
    rows.sort(key=lambda r: not r["favorite_for"])
    return dict(item_id=item, radius=8, corrals=rows)


def crop_gardens(item):
    validate_food(item)
    plots = rpc("sr_plots")["plots"]
    return dict(item_id=item, gardens=[p for p in plots if p["type"] == "GARDEN" and p["crop"] == item])


def feeder_space(item):
    validate_food(item)
    suitable = {storage: (plot, [d["item_id"] for d in diets if item in d["favorites"]])
                for plot, storage, diets in corral_diets()
                if storage is not None and compatible_food(item, diets)}
    rows = []
    for (storage, index), slot in storage_slots(item, "FOOD"):
        if storage not in suitable or index != 0 or slot["free_space_for_item"] <= 0:
            continue
        if slot["count"] and slot["item_id"] != item:
            continue
        plot, favorites = suitable[storage]
        rows.append(dict(plot_id=plot, storage_id=storage, slot=index, count=slot["count"],
                         free=slot["free_space_for_item"], favorite_for=favorites))
    rows.sort(key=lambda row: not row["favorite_for"])
    return dict(item_id=item, radius=8, free_total=sum(row["free"] for row in rows), feeders=rows)


def paged_rows(tool, collection, **filters):
    offset = 0
    while offset is not None:
        page = rpc(tool, dict(filters, offset=offset, limit=200))
        yield from page[collection]
        offset = page["next_offset"]


def recipe_shortfall(gadget):
    for row in paged_rows("sr_gadgets", "gadgets", query=gadget, owned_only=False):
        if row["gadget_id"] != gadget:
            continue
        costs = row["costs"]
        missing = None if costs is None else [
            dict(**c, missing=c["required"] - c["in_refinery"])
            for c in costs if c["required"] > c["in_refinery"]]
        return dict(gadget_id=gadget, blueprint_owned=row["blueprint_owned"],
                    can_fabricate=row["can_fabricate"], missing=missing)
    raise ValueError("找不到指定设备编号")


LOOKUPS = {
    "find_gardens_for_crop": ("item_id", crop_gardens),
    "find_feeder_space_for_food": ("item_id", feeder_space),
    "find_plort_silo_space": ("item_id", plort_space),
    "find_corrals_for_food": ("item_id", food_corrals),
    "inspect_recipe_shortfall": ("gadget_id", recipe_shortfall),
}


def backpack_quote():
    prices = rpc("sr_market")
    by_item = {row["item_id"]: row["unit_price"] for row in prices["prices"]}
    lines = []
    for slot, row in backpack().items():
        item, count = row["item_id"], row["count"]
        if count and item in by_item:
            lines.append(dict(slot=slot, item_id=item, count=count, unit_price=by_item[item],
                              subtotal=count * by_item[item]))
    return dict(market_closed=prices["market_closed"], slots=lines,
                quoted_total=sum(row["subtotal"] for row in lines))


def exchange_pack_shortfall():
    offers = [o for o in rpc("sr_exchange")["offers"] if not o["complete"]]
    stock = Counter()
    if offers:
        for row in backpack().values():
            stock[row["item_id"]] += row["count"]
    result = []
    for offer in offers:
        needed = Counter()
        for request in offer["requests"]:
            if request["remaining"] > 0:
                needed[request["item_id"]] += request["remaining"]
        lines = [dict(item_id=item, remaining=n, in_pack=stock[item], missing=max(0, n-stock[item]))
                 for item, n in needed.items()]
        result.append(dict(offer_id=offer["offer_id"], rancher=offer["rancher"],
                           pack_sufficient=all(not row["missing"] for row in lines), requests=lines))
    return dict(offers=result)


def nearest_exploration(rows):
    located = (r for r in rows if r["loaded"] and r["player_distance"] is not None)
    return dict(nearest=min(located, key=lambda r: r["player_distance"], default=None))


def nearest_unopened_pod():
    return nearest_exploration(r for r in rpc("sr_pods", {"include_open": False})["pods"]
                               if not r["opened"])


def nearest_locked_door():
    return nearest_exploration(r for r in rpc("sr_doors")["doors"] if r["state"] == "LOCKED")


def nearest_unpopped_gordo():
    return nearest_exploration(r for r in rpc("sr_gordos", {"include_popped": False})["gordos"]
                               if not r["popped"])


def gordo_feed_remaining():
    rows = []
    for r in rpc("sr_gordos", {"include_popped": False})["gordos"]:
        if r["loaded"] and not r["popped"] and r["target_count"] is not None and r["eaten"] >= 0:
            rows.append(dict(r, remaining_points=max(0, r["target_count"] - r["eaten"])))
    rows.sort(key=lambda r: (r["remaining_points"], r["gordo_id"]))
    return dict(total=len(rows), gordos=rows)


def backpack_stack_space():
    slots = [dict(slot=index, item_id=row["item_id"], count=row["count"],
                  capacity=row["capacity"], free=max(0, row["capacity"] - row["count"]))
             for index, row in backpack().items() if row["count"] > 0]
    return dict(slots=slots)


def empty_backpack_slots():
    slots = [index for index, row in backpack().items() if row["count"] == 0]
    return dict(total=len(slots), slots=slots)


def empty_silo_slots():
    slots = [dict(storage_id=storage, slot=index) for (storage, index), row in storage_slots("")
             if row["count"] == 0]
    return dict(total=len(slots), slots=slots)


SUMMARIES = {
    "inspect_backpack_stack_space": backpack_stack_space,
    "find_empty_backpack_slots": empty_backpack_slots,
    "find_empty_silo_slots": empty_silo_slots,
    "quote_backpack_plorts": backpack_quote,
    "inspect_exchange_pack_shortfall": exchange_pack_shortfall,
    "find_nearest_unopened_pod": nearest_unopened_pod,
    "find_nearest_locked_door": nearest_locked_door,
    "find_nearest_unpopped_gordo": nearest_unpopped_gordo,
    "inspect_gordo_feed_remaining": gordo_feed_remaining,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="check", required=True)
    for name in (*CHECKS, *SUMMARIES):
        commands.add_parser(name)
    for name, (key, _) in LOOKUPS.items():
        commands.add_parser(name).add_argument(key)
    args = parser.parse_args()
    name = args.check
    if name in SUMMARIES:
        report("finished", **SUMMARIES[name]())
        return
    if name in LOOKUPS:
        key, lookup = LOOKUPS[name]
        value = getattr(args, key).strip().upper()
        if not value:
            parser.error(f"{key} 不能为空")
        report("finished", **lookup(value))
        return
    tool, collection, matches, fields = CHECKS[name]
    if tool == "sr_gadgets":
        entries = paged_rows(tool, collection, owned_only=name != "find_unowned_unlocked_blueprints")
    elif tool == "sr_gadget_sites":
        entries = paged_rows(tool, collection)
    else:
        data = rpc(tool)
        entries = data[collection]
    rows = [{key: row[key] for key in fields} for row in entries if matches(row)]
    if name == "find_stocked_collectors":
        for row in rows:
            row["slots"] = [s for s in row["slots"] if s["accessible"] and s["count"] > 0]
    result = dict(total=len(rows), matches=rows)
    if tool == "sr_corrals":
        result["radius"] = data["radius"]
    if tool in ("sr_market", "sr_market_advice"):
        result["market_closed"] = data["market_closed"]
    report("finished", **result)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        report("stopped", error=str(exc))
        sys.exit(1)
