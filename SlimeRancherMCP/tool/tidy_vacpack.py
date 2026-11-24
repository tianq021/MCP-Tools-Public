"""背包整理。默认预演，用户授权后用 --apply 执行。输出为逐行 JSON。"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
from bridge_client import call
from gen_slime_docs import food_group_of


def report(status, **fields):
    print(json.dumps(dict(status=status, **fields), ensure_ascii=False), flush=True)


def rpc(name, args=None, request_id=None):
    reply = call(name, args or {}, request_id)
    if not reply["ok"]:
        raise RuntimeError(f"{name}: {reply.get('error')}")
    return reply["data"]


def backpack():
    pack = rpc("sr_vacpack", {"include_empty": True})
    if pack["mode"] != "DEFAULT":
        raise RuntimeError("请切换到普通背包")
    return {s["slot"]: s for s in pack["slots"]}


def storage_slots(item, storage_type="NON_SLIMES"):
    offset = 0
    while offset is not None:
        page = rpc("sr_storage", {"storage_type": storage_type, "item_id": item,
                                  "include_empty": True, "offset": offset})
        for store in page["storage"]:
            # PLORT collectors and FOOD feeder stores are dedicated corral equipment.
            # The bridge emits ports only for storage attached to a SILO plot.
            if store.get("storage_type") != storage_type:
                continue
            if storage_type == "NON_SLIMES" and "ports" not in store:
                continue
            for slot in store["slots"]:
                if slot["accessible"]:
                    yield (store["storage_id"], slot["slot"]), slot
        offset = page["next_offset"]


def refinery(item):
    return rpc("sr_refinery", {"item_id": item})["resources"][0]


def collector_inventory():
    stores = rpc("sr_collectors")["plort_storage"]
    return {(store["storage_id"], slot["slot"]): slot for store in stores
            if store["storage_type"] == "PLORT" for slot in store["slots"]
            if slot["accessible"]}


def corral_diets():
    corrals = rpc("sr_corrals", {"radius": 8})["corrals"]
    definitions = {s["item_id"]: s for s in rpc("sr_slime_info", {"include_largo": True})["slimes"]}
    for corral in corrals:
        if not corral["loaded"]:
            continue
        residents = corral["slime_types"]
        diets = [definitions.get(s["item_id"], {}) for s in residents]
        if not diets or any("food_groups" not in d for d in diets):
            continue
        if sum(s["count"] for s in residents) != corral["slime_count"]:
            continue
        stores = [s for s in corral["storages"] if s["storage_type"] == "FOOD"]
        linked = len(stores) == len(corral["feeders"]) == 1
        yield corral["plot_id"], stores[0]["storage_id"] if linked else None, diets


def feeder_diets(include_all_corrals=False):
    feeders = {}
    for plot_id, storage_id, diets in corral_diets():
        if storage_id is not None:
            feeders[storage_id] = diets
        if storage_id is not None or include_all_corrals:
            report("feeder_diet", plot_id=plot_id, storage_id=storage_id,
                   slimes=[dict(item=d["item_id"], favorites=d["favorites"],
                                food_groups=d["food_groups"]) for d in diets])
    return feeders


def compatible_food(item, diets):
    group = food_group_of(item)
    return all(group in d["food_groups"] or item in d["additional_foods"]
               or item in d["favorites"] for d in diets)


def food_targets(item, feeders, favorites_only):
    for dest, slot in storage_slots(item, "FOOD"):
        diets = feeders.get(dest[0])
        if dest[1] != 0 or not diets or (slot["count"] and slot["item_id"] != item):
            continue
        favorite = any(item in d["favorites"] for d in diets)
        if favorite != favorites_only:
            continue
        if compatible_food(item, diets):
            yield dest, slot


def tidy(task, apply):
    reserved = {}  # Preview only. Different items must not claim the same empty slot.
    from_collectors = task == "store_collector_plorts"
    pack = collector_inventory() if from_collectors else backpack()
    feeders = {}
    if task == "fill_feeders":
        food = [src for src, row in pack.items() if row["count"] and food_group_of(row["item_id"]) != "OTHER"]
        feeders = feeder_diets() if food else {}
        jobs = [(src, favorite) for favorite in (True, False) for src in food]
    else:
        suffix = {"store_plorts": "_PLORT", "store_collector_plorts": "_PLORT",
                  "deposit_materials": "_CRAFT"}[task]
        jobs = [(src, None) for src, row in pack.items() if row["item_id"].endswith(suffix)]
    for src, favorites_only in jobs:
        source_id, source_slot = src if from_collectors else (0, src)
        item, remaining = pack[src]["item_id"], pack[src]["count"]
        if not remaining:
            continue
        if item.endswith("_PLORT"):
            candidates = []
            for dest, slot in storage_slots(item):
                claimed_item, claimed = reserved.get(dest, (item, 0))
                if claimed_item != item or slot["free_space_for_item"] <= claimed:
                    continue
                if slot["count"] and slot["item_id"] != item:
                    continue
                candidates.append((slot["count"] + claimed, dest, slot))
            # Fill the fullest matching stacks first, including stacks planned in preview.
            candidates.sort(key=lambda entry: entry[0], reverse=True)
            targets = [(dest, slot) for _, dest, slot in candidates]
        elif item.endswith("_CRAFT"):
            targets = [(None, refinery(item))]
        elif favorites_only is not None:
            targets = food_targets(item, feeders, favorites_only)
        else:
            report("kept", source_slot=src, item=item, count=remaining, reason="other_item")
            continue
        for dest, slot in targets:
            key = dest if dest else item
            claimed_item, claimed = reserved.get(key, (item, 0))
            room = slot["free_space_for_item"] if dest else slot["free_space"]
            qty = min(remaining, room - claimed, 999)
            if claimed_item != item or qty <= 0:
                continue
            args = dict(source_slot=source_slot, source_storage_id=source_id, item_id=item, count=qty)
            action = "sr_deposit_refinery"
            if dest:
                action = "sr_move"
                args.update(destination_storage_id=dest[0], destination_slot=dest[1])
            rpc(action, dict(args, dry_run=True))
            if apply:
                rid = uuid.uuid4().hex
                report("executing", request_id=rid, action=action, **args)
                receipt = rpc(action, dict(args, dry_run=False), rid)
                source = collector_inventory()[src] if from_collectors else backpack()[src]
                storage_type = "FOOD" if favorites_only is not None else "NON_SLIMES"
                target = dict(storage_slots(item, storage_type))[dest] if dest else refinery(item)
                before = receipt["destination_count_before" if dest else "refinery_count_before"]
                debit = receipt["source_count_before"] - source["count"]
                credit = target["count"] - before
                verified = (debit == credit == qty and
                            (not source["count"] or source["item_id"] == item) and
                            target["item_id"] == item)
                report("verified" if verified else "mismatch", request_id=rid, action=action,
                       **args, debit=debit, credit=credit, source_after=source, target_after=target)
                if not verified:
                    raise RuntimeError("回读不符，整理已停止")
            else:
                reserved[key] = item, claimed + qty
                report("preview", action=action, **args)
            remaining -= qty
            if not remaining:
                break
        pack[src]["count"] = remaining
        if remaining and favorites_only is not True:
            reason = "no_compatible_feeder_space" if favorites_only is False else "insufficient_space"
            report("kept", source_storage_id=source_id, source_slot=source_slot,
                   item=item, count=remaining, reason=reason)
    report("finished", mode="apply" if apply else "preview")


READ_TOOLS = {
    "inspect_backpack": "sr_vacpack",
    "inspect_collectors": "sr_collectors",
    "inspect_feeders": "sr_feeders",
    "inspect_refinery": "sr_refinery",
    "inspect_market": "sr_market",
    "inspect_drones": "sr_drones",
    "inspect_extractors": "sr_extractors",
    "inspect_corrals": "sr_corrals",
    "inspect_active_exchange": "sr_exchange",
    "inspect_exchange_offers": "sr_exchange_pending",
    "inspect_clock": "sr_time",
    "inspect_player": "sr_player",
    "inspect_map_data": "sr_map_data",
}
WRITE_TOOLS = ("store_plorts", "deposit_materials", "fill_feeders", "store_collector_plorts")

# Each command maps to one read operation with fixed filters.
QUERY_PRESETS = {
    "inspect_silo_inventory": ("sr_model_storage", {"storage_type": "NON_SLIMES", "include_empty": False}),
    "inspect_collector_inventory": ("sr_model_storage", {"storage_type": "PLORT", "include_empty": False}),
    "inspect_feeder_inventory": ("sr_model_storage", {"storage_type": "FOOD", "include_empty": False}),
    "find_loose_plorts": ("sr_loose_items", {"category": "plort", "offset": 0}),
    "find_loose_materials": ("sr_loose_items", {"category": "craft", "offset": 0}),
    "find_loose_food": ("sr_loose_items", {"category": "food", "offset": 0}),
    "inspect_unopened_pods": ("sr_pods", {"include_open": False}),
    "inspect_unpopped_gordos": ("sr_gordos", {"include_popped": False}),
    "inspect_key_doors": ("sr_doors", {}),
    "inspect_zones": ("sr_zones", {}),
    "inspect_plots": ("sr_plots", {}),
    "inspect_available_player_upgrades": ("sr_player_shop", {"available_only": True}),
    "inspect_unread_mail": ("sr_mail", {"unread_only": True, "offset": 0}),
    "search_item_catalog": ("sr_item_catalog", {"offset": 0}),
    "locate_item_stocks": ("sr_find_items", {"offset": 0}),
    "inspect_slime_species": ("sr_slime_info", {}),
    "quote_plort": ("sr_market", {}),
    "inspect_refinery_item": ("sr_refinery", {}),
    "inspect_plot_upgrades": ("sr_plot_shop", {}),
    "inspect_drone_options": ("sr_drone_options", {}),
    "search_gadget_blueprints": ("sr_gadgets", {"offset": 0}),
    "find_zone_gadget_sites": ("sr_gadget_sites", {"offset": 0}),
    "search_pedia": ("sr_pedia", {"offset": 0}),
}

QUERY_ARGUMENTS = {
    "search_item_catalog": "query",
    "locate_item_stocks": "item_id",
    "inspect_slime_species": "item_id",
    "quote_plort": "item_id",
    "inspect_refinery_item": "item_id",
    "inspect_plot_upgrades": "plot_id",
    "inspect_drone_options": "site_id",
    "search_gadget_blueprints": "query",
    "find_zone_gadget_sites": "zone",
    "search_pedia": "query",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="task", required=True)
    for task in (*READ_TOOLS, "inspect_silos", "inspect_diets"):
        commands.add_parser(task)
    for task, (_, filters) in QUERY_PRESETS.items():
        command = commands.add_parser(task)
        if task in QUERY_ARGUMENTS:
            command.add_argument(QUERY_ARGUMENTS[task])
        if "offset" in filters:
            command.add_argument("--offset", type=int, default=0, help="上一页返回的 next_offset")
    for task in WRITE_TOOLS:
        commands.add_parser(task).add_argument("--apply", action="store_true", help="执行并回读对账")
    args = parser.parse_args()
    if args.task in READ_TOOLS:
        report("finished", **rpc(READ_TOOLS[args.task]))
    elif args.task in QUERY_PRESETS:
        name, filters = QUERY_PRESETS[args.task]
        if args.task in QUERY_ARGUMENTS:
            key = QUERY_ARGUMENTS[args.task]
            value = getattr(args, key).strip()
            if not value:
                parser.error(f"{key} 不能为空")
            filters = dict(filters, **{key: value})
        if "offset" in filters:
            if args.offset < 0:
                parser.error("--offset 必须大于或等于 0")
            filters = dict(filters, offset=args.offset)
        data = rpc(name, filters)
        if name == "sr_model_storage":
            plot_type = "SILO" if filters["storage_type"] == "NON_SLIMES" else "CORRAL"
            plots = [p for p in data["plots"] if p["plot_type"] == plot_type and p["storages"]]
            data = dict(plots=plots, total=len(plots))
        report("finished", **data)
    elif args.task == "inspect_silos":
        for (storage_id, _), slot in storage_slots(""):
            report("silo_slot", storage_id=storage_id, **slot)
        report("finished")
    elif args.task == "inspect_diets":
        feeder_diets(include_all_corrals=True)
        report("finished")
    else:
        tidy(args.task, args.apply)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, IndexError) as exc:
        report("stopped", error=str(exc))
        sys.exit(1)
