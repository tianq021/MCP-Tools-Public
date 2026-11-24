// PediaTools.cs —— 补全史莱姆百科（成就「专业范儿」）与发放物品（测试/Rush 用）
//
//   sr_pedia_unlock   解锁指定 pedia 条目；不传 pedia_id 时解锁「所有未解锁的非隐藏条目」
//   sr_give_item      把指定物品直接加进真空背包（走原生 Ammo.MaybeAddToSpecificSlot）
//
// 判定依据（源码实证）：
//   PediaDirector.OnUnlockedChanged()：entries 全部解锁（HIDDEN_ENTRIES 的 3 条不算）
//     → achieveDir.MaybeUpdateMaxStat(IntStat.COMPLETED_SLIMEPEDIA, 1) → 成就「专业范儿」
//   所以 sr_pedia_unlock 调用的是游戏自己的 PediaDirector.Unlock()，会正常触发成就统计。
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterPediaTools(BridgeServer server)
        {
            Action(server, "sr_pedia_unlock", PediaUnlock);
            Action(server, "sr_give_item", GiveItem);
        }

        static Dictionary<string, object> PediaUnlock(SceneContext scene, Dictionary<string, object> args)
        {
            PediaDirector pedia = scene.PediaDirector;
            if (pedia == null)
                return Fail("NOT_READY", "PediaDirector is not available.");

            string idsArg = Arg(args, "pedia_id");
            var targets = new List<PediaDirector.Id>();
            if (!string.IsNullOrEmpty(idsArg))
            {
                string[] parts = idsArg.Split(new char[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries);
                foreach (string raw in parts)
                {
                    string name = raw.Trim().ToUpperInvariant();
                    PediaDirector.Id parsed;
                    try
                    {
                        parsed = (PediaDirector.Id)Enum.Parse(typeof(PediaDirector.Id), name);
                    }
                    catch (Exception)
                    {
                        return Fail("UNKNOWN_PEDIA", "Unknown pedia_id: " + raw.Trim());
                    }
                    targets.Add(parsed);
                }
            }
            else
            {
                foreach (PediaDirector.IdEntry entry in pedia.entries)
                {
                    if (PediaDirector.HIDDEN_ENTRIES.Contains(entry.id)) continue;
                    if (!pedia.IsUnlocked(entry.id)) targets.Add(entry.id);
                }
            }

            var already = new List<object>();
            var pending = new List<PediaDirector.Id>();
            foreach (PediaDirector.Id id in targets)
            {
                if (pedia.IsUnlocked(id)) already.Add(id.ToString());
                else pending.Add(id);
            }
            var pendingNames = new List<object>();
            foreach (PediaDirector.Id id in pending) pendingNames.Add(id.ToString());

            var result = Row(
                "requested", targets.Select(x => (object)x.ToString()).ToList(),
                "already_unlocked", already,
                "pending", pendingNames,
                "unlocked_before", pedia.GetUnlockedCount(),
                "entries_total", pedia.entries.Length);

            if (Preview(args)) return result;
            if (pending.Count == 0)
            {
                result["message"] = "nothing to unlock";
                result["completed_slimepedia_stat"] = CompletedPediaStat(scene);
                return result;
            }

            pedia.Unlock(pending.ToArray());

            var missing = new List<object>();
            foreach (PediaDirector.IdEntry entry in pedia.entries)
            {
                if (PediaDirector.HIDDEN_ENTRIES.Contains(entry.id)) continue;
                if (!pedia.IsUnlocked(entry.id)) missing.Add(entry.id.ToString());
            }
            result["unlocked_after"] = pedia.GetUnlockedCount();
            result["missing_after"] = missing;
            result["missing_after_count"] = missing.Count;
            result["completed_slimepedia_stat"] = CompletedPediaStat(scene);
            return result;
        }

        static object CompletedPediaStat(SceneContext scene)
        {
            try
            {
                return scene.AchievementsDirector.GetStat(AchievementsDirector.IntStat.COMPLETED_SLIMEPEDIA);
            }
            catch (Exception e)
            {
                return "read_error: " + e.GetType().Name;
            }
        }

        static Dictionary<string, object> GiveItem(SceneContext scene, Dictionary<string, object> args)
        {
            Identifiable.Id id;
            if (!ParseItem(Arg(args, "item_id"), out id))
                return Fail("INVALID_ITEM", "Use a concrete item_id from sr_item_catalog.");

            int count = Number(args, "count", 1);
            if (count < 1 || count > 999)
                return Fail("INVALID_COUNT", "count must be between 1 and 999.");

            var result = Row("item_id", id.ToString(), "count", count);
            if (Preview(args)) return result;

            if (scene.PlayerState.GetAmmoMode() != PlayerState.AmmoMode.DEFAULT)
                return Fail("NOT_READY", "Normal Vacpack mode is required (exit Nimble Valley / other ammo modes).");

            Ammo ammo = scene.PlayerState.Ammo;
            int usable = ammo.GetUsableSlotCount();
            int remaining = count;
            int added = 0;
            for (int i = 0; i < usable && remaining > 0; i++)
            {
                Identifiable.Id slotId = ammo.GetSlotName(i);
                if (slotId != Identifiable.Id.NONE && slotId != id) continue;
                int max = ammo.GetSlotMaxCount(id, i);
                int current = slotId == id ? ammo.GetSlotCount(i) : 0;
                int room = max - current;
                if (room <= 0) continue;
                int take = Math.Min(room, remaining);
                if (ammo.MaybeAddToSpecificSlot(id, null, i, take))
                {
                    added += take;
                    remaining -= take;
                }
            }
            result["added"] = added;
            result["remaining"] = remaining;
            result["slots_after"] = SlotList(ammo, usable);
            return result;
        }

        static List<object> SlotList(Ammo ammo, int usable)
        {
            var list = new List<object>();
            for (int i = 0; i < usable; i++)
            {
                list.Add(Row("slot", i, "item_id", ammo.GetSlotName(i).ToString(), "count", ammo.GetSlotCount(i)));
            }
            return list;
        }
    }
}
