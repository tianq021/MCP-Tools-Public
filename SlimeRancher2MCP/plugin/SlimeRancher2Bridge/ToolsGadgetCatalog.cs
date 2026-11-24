// ToolsGadgetCatalog —— 枚举游戏里全部设备蓝图资产（含还没获得的）
//
//   sr2_gadget_catalog  列出所有 GadgetDefinition 资产：ID、标题、蓝图价格、
//                       是否已拥有、以及制作配方（材料/货币/组件）。默认带配方。
//
// 为什么要单独一个工具：sr2_blueprints / sr2_gadget_recipe 只覆盖"玩家已拥有或商店可买"
// 的蓝图（来自 GadgetsModel 的三个集合，本机只有 25 项）。想查"小蜜蜂/无人机"这类
// 还没拿到蓝图的设备配方，只能直接枚举 Resources 里的全部 GadgetDefinition 资产。
// 只读，不修改存档。
using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsGadgetCatalog
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_gadget_catalog", Run, mainThread: true);

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };

        static string Str(Row args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        static bool Flag(Row args, string key, bool fallback)
            => args.TryGetValue(key, out var value) && value is bool flag ? flag : fallback;

        static List<Row> Costs(GadgetDefinition gadget)
        {
            var rows = new List<Row>();
            var cost = gadget.CraftingCosts;
            if (cost == null) return rows;
            if (cost.IdentCosts != null)
                foreach (var entry in cost.IdentCosts)
                {
                    var item = entry?.IdentType;
                    if (item == null) continue;
                    rows.Add(new Row { ["kind"] = "material", ["id"] = item.ReferenceId, ["name"] = item.name, ["amount"] = entry.Amount });
                }
            if (cost._currencyCosts != null)
                foreach (var entry in cost._currencyCosts)
                {
                    var currency = entry?.AssignedCurrency;
                    if (currency == null) continue;
                    rows.Add(new Row { ["kind"] = "currency", ["id"] = currency.ReferenceId, ["amount"] = entry.Amount });
                }
            if (cost.ComponentCosts != null)
                foreach (var component in cost.ComponentCosts)
                {
                    if (component == null) continue;
                    rows.Add(new Row { ["kind"] = "component", ["id"] = component.ReferenceId, ["name"] = component.name, ["amount"] = 1 });
                }
            return rows;
        }

        internal static Row Run(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (Il2Cpp.SceneContext)raw;
            var game = scene.GameModel;
            if (game == null || !game.isInitialized) return Unavailable("game_model_unavailable");
            var model = game.GadgetsModel;

            string query = Str(args, "query", "");
            int limit = 0;
            if (!int.TryParse(Str(args, "limit", "0"), out limit)) limit = 0;
            bool includeRecipe = Flag(args, "include_recipe", true);

            List<GadgetDefinition> all;
            try { all = UnityEngine.Resources.FindObjectsOfTypeAll<GadgetDefinition>().ToList(); }
            catch (Exception e) { return Unavailable("asset_enumeration_failed: " + e.GetBaseException().Message); }
            if (all == null) return Unavailable("asset_enumeration_failed");

            var ownedIds = new HashSet<string>(StringComparer.Ordinal);
            if (model != null && model.blueprints != null)
                foreach (var blueprint in model.blueprints)
                    if (blueprint != null && !string.IsNullOrEmpty(blueprint.ReferenceId)) ownedIds.Add(blueprint.ReferenceId);

            var rows = new List<Row>();
            foreach (var gadget in all)
            {
                if (gadget == null || string.IsNullOrEmpty(gadget.ReferenceId)) continue;
                string title = Il2CppUtil.Localized(gadget.LocalizedName);
                if (query.Length > 0
                    && (gadget.ReferenceId ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0
                    && (gadget.name ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0
                    && (title ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0) continue;
                var row = new Row
                {
                    ["id"] = gadget.ReferenceId,
                    ["name"] = gadget.name,
                    ["title"] = title,
                    ["type"] = gadget.Type.ToString(),
                    ["blueprint_cost"] = gadget.BlueprintCost,
                    ["owned"] = ownedIds.Contains(gadget.ReferenceId),
                };
                if (includeRecipe) row["recipe"] = Costs(gadget);
                rows.Add(row);
            }
            rows.Sort((a, b) => string.CompareOrdinal(a["id"] as string, b["id"] as string));
            int total = rows.Count;
            if (limit > 0) rows = rows.Take(limit).ToList();
            return new Row
            {
                ["available"] = true,
                ["asset_total"] = all.Count,
                ["matched_count"] = total,
                ["owned_count"] = ownedIds.Count,
                ["items"] = rows,
                ["note"] = "asset_total 是游戏里枚举到的 GadgetDefinition 资产总数；owned 以 GadgetsModel.blueprints 的 ReferenceId 对比。",
            };
        });
    }
}
