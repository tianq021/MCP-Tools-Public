// RanchExtras3 —— 第三批扩展工具：史莱姆饮食百科、委托待选/接受
// C# 5 语法；同一 partial class 复用 helper。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterExtras3(BridgeServer server)
        {
            Read(server, "sr_slime_info", SlimeInfo);
            Read(server, "sr_exchange_pending", ExchangePending);
            Action(server, "sr_exchange_accept", ExchangeAccept);
        }

        // ---------------------------------------------------------------- 史莱姆饮食/产出

        static string[] NamesOf(Identifiable.Id[] ids)
        {
            if (ids == null) return new string[0];
            return ids.Select(x => x.ToString()).ToArray();
        }

        static Dictionary<string, object> SlimeInfo(SceneContext scene, Dictionary<string, object> args)
        {
            var gameContext = SRSingleton<GameContext>.Instance;
            var definitions = gameContext == null ? null : gameContext.SlimeDefinitions;
            if (definitions == null)
                return Fail("NOT_READY", "SlimeDefinitions 不可用。");
            string idArg = Arg(args, "item_id");
            bool includeLargo = Flag(args, "include_largo", true);
            Identifiable.Id wanted = Identifiable.Id.NONE;
            bool hasOne = idArg.Length != 0 && ParseItem(idArg, out wanted);
            if (idArg.Length != 0 && !hasOne)
                return Fail("INVALID_ITEM", "用 sr_item_catalog 里的 slime item_id（如 PINK_SLIME）。");

            var rows = new List<Dictionary<string, object>>();
            IEnumerable<SlimeDefinition> source = hasOne
                ? new SlimeDefinition[] { definitions.GetSlimeByIdentifiableId(wanted) }
                : definitions.Slimes;
            foreach (var definition in source)
            {
                if (definition == null) continue;
                if (!includeLargo && definition.IsLargo) continue;
                var diet = definition.Diet;
                var row = Row(
                    "item_id", definition.IdentifiableId.ToString(),
                    "name", definition.Name,
                    "is_largo", definition.IsLargo,
                    "can_largofy", definition.CanLargofy,
                    "base_slimes", definition.BaseSlimes == null ? new string[0]
                        : definition.BaseSlimes.Where(x => x != null).Select(x => x.IdentifiableId.ToString()).ToArray(),
                    "favorite_toys", NamesOf(definition.FavoriteToys));
                if (diet != null)
                {
                    row["food_groups"] = diet.MajorFoodGroups == null ? new string[0]
                        : diet.MajorFoodGroups.Select(x => x.ToString()).ToArray();
                    row["favorites"] = NamesOf(diet.Favorites);
                    row["favorite_names"] = diet.Favorites == null ? new string[0]
                        : diet.Favorites.Select(x => Identifiable.GetName(x, false)).ToArray();
                    row["additional_foods"] = NamesOf(diet.AdditionalFoods);
                    row["produces"] = NamesOf(diet.Produces);
                    row["favorite_production_count"] = diet.FavoriteProductionCount;
                }
                rows.Add(row);
            }
            if (hasOne && rows.Count == 0)
                return Fail("NOT_FOUND", "没有找到该史莱姆定义（可能是未注册的模组史莱姆）。");
            return Row("slimes", rows.ToArray(), "total", rows.Count);
        }

        // ---------------------------------------------------------------- 委托待选/接受

        static WorldModel WorldModelOf(SceneContext scene)
        {
            try
            {
                var field = typeof(ExchangeDirector).GetField("worldModel",
                    BindingFlags.Instance | BindingFlags.NonPublic);
                return field == null ? null : field.GetValue(scene.ExchangeDirector) as WorldModel;
            }
            catch
            {
                return null;
            }
        }

        static Dictionary<string, object> ExchangePending(SceneContext scene, Dictionary<string, object> args)
        {
            var world = WorldModelOf(scene);
            if (world == null) return Fail("TARGET_UNAVAILABLE", "拿不到 WorldModel（游戏版本或字段名变了）。");
            var pending = world.pendingOfferRancherIds ?? new List<string>();
            var offers = new List<Dictionary<string, object>>();
            foreach (ExchangeDirector.OfferType type in Enum.GetValues(typeof(ExchangeDirector.OfferType)))
            {
                string id = scene.ExchangeDirector.GetOfferId(type);
                if (id == null) continue;
                offers.Add(Row("offer_type", type.ToString(), "offer_id", id,
                    "rancher", scene.ExchangeDirector.GetOfferRancherId(type)));
            }
            return Row("pending_ranchers", pending.ToArray(), "pending_count", pending.Count,
                "current_offers", offers.ToArray(),
                "general_offer_id", scene.ExchangeDirector.GetOfferId(ExchangeDirector.OfferType.GENERAL),
                "auto_selectable", pending.Count == 1 && scene.ExchangeDirector.GetOfferId(ExchangeDirector.OfferType.GENERAL) == null);
        }

        static Dictionary<string, object> ExchangeAccept(SceneContext scene, Dictionary<string, object> args)
        {
            var world = WorldModelOf(scene);
            if (world == null) return Fail("TARGET_UNAVAILABLE", "拿不到 WorldModel（游戏版本或字段名变了）。");
            string rancher = Arg(args, "rancher_id");
            var pending = world.pendingOfferRancherIds ?? new List<string>();
            string existing = scene.ExchangeDirector.GetOfferId(ExchangeDirector.OfferType.GENERAL);
            var result = Row("pending_ranchers", pending.ToArray(), "pending_count", pending.Count,
                "current_general_offer", existing,
                "requested_rancher", rancher.Length == 0 ? null : rancher);
            if (existing != null)
            {
                result["ok"] = true;
                result["note"] = "今天已经有委托了，无需接受。";
                return result;
            }
            if (pending.Count == 0)
                return Fail("NO_PENDING", "当前没有待选委托人（等下一次委托刷新）。");
            if (rancher.Length != 0 && !pending.Contains(rancher))
                return Fail("INVALID_RANCHER", "rancher_id 不在待选列表: " + string.Join(",", pending.ToArray()));
            if (rancher.Length == 0 && pending.Count > 1)
                return Fail("NEED_RANCHER", "有多个待选委托人，请传 rancher_id（见 pending_ranchers）。");
            if (Preview(args))
            {
                result["planned_rancher"] = rancher.Length == 0 ? pending[0] : rancher;
                return result;
            }
            bool selected;
            if (rancher.Length != 0)
            {
                selected = scene.ExchangeDirector.SelectDailyOffer(rancher, true);
            }
            else
            {
                scene.ExchangeDirector.TryToAcceptNewOffer();
                selected = scene.ExchangeDirector.GetOfferId(ExchangeDirector.OfferType.GENERAL) != null;
            }
            result["ok"] = selected;
            result["general_offer_id_after"] = scene.ExchangeDirector.GetOfferId(ExchangeDirector.OfferType.GENERAL);
            result["rancher_after"] = scene.ExchangeDirector.GetOfferRancherId(ExchangeDirector.OfferType.GENERAL);
            if (!selected)
                return Fail("ACCEPT_FAILED", "游戏拒绝了接受委托（可能已有委托或状态不支持）。");
            return result;
        }
    }
}
