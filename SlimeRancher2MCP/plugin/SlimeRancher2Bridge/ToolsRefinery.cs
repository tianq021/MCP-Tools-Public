// ToolsRefinery —— 精炼机 / 精炼炉（refinery）读取与「背包→精炼炉」存入
//
//   sr2_refinery  读精炼炉储量：每种可精炼物品的现有数量 / 上限 / 剩余空间（全局共享）
//   sr2_refine    把背包里的物品直接存进精炼炉；默认预演，执行需 dry_run=false + request_id
//
// 游戏 API（1.3.0 实测类型名）：
//   SceneContext.GadgetDirector
//     REFINERY_MAX                         每种可精炼物品的上限
//     RefineryTypeGroup                    IdentifiableTypeGroup（GetAllMembers() 列出可精炼种类）
//     GetRefinerySpaceAvailable(id)        剩余可存数量
//     HasRefinerySpaceAvailable(id, count)
//   AcquisitionUtility.AcquireRefineryResource(IdentifiableType id, int count) -> int  ← 实际存入数量
//
// 关键差异：精炼炉储量是**全局的**（不是某个建筑里的槽位），所以工具存它完全不涉及位置/距离；
// 而不是可精炼种类的物品会被 RefineryTypeGroup.IsMember 过滤掉，不会硬塞。
using System;
using System.Collections;
using System.Collections.Generic;

namespace SlimeRancher2Bridge
{
    internal static class ToolsRefinery
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

        static int IntOf(object value) => value == null ? 0 : Convert.ToInt32(value);

        static string IdOf(object identifiableType)
        {
            if (identifiableType == null) return null;
            var referenceId = Try(identifiableType, "ReferenceId") as string;
            return string.IsNullOrEmpty(referenceId) ? Try(identifiableType, "name") as string : referenceId;
        }

        static object Invoke(object target, string method, object[] args = null)
        {
            try
            {
                var info = target.GetType().GetMethod(method, args == null ? Type.EmptyTypes
                    : Array.ConvertAll(args, a => a?.GetType() ?? typeof(object)));
                if (info == null) return null;
                return info.Invoke(target, args);
            }
            catch
            {
                return null;
            }
        }

        // ---------------- 共享上下文 ----------------

        sealed class RefineryContext
        {
            public object GadgetDirector;
            public object TypeGroup;
            public int Max;
            public object PlayerState;
            public List<object> VacpackSlots;
        }

        static RefineryContext LoadContext(out Dictionary<string, object> error)
        {
            var context = new RefineryContext();
            Dictionary<string, object> err = null;
            ToolsNavigation.WithScene(scene =>
            {
                context.GadgetDirector = Try(scene, "GadgetDirector");
                if (context.GadgetDirector == null)
                {
                    err = new() { ["available"] = false, ["reason"] = "gadget_director_unavailable" };
                    return new Dictionary<string, object>();
                }
                context.TypeGroup = Try(context.GadgetDirector, "RefineryTypeGroup");
                context.Max = IntOf(Try(context.GadgetDirector, "REFINERY_MAX"));
                context.PlayerState = Try(scene, "PlayerState");
                if (context.PlayerState != null)
                {
                    var ammo = Try(context.PlayerState, "Ammo");
                    context.VacpackSlots = Enumerate(Try(ammo, "Slots"));
                }
                else
                {
                    context.VacpackSlots = new List<object>();
                }
                return new Dictionary<string, object>();
            });
            error = err;
            return context;
        }

        static bool IsRefineryType(object typeGroup, object id)
        {
            if (typeGroup == null || id == null) return false;
            try
            {
                var method = typeGroup.GetType().GetMethod("IsMember");
                if (method == null) return true;
                var ok = method.Invoke(typeGroup, new[] { id });
                return ok is bool flag && flag;
            }
            catch
            {
                return true;
            }
        }

        static int SpaceAvailable(object gadgetDirector, object id)
        {
            try
            {
                var method = gadgetDirector.GetType().GetMethod("GetRefinerySpaceAvailable");
                if (method == null) return 0;
                return IntOf(method.Invoke(gadgetDirector, new[] { id }));
            }
            catch
            {
                return 0;
            }
        }

        static string TypeGroupDiagnostic = "not_run";

        static List<object> AllRefineryTypes(object typeGroup)
        {
            if (typeGroup == null)
            {
                TypeGroupDiagnostic = "typegroup_null";
                return new List<object>();
            }
            var result = new List<object>();
            try
            {
                var method = typeGroup.GetType().GetMethod("GetAllMembers", Type.EmptyTypes);
                if (method != null)
                {
                    object raw = method.Invoke(typeGroup, null);
                    result = Il2CppUtil.Enumerate(raw, out string strategy);
                    TypeGroupDiagnostic = "GetAllMembers raw=" + Il2CppUtil.Describe(raw)["type"] + " strategy=" + strategy + " count=" + result.Count;
                }
                else
                {
                    TypeGroupDiagnostic = "GetAllMembers_missing";
                }
            }
            catch (Exception e)
            {
                TypeGroupDiagnostic = "GetAllMembers_failed:" + e.GetBaseException().Message;
            }
            if (result.Count > 0) return result;

            // 兜底：直接读 ImmediateMemberTypes，再递归 MemberGroups
            try
            {
                object immediate = Try(typeGroup, "ImmediateMemberTypes");
                result = Il2CppUtil.Enumerate(immediate, out string s2);
                TypeGroupDiagnostic += " | ImmediateMemberTypes strategy=" + s2 + " count=" + result.Count;
            }
            catch
            {
            }
            if (result.Count > 0) return result;
            try
            {
                foreach (var group in Il2CppUtil.Enumerate(Try(typeGroup, "MemberGroups")))
                    result.AddRange(AllRefineryTypes(group));
            }
            catch
            {
            }
            return result;
        }

        // ---------------- 读精炼炉 ----------------

        internal static Dictionary<string, object> Read(Dictionary<string, object> args)
        {
            var context = LoadContext(out var error);
            if (error != null) return error;
            if (context.TypeGroup == null)
                return new() { ["available"] = false, ["reason"] = "refinery_typegroup_unavailable" };

            var rows = new List<object>();
            foreach (var type in AllRefineryTypes(context.TypeGroup))
            {
                int space = SpaceAvailable(context.GadgetDirector, type);
                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["id"] = IdOf(type),
                    ["name"] = Try(type, "name") as string,
                    ["count"] = context.Max > 0 ? Math.Max(0, context.Max - space) : (object)null,
                    ["space"] = space,
                });
            }
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["refinery_max"] = context.Max,
                ["type_count"] = rows.Count,
                ["items"] = rows,
                ["note"] = "精炼炉储量是全局共享的；count = REFINERY_MAX - 剩余空间",
                ["debug"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["typegroup"] = TypeGroupDiagnostic,
                    ["typegroup_type"] = context.TypeGroup?.GetType().FullName,
                },
            };
        }

        // ---------------- 背包 → 精炼炉 ----------------

        internal static Dictionary<string, object> Deposit(Dictionary<string, object> args)
        {
            bool preview = true;
            if (args.TryGetValue("dry_run", out var mode) && mode is bool flag) preview = flag;
            string itemArg = args.TryGetValue("item", out var it) && it != null ? Convert.ToString(it) : "";
            string countArg = args.TryGetValue("count", out var ct) && ct != null ? Convert.ToString(ct) : "";
            int countLimit = -1;
            if (countArg.Length > 0 && (!int.TryParse(countArg, out countLimit) || countLimit <= 0))
                return new() { ["available"] = false, ["reason"] = "bad_count", ["hint"] = "count 必须是正整数，留空=全存" };

            var context = LoadContext(out var error);
            if (error != null) return error;

            var plan = new List<Dictionary<string, object>>();
            var skipped = new List<object>();
            var reserved = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int idx = 0; idx < context.VacpackSlots.Count; idx++)
            {
                object slot = context.VacpackSlots[idx];
                object id = Try(slot, "Id");
                int count = IntOf(Try(slot, "Count"));
                if (id == null || count <= 0) continue;
                string idText = IdOf(id);
                if (string.IsNullOrEmpty(idText)) continue;
                string name = Try(id, "name") as string;
                if (itemArg.Length > 0
                    && !string.Equals(itemArg, idText, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(itemArg, idx.ToString(), StringComparison.Ordinal)
                    && !string.Equals(itemArg, name, StringComparison.OrdinalIgnoreCase))
                    continue;
                if (!IsRefineryType(context.TypeGroup, id))
                {
                    skipped.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["slot"] = idx, ["id"] = idText, ["count"] = count, ["reason"] = "not_refinable",
                    });
                    continue;
                }
                int alreadyPlanned = reserved.GetValueOrDefault(idText);
                int space = Math.Max(0, SpaceAvailable(context.GadgetDirector, id) - alreadyPlanned);
                int move = countLimit > 0 ? Math.Min(Math.Min(countLimit, count), space) : Math.Min(count, space);
                if (move <= 0)
                {
                    skipped.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["slot"] = idx, ["id"] = idText, ["count"] = count, ["space"] = space, ["reason"] = "refinery_full",
                    });
                    continue;
                }
                reserved[idText] = alreadyPlanned + move;
                plan.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["slot"] = idx,
                    ["id"] = idText,
                    ["name"] = name,
                    ["count"] = count,
                    ["space_before"] = space,
                    ["deposit"] = move,
                });
            }

            var data = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["refinery_max"] = context.Max,
                ["plan"] = plan,
                ["plan_count"] = plan.Count,
                ["skipped"] = skipped,
                ["note"] = "精炼炉全局共享，不涉及位置；只有 RefineryTypeGroup 成员（结晶/材料类）能存",
            };
            if (preview) return data;

            var done = new List<object>();
            var failures = new List<object>();
            var acquireType = FindType("Il2CppMonomiPark.SlimeRancher.Economy.AcquisitionUtility")
                ?? FindType("MonomiPark.SlimeRancher.Economy.AcquisitionUtility");
            var acquire = acquireType?.GetMethod("AcquireRefineryResource");

            foreach (var row in plan)
            {
                int idx = (int)row["slot"];
                int want = (int)row["deposit"];
                try
                {
                    object slot = context.VacpackSlots[idx];
                    object id = Try(slot, "Id");
                    if (acquire == null) throw new MissingMethodException("AcquisitionUtility", "AcquireRefineryResource");
                    int spaceBefore = SpaceAvailable(context.GadgetDirector, id);
                    object addedObj = acquire.Invoke(null, new[] { id, (object)want });
                    int added = IntOf(addedObj);
                    int spaceAfter = SpaceAvailable(context.GadgetDirector, id);
                    int spaceDelta = spaceBefore - spaceAfter;
                    if (added <= 0 && spaceDelta > 0) added = spaceDelta;
                    if (added <= 0)
                        throw new InvalidOperationException("精炼炉没有接收（返回 " + added + "）");
                    // 扣背包
                    int before = IntOf(Try(slot, "Count"));
                    int target = Math.Max(0, before - added);
                    string decrementPath = "count_setter";
                    try
                    {
                        var countProp = slot.GetType().GetProperty("Count");
                        if (countProp == null || !countProp.CanWrite) throw new MissingMemberException("AmmoSlot", "Count");
                        countProp.SetValue(slot, target);
                    }
                    catch
                    {
                        decrementPath = "decrement_loop";
                        var decrement = slot.GetType().GetMethod("DecrementAmmo", Type.EmptyTypes);
                        for (int i = 0; i < added && decrement != null; i++) decrement.Invoke(slot, null);
                    }
                    done.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["id"] = row["id"],
                        ["name"] = row["name"],
                        ["deposited"] = added,
                        ["space_before"] = spaceBefore,
                        ["space_after"] = spaceAfter,
                        ["source_count_after"] = IntOf(Try(slot, "Count")),
                        ["decrement_path"] = decrementPath,
                    });
                }
                catch (Exception e)
                {
                    failures.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["slot"] = idx,
                        ["id"] = row["id"],
                        ["error"] = e.GetBaseException().Message,
                    });
                }
            }

            var after = Read(new Dictionary<string, object>());
            data["deposited"] = done;
            data["deposited_count"] = done.Count;
            data["failures"] = failures;
            data["vacpack_after"] = VacpackSummary(context.VacpackSlots);
            data["refinery_after"] = after;
            return data;
        }

        static List<object> VacpackSummary(List<object> slots)
        {
            var rows = new List<object>();
            if (slots == null) return rows;
            foreach (var slot in slots)
            {
                object id = Try(slot, "Id");
                int count = IntOf(Try(slot, "Count"));
                if (id == null || count <= 0) continue;
                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["id"] = IdOf(id),
                    ["name"] = Try(id, "name") as string,
                    ["count"] = count,
                });
            }
            return rows;
        }
    }
}
