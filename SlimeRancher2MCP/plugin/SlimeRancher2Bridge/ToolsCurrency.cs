// ToolsCurrency —— 新币（以及其他货币）读取 / 增加
//
//   sr2_currency      读当前新币余额、累计获得、以及 CurrencyList 里的可用货币列表
//   sr2_currency_add  增加（或减少）新币；默认预演，执行需 dry_run=false + request_id
//
// 游戏 API（1.3.0 实测类型名）：
//   MonomiPark.SlimeRancher.Economy.CurrencyUtility.DefaultCurrency   ← 静态属性，直接给出 ICurrency 实例
//   PlayerState.GetCurrency(ICurrency) -> int                         ← 当前余额
//   PlayerState.Model (PlayerModel)
//     GetCurrencyAmount(ICurrency) / AddCurrency(ICurrency, int) -> int / SetCurrency(ICurrency, int)
//     GetCurrencyAmountEverCollected(ICurrency) -> int
//   LookupDirector.GetIfReady() -> LookupDirector → CurrencyList → Currencies（列出所有货币，尽力而为）
//
// 注意：sr2_currency_add 属于「作弊」类写操作：直接改账户余额。默认预演，执行必须带 request_id，
//       并在结果里回读执行前后余额。
using System;
using System.Collections.Generic;

namespace SlimeRancher2Bridge
{
    internal static class ToolsCurrency
    {
        // ---------------- 货币实例 ----------------

        static string CurrencyDiagnostic = "not_run";

        /// <summary>拿默认货币（新币）实例：三条路径依次尝试。</summary>
        static object DefaultCurrency(object playerState)
        {
            // ① CurrencyUtility.DefaultCurrency（静态属性；若静态字段还没赋值会返回 null）
            try
            {
                var type = Il2CppUtil.FindType("CurrencyUtility");
                if (type != null)
                {
                    var property = type.GetProperty("DefaultCurrency");
                    if (property != null)
                    {
                        var direct = property.GetValue(null);
                        if (direct != null) { CurrencyDiagnostic = "CurrencyUtility.DefaultCurrency"; return direct; }
                    }
                    var method = type.GetMethod("get_DefaultCurrency");
                    if (method != null)
                    {
                        var direct = method.Invoke(null, null);
                        if (direct != null) { CurrencyDiagnostic = "CurrencyUtility.get_DefaultCurrency"; return direct; }
                    }
                }
            }
            catch (Exception e)
            {
                CurrencyDiagnostic = "CurrencyUtility_failed:" + e.GetBaseException().Message;
            }

            // ② LookupDirector.GetIfReady() → CurrencyList.GetDefaultCurrency()
            try
            {
                var lookupType = Il2CppUtil.FindType("LookupDirector");
                var getIfReady = lookupType?.GetMethod("GetIfReady", Type.EmptyTypes);
                object lookup = getIfReady?.Invoke(null, null);
                object list = lookup == null ? null : Il2CppUtil.Try(lookup, "CurrencyList");
                var getDefault = list?.GetType().GetMethod("GetDefaultCurrency", Type.EmptyTypes);
                object currency = getDefault?.Invoke(list, null);
                if (currency != null) { CurrencyDiagnostic = "LookupDirector.CurrencyList.GetDefaultCurrency"; return currency; }
            }
            catch
            {
            }

            // ③ PlayerState._sessionCurrencyInfos 字典的 Key
            try
            {
                object infos = playerState == null ? null : Il2CppUtil.Try(playerState, "_sessionCurrencyInfos");
                foreach (var entry in Il2CppUtil.Enumerate(infos))
                {
                    object key = Il2CppUtil.Try(entry, "Key") ?? Il2CppUtil.Try(entry, "key");
                    if (key != null) { CurrencyDiagnostic = "sessionCurrencyInfos.Key"; return key; }
                    if (entry != null && entry.GetType().Name.Contains("Currency")) { CurrencyDiagnostic = "sessionCurrencyInfos.Entry"; return entry; }
                }
            }
            catch
            {
            }

            CurrencyDiagnostic = "none";
            return null;
        }

        static object CurrencyList()
        {
            try
            {
                var lookupType = Il2CppUtil.FindType("LookupDirector");
                if (lookupType == null) return null;
                var getIfReady = lookupType.GetMethod("GetIfReady", Type.EmptyTypes);
                object lookup = getIfReady != null ? getIfReady.Invoke(null, null) : null;
                return lookup == null ? null : Il2CppUtil.Try(lookup, "CurrencyList");
            }
            catch
            {
                return null;
            }
        }

        static string CurrencyId(object currency)
        {
            if (currency == null) return null;
            return Il2CppUtil.Try(currency, "ReferenceId") as string
                ?? Il2CppUtil.Try(currency, "name") as string
                ?? Il2CppUtil.Try(currency, "_identifier") as string;
        }

        static object PlayerState()
        {
            object state = null;
            ToolsNavigation.WithScene(scene =>
            {
                state = Il2CppUtil.Try(scene, "PlayerState");
                return new Dictionary<string, object>();
            });
            return state;
        }

        static object PlayerModelOf(object playerState)
        {
            return Il2CppUtil.Try(playerState, "Model")
                ?? Il2CppUtil.Try(playerState, "_model")
                ?? Il2CppUtil.Try(playerState, "PlayerModel");
        }

        static string AmountDiagnostic = "not_run";

        static int AmountOf(object playerState, object model, object currency)
        {
            AmountDiagnostic = "not_run";
            // ① PlayerState.GetCurrency(ICurrency)
            try
            {
                var method = playerState?.GetType().GetMethod("GetCurrency");
                if (method != null)
                {
                    string argType = method.GetParameters()[0].ParameterType.Name;
                    object value = Il2CppUtil.InvokeMethod(method, playerState, new[] { currency });
                    AmountDiagnostic = "GetCurrency(" + argType + ")->" + (value ?? "null");
                    if (value != null) return Il2CppUtil.IntOf(value);
                }
                else
                {
                    AmountDiagnostic = "GetCurrency_missing";
                }
            }
            catch (Exception e)
            {
                AmountDiagnostic = "GetCurrency_threw:" + e.GetBaseException().Message;
            }
            // ② PlayerModel.GetCurrencyAmount(ICurrency)
            try
            {
                var method = model?.GetType().GetMethod("GetCurrencyAmount");
                if (method != null)
                {
                    string argType = method.GetParameters()[0].ParameterType.Name;
                    object value = Il2CppUtil.InvokeMethod(method, model, new[] { currency });
                    AmountDiagnostic += " | GetCurrencyAmount(" + argType + ")->" + (value ?? "null");
                    if (value != null) return Il2CppUtil.IntOf(value);
                }
            }
            catch (Exception e)
            {
                AmountDiagnostic += " | GetCurrencyAmount_threw:" + e.GetBaseException().Message;
            }
            // ③ PlayerState._sessionCurrencyInfos[currency] 里的数值字段
            try
            {
                object infos = playerState == null ? null : Il2CppUtil.Try(playerState, "_sessionCurrencyInfos");
                string wantId = CurrencyId(currency);
                foreach (var entry in Il2CppUtil.Enumerate(infos))
                {
                    object key = Il2CppUtil.Try(entry, "Key") ?? Il2CppUtil.Try(entry, "key");
                    if (key == null) continue;
                    string keyId = CurrencyId(key);
                    if (keyId != null && wantId != null && keyId != wantId) continue;
                    object info = Il2CppUtil.Try(entry, "Value") ?? Il2CppUtil.Try(entry, "value");
                    if (info == null) continue;
                    foreach (string field in new[] { "Amount", "_amount", "Value", "_value", "DisplayedAmount", "_displayedAmount", "Count", "_count" })
                    {
                        object raw = Il2CppUtil.Try(info, field);
                        if (raw != null)
                        {
                            AmountDiagnostic += " | sessionInfo." + field + "=" + raw;
                            return Il2CppUtil.IntOf(raw);
                        }
                    }
                }
            }
            catch (Exception e)
            {
                AmountDiagnostic += " | sessionInfos_threw:" + e.GetBaseException().Message;
            }
            return -1;
        }

        internal static int? ReadDefaultAmount(object state)
        {
            object currency = DefaultCurrency(state);
            if (currency == null) return null;
            int amount = AmountOf(state, PlayerModelOf(state), currency);
            return amount >= 0 ? amount : null;
        }

        // ---------------- 读 ----------------

        internal static Dictionary<string, object> Read(Dictionary<string, object> args)
        {
            object state = PlayerState();
            if (state == null)
                return new() { ["available"] = false, ["reason"] = "player_unavailable" };
            object currency = DefaultCurrency(state);
            if (currency == null)
                return new() { ["available"] = false, ["reason"] = "default_currency_unavailable" };
            object model = PlayerModelOf(state);

            int amount = AmountOf(state, model, currency);
            object everCollected = model != null
                ? Il2CppUtil.Invoke(model, "GetCurrencyAmountEverCollected", new[] { currency })
                : null;

            var currencies = new List<object>();
            var list = CurrencyList();
            if (list != null)
            {
                foreach (var item in Il2CppUtil.Enumerate(Il2CppUtil.Try(list, "Currencies")))
                {
                    if (item == null) continue;
                    int value = AmountOf(state, model, item);
                    currencies.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["id"] = CurrencyId(item),
                        ["amount"] = value,
                    });
                }
            }
            if (currencies.Count == 0)
                currencies.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["id"] = CurrencyId(currency),
                    ["amount"] = amount,
                });

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["currency_id"] = CurrencyId(currency),
                ["amount"] = amount,
                ["ever_collected"] = everCollected,
                ["currencies"] = currencies,
                ["debug"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["currency_type"] = currency.GetType().FullName,
                    ["player_state_type"] = state.GetType().FullName,
                    ["player_model_found"] = model != null,
                    ["currency_list_found"] = list != null,
                    ["amount_read"] = AmountDiagnostic,
                    ["last_invoke_error"] = Il2CppUtil.LastInvokeError,
                },
            };
        }

        // ---------------- 增加（作弊类写操作）----------------

        internal static Dictionary<string, object> Add(Dictionary<string, object> args)
        {
            bool preview = true;
            if (args.TryGetValue("dry_run", out var mode) && mode is bool flag) preview = flag;

            string amountArg = args.TryGetValue("amount", out var a) && a != null ? Convert.ToString(a) : "";
            if (!int.TryParse(amountArg, out int amount) || amount == 0)
                return new() { ["available"] = false, ["reason"] = "bad_amount", ["hint"] = "amount 必须是非零整数（正数加、负数减）" };

            object state = PlayerState();
            if (state == null)
                return new() { ["available"] = false, ["reason"] = "player_unavailable" };
            object currency = DefaultCurrency(state);
            if (currency == null)
                return new() { ["available"] = false, ["reason"] = "default_currency_unavailable" };
            object model = PlayerModelOf(state);
            if (model == null)
                return new() { ["available"] = false, ["reason"] = "player_model_unavailable" };

            int before = AmountOf(state, model, currency);
            var data = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["currency_id"] = CurrencyId(currency),
                ["amount_before"] = before,
                ["requested_delta"] = amount,
                ["expected_after"] = before >= 0 ? Math.Max(0, before + amount) : (object)null,
                ["note"] = "作弊类操作：直接改账户余额；执行后回读实际余额",
            };
            if (preview) return data;

            object result = Il2CppUtil.Invoke(model, "AddCurrency", new[] { currency, (object)amount });
            int after = AmountOf(state, model, currency);
            data["addcurrency_result"] = result;
            data["amount_after"] = after;
            data["actual_delta"] = before >= 0 && after >= 0 ? after - before : (object)null;
            return data;
        }
    }
}






