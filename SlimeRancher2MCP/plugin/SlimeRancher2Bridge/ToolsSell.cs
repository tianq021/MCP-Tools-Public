// ToolsSell —— 快速卖结晶（plort）
//
//   sr2_sell_plorts  把背包和/或收集器、仓库里的结晶按当前市场价卖给结晶市场；
//                    默认预演，执行需 dry_run=false + request_id
//
// 游戏 API（1.3.0 实测类型名）：
//   SceneContext.PlortEconomyDirector
//     TrySell(IdentifiableType id, int count, bool ignoreMarketShutdown, bool showUiNotification, IUIDisplayData source) -> bool
//     IsMarketShutdown() / TryGetMarketValues(id, out currentValue, out delta)
//   TrySell 只按「类型 + 数量」结算，不负责扣容器，所以执行后要自己从来源扣数量。
//   IdentifiableType.IsPlort 用于筛出结晶
//   新币：CurrencyList.GetDefaultCurrency() -> ICurrency，再 PlayerState.GetCurrency(ICurrency)（拿不到就返回 null）
//
// 安全：只有 TrySell 返回 true 才从来源扣数量；执行后回读来源与新币并给出实际到账金额。
//   source=vacpack（默认）只卖背包；storage 只卖容器（收集器/仓库）；both 两边都卖。
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using AmmoSlot = Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot;

namespace SlimeRancher2Bridge
{
    internal static class ToolsSell
    {
        static object Member(object instance, string name)
        {
            if (instance == null) return null;
            Type type = instance.GetType();
            var property = type.GetProperty(name);
            if (property != null) return property.GetValue(instance);
            var field = type.GetField(name);
            if (field != null) return field.GetValue(instance);
            throw new MissingMemberException(type.FullName, name);
        }

        static object Try(object instance, string name)
        {
            try { return Member(instance, name); }
            catch { return null; }
        }

        static Type FindType(string name)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(name) ?? assembly.GetType("Il2Cpp." + name);
                if (type != null) return type;
            }
            return null;
        }

        static List<object> Enumerate(object sequence) => Il2CppUtil.Enumerate(sequence);

        static bool BoolOf(object value, bool fallback = false)
        {
            if (value == null) return fallback;
            return value is bool flag ? flag : Convert.ToBoolean(value);
        }

        static int IntOf(object value, int fallback = 0)
        {
            if (value == null) return fallback;
            return Convert.ToInt32(value);
        }

        // ---------------- 新币 ----------------

        static object FindDefaultCurrency()
        {
            // 首选：CurrencyUtility.DefaultCurrency（静态属性，实测最可靠）
            try
            {
                var utilityType = FindType("CurrencyUtility") ?? FindType("MonomiPark.SlimeRancher.Economy.CurrencyUtility");
                var defaultProperty = utilityType?.GetProperty("DefaultCurrency");
                if (defaultProperty != null)
                {
                    var direct = defaultProperty.GetValue(null);
                    if (direct != null) return direct;
                }
            }
            catch
            {
            }
            try
            {
                var currencyListType = FindType("CurrencyList")
                    ?? FindType("MonomiPark.SlimeRancher.Economy.CurrencyList");
                if (currencyListType == null) return null;
                var resourcesType = Type.GetType("UnityEngine.Resources, UnityEngine.CoreModule");
                var method = resourcesType?.GetMethod("FindObjectsOfTypeAll", new[] { typeof(Type) });
                if (method == null) return null;
                foreach (var asset in Enumerate(method.Invoke(null, new object[] { currencyListType })))
                {
                    var getDefault = asset.GetType().GetMethod("GetDefaultCurrency", Type.EmptyTypes);
                    if (getDefault != null)
                    {
                        var currency = getDefault.Invoke(asset, null);
                        if (currency != null) return currency;
                    }
                }
            }
            catch
            {
            }
            return null;
        }

        static object ReadCoins(object playerState)
        {
            try
            {
                // Reuse the currency tool's three-path reader; it handles the ICurrency
                // wrapper conversion that plain reflection loses.
                var row = ToolsCurrency.Read(new Dictionary<string, object>(StringComparer.Ordinal));
                if (row != null && row.TryGetValue("amount", out var amount)) return amount;
            }
            catch
            {
            }
            return null;
        }

        // ---------------- 背包结晶枚举 ----------------

        static List<object> VacpackSlots(out object playerState)
        {
            object state = null;
            object ammo = null;
            ToolsNavigation.WithScene(scene =>
            {
                state = Try(scene, "PlayerState");
                ammo = state == null ? null : Try(state, "Ammo");
                return new Dictionary<string, object>();
            });
            playerState = state;
            return Enumerate(Try(ammo, "Slots"));
        }

        /// <summary>一条待卖记录：来自背包或某个容器槽位。</summary>
        sealed class Entry
        {
            public bool FromBag;
            public int BagIndex;
            public ToolsStorage.Silo Silo;
            public int SlotIndex;
            public AmmoSlot Slot;
        }

        static string StrArg(Dictionary<string, object> args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        internal static Dictionary<string, object> Sell(Dictionary<string, object> args)
        {
            bool preview = true;
            if (args.TryGetValue("dry_run", out var mode) && mode is bool flag) preview = flag;

            string itemArg = StrArg(args, "item", "");
            string countArg = StrArg(args, "count", "");
            int countLimit = -1;
            if (countArg.Length > 0 && (!int.TryParse(countArg, out countLimit) || countLimit <= 0))
                return new() { ["available"] = false, ["reason"] = "bad_count", ["hint"] = "count 必须是正整数，留空=全卖" };

            string sourceArg = StrArg(args, "source", "vacpack").ToLowerInvariant();
            if (sourceArg.Length == 0) sourceArg = "vacpack";
            if (sourceArg != "vacpack" && sourceArg != "storage" && sourceArg != "both")
                return new() { ["available"] = false, ["reason"] = "bad_source",
                    ["hint"] = "source 只能是 vacpack / storage / both" };
            bool wantBag = sourceArg != "storage";
            bool wantStorage = sourceArg != "vacpack";
            string storageArg = StrArg(args, "storage", "");

            object director = null;
            object playerState = null;
            List<ToolsStorage.Silo> silos = null;
            List<object> slots = VacpackSlots(out playerState);
            ToolsNavigation.WithScene(scene =>
            {
                director = Try(scene, "PlortEconomyDirector");
                if (wantStorage)
                {
                    var raw = (Il2Cpp.SceneContext)scene;
                    silos = ToolsStorage.Warehouses(raw, raw.Player?.transform.position);
                }
                return new Dictionary<string, object>();
            });
            if (director == null)
                return new() { ["available"] = false, ["reason"] = "market_unavailable" };

            var marketType = director.GetType();
            var shutdownMethod = marketType.GetMethod("IsMarketShutdown", Type.EmptyTypes);
            bool shutdown = shutdownMethod != null && BoolOf(shutdownMethod.Invoke(director, null));
            var sellMethod = marketType.GetMethod("TrySell");
            if (sellMethod == null)
                return new() { ["available"] = false, ["reason"] = "unsupported_game_api", ["hint"] = "找不到 TrySell" };
            var priceQuery = marketType.GetMethod("TryGetMarketValues");

            // 只在 source 涉及容器时筛选容器；留空=所有容器。
            if (wantStorage && !string.IsNullOrEmpty(storageArg))
            {
                if (silos == null || silos.Count == 0)
                    return new() { ["available"] = false, ["reason"] = "no_storage_found" };
                List<ToolsStorage.Silo> picked = null;
                if (int.TryParse(storageArg, out int only) && only >= 0 && only < silos.Count)
                    picked = new() { silos[only] };
                else
                {
                    var found = silos.Find(s => s.Key == storageArg);
                    if (found != null) picked = new() { found };
                }
                if (picked == null) return new() { ["available"] = false, ["reason"] = "bad_storage" };
                silos = picked;
            }

            var entries = new List<Entry>();
            if (wantBag)
            {
                for (int i = 0; i < slots.Count; i++)
                {
                    var slot = slots[i];
                    object id = Try(slot, "Id");
                    if (id == null || !BoolOf(Try(id, "IsPlort"))) continue;
                    if (IntOf(Try(slot, "Count")) <= 0) continue;
                    if (itemArg.Length > 0 && !ToolsStorage.Matches(itemArg, i, (AmmoSlot)slot)) continue;
                    entries.Add(new Entry { FromBag = true, BagIndex = i, Slot = (AmmoSlot)slot });
                }
            }
            if (wantStorage && silos != null)
            {
                for (int si = 0; si < silos.Count; si++)
                {
                    var silo = silos[si];
                    if (silo?.Live == null) continue;
                    for (int i = 0; i < silo.Live.Length; i++)
                    {
                        var slot = silo.Live[i];
                        object id = Try(slot, "Id");
                        if (id == null || !BoolOf(Try(id, "IsPlort"))) continue;
                        if (IntOf(Try(slot, "Count")) <= 0) continue;
                        if (itemArg.Length > 0 && !ToolsStorage.Matches(itemArg, -1, slot)) continue;
                        entries.Add(new Entry { FromBag = false, Silo = silo, SlotIndex = i, Slot = slot });
                    }
                }
            }
            if (entries.Count == 0)
                return new() { ["available"] = true, ["dry_run"] = preview, ["plorts"] = new List<object>(),
                    ["plort_count"] = 0, ["total_income_estimate"] = 0, ["coins_before"] = ReadCoins(playerState),
                    ["note"] = "没有找到可卖的结晶（source=" + sourceArg + "）" };

            object coinsBefore = ReadCoins(playerState);
            var plan = new List<Dictionary<string, object>>();
            int totalIncome = 0;

            for (int idx = 0; idx < entries.Count; idx++)
            {
                var entry = entries[idx];
                object id = Try(entry.Slot, "Id");
                int count = IntOf(Try(entry.Slot, "Count"));
                string idText = (Try(id, "ReferenceId") as string) ?? (Try(id, "name") as string);
                string name = Try(id, "name") as string;
                int sellCount = countLimit > 0 ? Math.Min(countLimit, count) : count;
                int unitPrice = 0, change = 0;
                try
                {
                    if (priceQuery != null)
                    {
                        object[] values = { id, 0, 0 };
                        if (BoolOf(priceQuery.Invoke(director, values)))
                        {
                            unitPrice = IntOf(values[1]);
                            change = IntOf(values[2]);
                        }
                    }
                }
                catch
                {
                }

                int income = unitPrice * sellCount;
                totalIncome += income;
                plan.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["index"] = idx,
                    ["source"] = entry.FromBag ? "vacpack" : "storage",
                    ["container"] = entry.FromBag ? null : entry.Silo.Key ?? entry.Silo.ObjectName,
                    ["container_kind"] = entry.FromBag ? null : (entry.Silo.Key != null && entry.Silo.Key.StartsWith("plot:") ? "plot_silo" : "scene_storage"),
                    ["container_loaded"] = entry.FromBag ? null : (object)entry.Silo.Loaded,
                    ["slot"] = entry.FromBag ? entry.BagIndex : entry.SlotIndex,
                    ["id"] = idText,
                    ["name"] = name,
                    ["count"] = count,
                    ["sell"] = sellCount,
                    ["unit_price"] = unitPrice,
                    ["change"] = change,
                    ["income_estimate"] = income,
                });
            }

            var data = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["source"] = sourceArg,
                ["market_shutdown"] = shutdown,
                ["coins_before"] = coinsBefore,
                ["plorts"] = plan,
                ["plort_count"] = plan.Count,
                ["total_income_estimate"] = totalIncome,
                ["note"] = shutdown ? "市场已休市，执行会被游戏拒绝" : "收入为单价×数量估算，实际到账以执行结果为准",
            };
            if (preview) return data;
            if (shutdown) return data;

            var sold = new List<object>();
            var failures = new List<object>();
            foreach (var row in plan)
            {
                int idx = (int)row["index"];
                int sellCount = (int)row["sell"];
                var entry = entries[idx];
                try
                {
                    object id = Try(entry.Slot, "Id");
                    object ok = sellMethod.Invoke(director, new object[] { id, sellCount, false, true, null });
                    if (!BoolOf(ok))
                        throw new InvalidOperationException("TrySell 返回 false");
                    int before = IntOf(Try(entry.Slot, "Count"));
                    int target = Math.Max(0, before - sellCount);
                    string decrementPath = entry.FromBag ? "bag_count_setter" : "storage_count_setter";
                    try
                    {
                        entry.Slot.Count = target;
                        if (target == 0) entry.Slot.Clear();
                    }
                    catch
                    {
                        decrementPath = "decrement_loop";
                        var decrement = entry.Slot.GetType().GetMethod("DecrementAmmo", Type.EmptyTypes);
                        for (int i = 0; i < sellCount && decrement != null; i++) decrement.Invoke(entry.Slot, null);
                    }
                    sold.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["source"] = row["source"],
                        ["container"] = row["container"],
                        ["slot"] = row["slot"],
                        ["id"] = row["id"],
                        ["name"] = row["name"],
                        ["sold"] = sellCount,
                        ["unit_price"] = row["unit_price"],
                        ["income_estimate"] = row["income_estimate"],
                        ["source_count_after"] = IntOf(Try(entry.Slot, "Count")),
                        ["decrement_path"] = decrementPath,
                    });
                }
                catch (Exception e)
                {
                    failures.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["source"] = row["source"],
                        ["container"] = row["container"],
                        ["slot"] = row["slot"],
                        ["id"] = row["id"],
                        ["error"] = (e is TargetInvocationException tie ? tie.GetBaseException() : e).Message,
                    });
                }
            }

            object coinsAfter = ReadCoins(playerState);
            data["sold"] = sold;
            data["sold_count"] = sold.Count;
            data["failures"] = failures;
            data["coins_after"] = coinsAfter;
            data["coins_delta"] = coinsBefore != null && coinsAfter != null
                ? Convert.ToInt32(coinsAfter) - Convert.ToInt32(coinsBefore) : (object)null;

            // 回读两边还剩哪些结晶
            var remainingBag = new List<object>();
            foreach (var slot in VacpackSlots(out _))
            {
                object id = Try(slot, "Id");
                int count = IntOf(Try(slot, "Count"));
                if (id == null || count <= 0 || !BoolOf(Try(id, "IsPlort"))) continue;
                remainingBag.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["id"] = Try(id, "ReferenceId") as string ?? Try(id, "name") as string,
                    ["name"] = Try(id, "name") as string,
                    ["count"] = count,
                });
            }
            data["vacpack_plorts_after"] = remainingBag;
            if (wantStorage && silos != null)
            {
                var remainingStorage = new List<object>();
                foreach (var silo in silos)
                {
                    if (silo?.Live == null) continue;
                    for (int i = 0; i < silo.Live.Length; i++)
                    {
                        var slot = silo.Live[i];
                        object id = Try(slot, "Id");
                        int count = IntOf(Try(slot, "Count"));
                        if (id == null || count <= 0 || !BoolOf(Try(id, "IsPlort"))) continue;
                        remainingStorage.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                        {
                            ["container"] = silo.Key ?? silo.ObjectName,
                            ["loaded"] = silo.Loaded,
                            ["slot"] = i,
                            ["id"] = Try(id, "ReferenceId") as string ?? Try(id, "name") as string,
                            ["name"] = Try(id, "name") as string,
                            ["count"] = count,
                        });
                    }
                }
                data["storage_plorts_after"] = remainingStorage;
            }
            return data;
        }
    }
}
