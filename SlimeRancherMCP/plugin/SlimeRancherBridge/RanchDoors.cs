// RanchDoors.cs —— 购买扩展区（AccessDoor 锁门）
//
//   sr_buy_expansion   预演/购买一扇未解锁的扩展区门（如 LAB / WILDS / RUINS）
//
// 依据：AccessDoorUI.UnlockDoor 的原生逻辑 —— 货币足够时 SpendCurrency(cost)、
// door.CurrState = OPEN、linkedDoors 里的 LOCKED 门置为 CLOSED，然后存档。
using System;
using System.Collections.Generic;
using System.Linq;
using MonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterExpansionDoors(BridgeServer server)
        {
            Action(server, "sr_buy_expansion", BuyExpansion);
        }

        static Dictionary<string, object> BuyExpansion(SceneContext scene, Dictionary<string, object> args)
        {
            string doorId = Arg(args, "door_id");
            AccessDoor door = null;
            foreach (var candidate in Object.FindObjectsOfType<AccessDoor>())
            {
                if (candidate != null && candidate.isActiveAndEnabled && Same(candidate.id, doorId))
                {
                    door = candidate;
                    break;
                }
            }
            if (door == null)
                return Fail("TARGET_UNAVAILABLE", "Use a loaded door_id from sr_doors (walk near the door first).");
            if (door.CurrState != AccessDoor.State.LOCKED)
                return Fail("NOT_LOCKED", "The door is already open/closed; only LOCKED expansion doors can be purchased.");
            if (door.doorPurchase == null)
                return Fail("INVALID_DOOR", "The door has no expansion purchase entry.");

            int cost = door.doorPurchase.cost;
            int currency = scene.PlayerState.GetCurrency();
            var result = Row(
                "door_id", door.id,
                "locked_region", door.lockedRegionId.ToString(),
                "state_before", door.CurrState.ToString(),
                "cost", cost,
                "currency_before", currency,
                "position", Position(door.transform.position),
                "player_distance", Distance(scene, door.transform));
            if (currency < cost)
                return Row("__error", BridgeServer.Error("INSUFFICIENT_FUNDS",
                    "Need " + cost + " newbucks but only " + currency + " available."));
            if (Preview(args)) return result;

            scene.PlayerState.SpendCurrency(cost);
            door.CurrState = AccessDoor.State.OPEN;
            if (door.linkedDoors != null)
            {
                foreach (var linked in door.linkedDoors)
                {
                    if (linked != null && linked.CurrState == AccessDoor.State.LOCKED)
                        linked.CurrState = AccessDoor.State.CLOSED;
                }
            }

            result["state_after"] = door.CurrState.ToString();
            result["currency_after"] = scene.PlayerState.GetCurrency();

            var autoSave = SRSingleton<GameContext>.Instance == null ? null : SRSingleton<GameContext>.Instance.AutoSaveDirector;
            if (autoSave != null)
            {
                try { autoSave.SaveAllNow(); result["autosaved"] = true; }
                catch (Exception) { result["autosaved"] = false; }
            }

            return result;
        }
    }
}
