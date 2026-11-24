// ToolsInventory —— 背包（vacpack / ammo slots）读写工具
//
//   sr2_vacpack        读玩家背包每个槽位：编号、物品 id/名称、数量、上限、是否选中、是否解锁
//   sr2_vacpack_clear  清空背包（默认预演；执行需 dry_run=false + request_id）
//
// 游戏 API（1.3.0 实测类型名）：
//   SceneContext.PlayerState  → PlayerState.Ammo : AmmoSlotManager
//   AmmoSlotManager.Slots : Il2CppReferenceArray<AmmoSlot>
//   AmmoSlot.Id(IdentifiableType，可写) / Count(int，可写) / MaxCount / IsUnlocked / Clear()
//
// 说明：Clear() 是游戏自己的方法（会走原生事件，UI 会同步），比手改 Count 安全；
//       但它属于销毁性操作 —— 物品直接消失、不会掉在地上，所以必须预演 + 显式确认。
using System;
using System.Collections.Generic;
using System.Reflection;

namespace SlimeRancher2Bridge
{
    internal static class ToolsInventory
    {
        // Il2CppInterop 对带引用的字段/属性暴露方式不固定，属性优先、字段兜底
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

        static object TryMember(object instance, string name)
        {
            try { return Member(instance, name); }
            catch { return null; }
        }

        static string IdOf(object identifiableType)
        {
            if (identifiableType == null) return null;
            try
            {
                string referenceId = TryMember(identifiableType, "ReferenceId") as string;
                if (!string.IsNullOrEmpty(referenceId)) return referenceId;
            }
            catch
            {
            }
            return TryMember(identifiableType, "name") as string;
        }

        static string NameOf(object identifiableType)
        {
            if (identifiableType == null) return null;
            return TryMember(identifiableType, "name") as string;
        }

        /// <summary>把 AmmoSlotManager.Slots 展开成 List&lt;AmmoSlot&gt;（不依赖泛型类型）。</summary>
        static List<object> Slots(object ammo)
        {
            var result = new List<object>();
            object slots = TryMember(ammo, "Slots");
            if (slots == null) return result;
            Type type = slots.GetType();
            var lengthProp = type.GetProperty("Length") ?? type.GetProperty("Count");
            var itemProp = type.GetProperty("Item");
            if (lengthProp == null || itemProp == null) return result;
            int length = Convert.ToInt32(lengthProp.GetValue(slots));
            for (int i = 0; i < length; i++)
            {
                object slot = itemProp.GetValue(slots, new object[] { i });
                result.Add(slot);
            }
            return result;
        }

        static Dictionary<string, object> Snapshot()
        {
            return ToolsNavigation.WithScene(scene =>
            {
                object playerState = TryMember(scene, "PlayerState");
                if (playerState == null) return new() { ["available"] = false, ["reason"] = "player_unavailable" };
                object ammo = TryMember(playerState, "Ammo");
                if (ammo == null) return new() { ["available"] = false, ["reason"] = "ammo_unavailable" };

                int selected = -1;
                object selectedIdx = TryMember(ammo, "_selectedAmmoIdx");
                if (selectedIdx != null) selected = Convert.ToInt32(selectedIdx);

                var rows = new List<object>();
                var slots = Slots(ammo);
                for (int i = 0; i < slots.Count; i++)
                {
                    object slot = slots[i];
                    if (slot == null) continue;
                    object id = TryMember(slot, "Id");
                    object countObj = TryMember(slot, "Count");
                    int count = countObj == null ? 0 : Convert.ToInt32(countObj);
                    object maxObj = TryMember(slot, "MaxCount");
                    int max = maxObj == null ? 0 : Convert.ToInt32(maxObj);
                    object unlockedObj = TryMember(slot, "IsUnlocked");
                    rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["index"] = i,
                        ["id"] = IdOf(id),
                        ["name"] = NameOf(id),
                        ["count"] = count,
                        ["max"] = max,
                        ["selected"] = i == selected,
                        ["unlocked"] = unlockedObj is bool b ? b : true,
                    });
                }
                return new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["available"] = true,
                    ["slot_count"] = slots.Count,
                    ["selected_index"] = selected,
                    ["slots"] = rows,
                };
            });
        }

        internal static Dictionary<string, object> Read(Dictionary<string, object> args) => Snapshot();

        internal static Dictionary<string, object> Clear(Dictionary<string, object> args)
        {
            bool preview = true;
            if (args.TryGetValue("dry_run", out var mode) && mode is bool flag) preview = flag;

            string onlySlot = args.TryGetValue("slot", out var slotArg) && slotArg != null ? Convert.ToString(slotArg) : "";
            int onlyIndex = -1;
            if (onlySlot.Length > 0 && !int.TryParse(onlySlot, out onlyIndex))
                return new() { ["available"] = false, ["reason"] = "bad_slot", ["hint"] = "slot 必须是槽位编号（从 0 开始）" };

            return ToolsNavigation.WithScene(scene =>
            {
                object playerState = TryMember(scene, "PlayerState");
                if (playerState == null) return new() { ["available"] = false, ["reason"] = "player_unavailable" };
                object ammo = TryMember(playerState, "Ammo");
                if (ammo == null) return new() { ["available"] = false, ["reason"] = "ammo_unavailable" };

                var slots = Slots(ammo);
                var targets = new List<object>();
                var previewRows = new List<object>();
                for (int i = 0; i < slots.Count; i++)
                {
                    if (onlyIndex >= 0 && i != onlyIndex) continue;
                    object slot = slots[i];
                    object countObj = TryMember(slot, "Count");
                    int count = countObj == null ? 0 : Convert.ToInt32(countObj);
                    if (count <= 0) continue;
                    object id = TryMember(slot, "Id");
                    targets.Add(slot);
                    previewRows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["index"] = i,
                        ["id"] = IdOf(id),
                        ["name"] = NameOf(id),
                        ["count"] = count,
                    });
                }

                var data = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["available"] = true,
                    ["dry_run"] = preview,
                    ["slot_count"] = slots.Count,
                    ["clear_count"] = targets.Count,
                    ["cleared"] = previewRows,
                    ["note"] = "Clear() 是销毁性操作：物品直接消失，不会掉在地上",
                };
                if (preview) return data;

                var done = new List<object>();
                var failures = new List<object>();
                for (int i = 0; i < targets.Count; i++)
                {
                    object slot = targets[i];
                    try
                    {
                        var clear = slot.GetType().GetMethod("Clear", Type.EmptyTypes);
                        if (clear == null) throw new MissingMethodException(slot.GetType().FullName, "Clear");
                        clear.Invoke(slot, null);
                        done.Add(previewRows[i]);
                    }
                    catch (Exception e)
                    {
                        failures.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                        {
                            ["index"] = (previewRows[i] as Dictionary<string, object>)["index"],
                            ["error"] = (e is TargetInvocationException tie ? tie.GetBaseException() : e).Message,
                        });
                    }
                }
                data["cleared"] = done;
                data["cleared_count"] = done.Count;
                data["failures"] = failures;
                data["after"] = Snapshot();
                return data;
            });
        }
    }
}
