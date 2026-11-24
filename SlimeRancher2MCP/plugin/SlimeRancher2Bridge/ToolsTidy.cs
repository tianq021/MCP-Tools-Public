// ToolsTidy —— 一键整理背包（按玩家约定）
//
//   sr2_tidy  默认预演；执行时按规则处理背包：
//     · 粉色结晶 PinkPlort、水滴结晶 PuddlePlort → 精炼炉
//     · 其它 *Craft 矿物/材料 → 精炼炉
//     · 其它结晶 → 按当前市场价卖出
//     · 活体（史莱姆/鸡）、水等 → 留在背包
//   组合调用已实测的 sr2_refine / sr2_sell_plorts 内部实现，并把两边结果回读出来。
using System;
using System.Collections.Generic;
using System.Linq;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsTidy
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_tidy", Run, mainThread: true, write: true);

        static bool IsPlort(string id) => id != null && id.EndsWith("Plort", StringComparison.OrdinalIgnoreCase);
        static bool IsCraft(string id) => id != null && id.IndexOf("Craft", StringComparison.OrdinalIgnoreCase) >= 0;
        static bool AlwaysRefine(string id)
            => string.Equals(id, "IdentifiableType.PinkPlort", StringComparison.OrdinalIgnoreCase)
            || string.Equals(id, "IdentifiableType.PuddlePlort", StringComparison.OrdinalIgnoreCase);

        // 返回 refine / sell / keep 三组计划
        static (List<Row> Plan, List<Row> Keep) BuildPlan()
        {
            var plan = new List<Row>();
            var keep = new List<Row>();
            var bag = ToolsInventory.Read(new Row());
            if (bag == null || !(bag.TryGetValue("slots", out var slotsRaw) && slotsRaw is List<object> slots))
                return (plan, keep);
            foreach (var slotRaw in slots)
            {
                if (!(slotRaw is Row slot)) continue;
                string id = slot.TryGetValue("id", out var idRaw) ? Convert.ToString(idRaw) : null;
                int count = slot.TryGetValue("count", out var countRaw) ? Convert.ToInt32(countRaw) : 0;
                if (string.IsNullOrEmpty(id) || count <= 0) continue;
                string name = slot.TryGetValue("name", out var nameRaw) ? Convert.ToString(nameRaw) : id;
                string action;
                if (IsPlort(id)) action = AlwaysRefine(id) ? "refine" : "sell";
                else if (IsCraft(id)) action = "refine";
                else action = "keep";
                var row = new Row
                {
                    ["slot"] = slot.TryGetValue("index", out var indexRaw) ? indexRaw : null,
                    ["id"] = id,
                    ["name"] = name,
                    ["count"] = count,
                    ["action"] = action,
                };
                if (action == "keep") keep.Add(row);
                else plan.Add(row);
            }
            return (plan, keep);
        }

        internal static Row Run(Row args)
        {
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            var (plan, keep) = BuildPlan();
            var result = new Row
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["plan"] = plan,
                ["kept"] = keep,
                ["plan_count"] = plan.Count,
                ["note"] = "refine=粉色/水滴结晶与 *Craft 进精炼炉；sell=其它结晶按市场价卖；keep=活体/水等不动",
            };
            if (preview) return result;

            var refined = new List<object>();
            var sold = new List<object>();
            var failures = new List<object>();
            foreach (var row in plan)
            {
                string id = Convert.ToString(row["id"]);
                string action = Convert.ToString(row["action"]);
                try
                {
                    if (action == "refine")
                    {
                        var deposit = ToolsRefinery.Deposit(new Row
                        {
                            ["dry_run"] = false,
                            ["item"] = id,
                        });
                        refined.Add(new Row
                        {
                            ["id"] = id,
                            ["name"] = row["name"],
                            ["count"] = row["count"],
                            ["deposited"] = deposit != null && deposit.TryGetValue("deposited", out var dep) ? dep : null,
                            ["result"] = deposit != null && deposit.TryGetValue("failures", out var f) ? f : null,
                        });
                    }
                    else
                    {
                        var sale = ToolsSell.Sell(new Row
                        {
                            ["dry_run"] = false,
                            ["source"] = "vacpack",
                            ["item"] = id,
                        });
                        sold.Add(new Row
                        {
                            ["id"] = id,
                            ["name"] = row["name"],
                            ["count"] = row["count"],
                            ["sold"] = sale != null && sale.TryGetValue("sold", out var s) ? s : null,
                            ["failures"] = sale != null && sale.TryGetValue("failures", out var f) ? f : null,
                        });
                    }
                }
                catch (Exception e)
                {
                    failures.Add(new Row
                    {
                        ["id"] = id,
                        ["action"] = action,
                        ["error"] = e.GetBaseException().Message,
                    });
                }
            }
            var (planAfter, keepAfter) = BuildPlan();
            var currency = ToolsCurrency.Read(new Row());
            result["refined"] = refined;
            result["sold"] = sold;
            result["failures"] = failures;
            result["remaining"] = keepAfter;
            result["remaining_plan"] = planAfter;
            result["coins_after"] = currency != null && currency.TryGetValue("amount", out var amount) ? amount : null;
            return result;
        }
    }
}
