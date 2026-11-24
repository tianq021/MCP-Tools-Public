using System;
using System.Collections.Generic;
using Il2Cpp;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsRescue
    {
        // Main-thread only. Marks belong to the running bridge and never enter the save file.
        static readonly Dictionary<string, (string Group, Vector3 Point)> marks = new(StringComparer.Ordinal);

        static Row Blocked(string reason) => new() { ["applied"] = false, ["reason"] = reason };

        internal static Row Restore(SceneContext scene, Row args, bool preview, bool health)
        {
            if (args.GetValueOrDefault("points", 0L) is not long points || points < 0 || points > int.MaxValue)
                return Blocked("invalid_points");
            var player = scene.PlayerState;
            if (scene.Player == null || player?._model == null) return Blocked("player_unavailable");
            if (player.GetCurrHealth() <= 0) return Blocked("player_knocked_out");
            int before = health ? player.GetCurrHealth() : player.GetCurrEnergy();
            int maximum = health ? player.GetMaxHealth() : player.GetMaxEnergy();
            if (maximum <= 0 || before < 0) return Blocked("resource_unavailable");
            int target = points == 0 ? maximum : (int)Math.Min(maximum, before + points);
            var result = new Row { ["before"] = before, ["maximum"] = maximum, ["target"] = Math.Max(before, target),
                ["already_full"] = before >= maximum, ["applied"] = false };
            if (preview || before >= maximum) return result;
            if (health) player.SetHealth(target);
            else player.SetEnergy(target);
            int after = health ? player.GetCurrHealth() : player.GetCurrEnergy();
            result["after"] = after;
            result["delta"] = after - before;
            result["applied"] = after == target;
            if (after != target) result["reason"] = "recovery_unconfirmed";
            return result;
        }

        internal static Row SelectSlot(SceneContext scene, Row args, bool preview)
        {
            var ammo = scene.PlayerState?.Ammo;
            var slots = ammo?.Slots;
            if (slots == null) return Blocked("vacpack_unavailable");
            if (args.GetValueOrDefault("index") is not long index || index < 0 || index >= slots.Length)
                return Blocked("invalid_slot_index");
            var slot = slots[(int)index];
            if (slot == null || !slot.IsUnlocked) return Blocked("slot_unavailable");
            int before = ammo._selectedAmmoIdx;
            var result = new Row { ["before"] = before, ["index"] = index, ["item_id"] = slot.Id?.ReferenceId,
                ["count"] = slot.Count, ["already_selected"] = before == index, ["applied"] = false };
            if (preview || before == index) return result;
            bool accepted = ammo.SetSelectedSlot((int)index);
            int after = ammo._selectedAmmoIdx;
            result["after"] = after;
            result["applied"] = accepted && after == index;
            if (!accepted || after != index) result["reason"] = "slot_selection_unconfirmed";
            return result;
        }

        static (string Save, string Group) Location() => (
            GameContext.Instance?.AutoSaveDirector?.CurrentSaveGameName(),
            SystemContext.Instance?.SceneLoader?.CurrentSceneGroup?.ReferenceId);

        static object Coordinates(Vector3 p) => new { x = p.x, y = p.y, z = p.z };

        internal static Row Mark(SceneContext scene, Row args, bool preview)
        {
            var (save, group) = Location();
            if (string.IsNullOrWhiteSpace(save) || string.IsNullOrWhiteSpace(group)) return Blocked("location_unavailable");
            if (scene.Player == null) return Blocked("player_unavailable");
            var point = scene.Player.transform.position;
            if (!float.IsFinite(point.x) || !float.IsFinite(point.y) || !float.IsFinite(point.z))
                return Blocked("invalid_player_position");
            var probe = ToolsLanding.Probe(point);
            bool suitable = probe.GetValueOrDefault("suitable") is true;
            bool hasPrevious = marks.TryGetValue(save, out var previous);
            var result = new Row { ["save"] = save, ["scene_group"] = group, ["position"] = Coordinates(point),
                ["previous_mark"] = hasPrevious ? new { scene_group = previous.Group, position = Coordinates(previous.Point) } : null,
                ["probe"] = probe, ["can_mark"] = suitable, ["applied"] = false };
            if (!suitable) { result["reason"] = "target_not_landable"; return result; }
            if (preview) return result;
            marks[save] = (group, point);
            result["applied"] = true;
            return result;
        }

        internal static Row Return(SceneContext scene, Row args, bool preview)
        {
            var (save, group) = Location();
            if (string.IsNullOrWhiteSpace(save) || string.IsNullOrWhiteSpace(group)) return Blocked("location_unavailable");
            if (!marks.TryGetValue(save, out var mark)) return Blocked("no_return_point");
            if (mark.Group != group) return new Row { ["applied"] = false, ["reason"] = "different_scene_group",
                ["scene_group"] = group, ["marked_group"] = mark.Group, ["target"] = Coordinates(mark.Point) };
            var result = ToolsNavigation.TeleportTo(mark.Point.x, mark.Point.y, mark.Point.z, preview);
            result.Remove("hint"); // The coordinate tool's force override is not offered for a return.
            result["scene_group"] = group;
            result["save"] = save;
            bool suitable = result.GetValueOrDefault("probe") is Row probe && probe.GetValueOrDefault("suitable") is true;
            result["can_return"] = suitable;
            result["applied"] = result.GetValueOrDefault("reached") is true;
            if (!suitable && !result.ContainsKey("reason")) result["reason"] = "target_not_landable";
            return result;
        }
    }
}
