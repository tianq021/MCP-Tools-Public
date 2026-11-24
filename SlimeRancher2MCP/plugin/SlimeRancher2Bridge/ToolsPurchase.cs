// ToolsPurchase —— 用新币购买科技设备蓝图
//
//   sr2_buy_blueprint  按蓝图 ID/名称购买"科技设备"（Gadget）蓝图，默认预演。
//
// 原生路径（读 Assembly-CSharp.dll 元数据确认）：
//   GadgetDefinition.BlueprintCost                 蓝图价格（新币）
//   GadgetsModel.blueprints / availBlueprints      已拥有 / 可购买的蓝图集合
//   GadgetDirector.AddBlueprint(GadgetDefinition, Boolean) -> Boolean
//   AcquisitionUtility.AcquireGadgetBlueprint(GadgetDefinition, Boolean) -> Boolean  （静态，兜底）
//   PlayerState.GetCurrency(ICurrency) / SpendCurrency(ICurrency, Int32, ...)
//   CurrencyUtility.DefaultCurrency                默认货币（新币）
//
// 写操作：默认 dry_run=true；执行需 dry_run=false + 唯一 request_id，执行后回读余额与拥有状态。
using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.Economy;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsPurchase
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_buy_blueprint", BuyBlueprint, mainThread: true, write: true);
        }

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };

        internal static string Str(Row args, string key)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : "";

        // 新币余额。拿不到时返回 null，而不是 0，避免把"读不到"当成"没钱"。
        internal static int? Balance(object playerState, object currency)
        {
            if (playerState == null || currency == null) return null;
            var value = Il2CppUtil.Invoke(playerState, "GetCurrency", new object[] { currency });
            if (value == null) return null;
            try { return Convert.ToInt32(value); } catch { return null; }
        }

        static Row BuyBlueprint(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var game = scene.GameModel;
            if (game == null || !game.isInitialized) return Unavailable("game_model_unavailable");
            var model = game.GadgetsModel;
            var catalog = ToolsFabrication.Catalog(model);
            if (catalog == null) return Unavailable("blueprint_catalog_unavailable");

            string query = Str(args, "gadget_id");
            if (query.Length == 0) query = Str(args, "query");
            if (query.Length == 0) return Unavailable("gadget_id_required");
            bool preview = !(args.TryGetValue("dry_run", out var dry) && dry is bool flag && !flag);

            var matches = catalog.Values.Where(d => d != null && (
                    string.Equals(d.ReferenceId, query, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(d.name, query, StringComparison.OrdinalIgnoreCase)
                    || (d.ReferenceId ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
                    || (d.name ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();
            if (matches.Count == 0) return Unavailable("gadget_not_found");
            if (matches.Count > 1)
                return new Row
                {
                    ["available"] = false, ["reason"] = "ambiguous_gadget",
                    ["candidates"] = matches.Select(d => d.ReferenceId).Take(20).ToArray(),
                };

            var def = matches[0];
            bool owned = model.blueprints != null && model.blueprints.Contains(def);
            int cost = def.BlueprintCost;
            var playerState = Il2CppUtil.Try(scene, "PlayerState");
            object currency = CurrencyUtility.DefaultCurrency;
            int? balance = Balance(playerState, currency);
            bool affordable = balance.HasValue && balance.Value >= cost;
            bool eligible = !owned && cost >= 0 && (cost == 0 || affordable);

            var row = new Row
            {
                ["available"] = true, ["dry_run"] = preview,
                ["gadget_id"] = def.ReferenceId, ["name"] = def.name,
                ["title"] = Il2CppUtil.Localized(def.LocalizedName),
                ["type"] = def.Type.ToString(),
                ["owned"] = owned, ["owned_before"] = owned,
                ["blueprint_cost"] = cost, ["balance_before"] = balance,
                ["eligible"] = eligible, ["purchased"] = false,
            };
            if (owned) row["reason"] = "already_owned";
            else if (cost < 0) row["reason"] = "cost_unknown";
            else if (!affordable) row["reason"] = "insufficient_newbucks";
            if (preview || !eligible) return row;

            if (cost > 0)
            {
                if (playerState == null || currency == null) return Unavailable("currency_unavailable");
                Il2CppUtil.Invoke(playerState, "SpendCurrency", new object[] { currency, cost, null });
                row["spend_error"] = Il2CppUtil.LastInvokeError;
            }

            // 授予蓝图：优先 GadgetDirector.AddBlueprint，失败退回静态 AcquisitionUtility。
            bool granted = false;
            string grantPath = null;
            var director = scene.GadgetDirector;
            if (director != null)
            {
                var ok = Il2CppUtil.Invoke(director, "AddBlueprint", new object[] { def, false });
                row["add_blueprint_error"] = Il2CppUtil.LastInvokeError;
                granted = ok is bool added && added;
                if (granted) grantPath = "gadget_director";
            }
            if (!granted)
            {
                var ok = Il2CppUtil.InvokeMethod(
                    typeof(AcquisitionUtility).GetMethod("AcquireGadgetBlueprint"), null,
                    new object[] { def, false });
                row["acquire_blueprint_error"] = Il2CppUtil.LastInvokeError;
                granted = ok is bool acquired && acquired;
                if (granted) grantPath = "acquisition_utility";
            }
            row["grant_path"] = grantPath;
            row["purchased"] = granted;

            // 回读：拥有状态 + 余额。
            bool ownedAfter = model.blueprints != null && model.blueprints.Contains(def);
            row["owned_after"] = ownedAfter;
            row["balance_after"] = Balance(Il2CppUtil.Try(scene, "PlayerState"), CurrencyUtility.DefaultCurrency);
            if (!ownedAfter) row["reason"] = granted ? "readback_mismatch" : "grant_failed";
            return row;
        });
    }
}
