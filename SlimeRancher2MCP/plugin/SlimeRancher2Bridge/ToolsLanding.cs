using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeRancher2Bridge
{
    internal static class ToolsLanding
    {
        internal static Dictionary<string, object> Check(Dictionary<string, object> args)
        {
            var target = new Vector3(ToolsNavigation.Axis(args, "x"),
                ToolsNavigation.Axis(args, "y"), ToolsNavigation.Axis(args, "z"));
            return Probe(target);
        }

        /// <summary>向下探测目标点：地面 / 水面 / 阻挡 / 坡度。目标区域未加载时找不到地面。</summary>
        internal static Dictionary<string, object> Probe(Vector3 target)
        {
            return ToolsNavigation.WithScene(scene =>
            {
                int waterLayer = LayerMask.NameToLayer("Water");
                if (waterLayer < 0) return new() { ["available"] = false, ["reason"] = "water_layer_unknown" };
                int waterMask = 1 << waterLayer;
                int solidMask = Physics.DefaultRaycastLayers & ~waterMask & ~LayerMask.GetMask("Player");
                // Search well below the requested point: a stale Y, a point under the
                // terrain or an unloaded scene group all show up as "no ground".
                var origin = target + Vector3.up * 3f;
                bool ground = Physics.Raycast(origin, Vector3.down, out RaycastHit hit, 200f,
                    solidMask, QueryTriggerInteraction.Ignore);
                var clearanceTop = target + Vector3.up * 1.5f;
                var clearanceBase = target + Vector3.up * 0.4f;
                bool blocked = Physics.CheckCapsule(clearanceBase, clearanceTop, 0.35f,
                    solidMask, QueryTriggerInteraction.Ignore);
                bool water = Physics.CheckCapsule(clearanceBase, clearanceTop, 0.4f, waterMask, QueryTriggerInteraction.Collide)
                    || Physics.Raycast(origin, Vector3.down, ground ? hit.distance : 200f, waterMask, QueryTriggerInteraction.Collide);
                float slope = ground ? Vector3.Angle(hit.normal, Vector3.up) : 0;
                float drop = ground ? target.y - hit.point.y : 0;
                return new() { ["available"] = true, ["ground_found"] = ground,
                    ["ground_y"] = ground ? hit.point.y : null, ["drop_m"] = ground ? drop : null,
                    ["slope_deg"] = ground ? slope : null, ["water_detected"] = water, ["obstructed"] = blocked,
                    // A landing is safe only when the ground is close to the requested Y.
                    ["suitable"] = ground && !blocked && !water && slope <= 50 && drop >= -3f && drop <= 3f,
                    ["surface"] = ground ? hit.collider?.name : null };
            });
        }
    }
}
