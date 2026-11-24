using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Il2CppMonomiPark.SlimeRancher.Regions;
using Il2CppMonomiPark.SlimeRancher.World;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    // 把已加载的场景对象映射到游戏自己的 ZoneDefinition（「区域」）。
    //
    // 两条路径，都只用游戏现成数据：
    //   ① RegionMember.GetCurrentZones()：对象自己登记的区域成员关系，最准；
    //   ② Region.Bounds.Contains(position)：兜底，Region 是空间分区，
    //      Region.GetZoneId() 直接给出所属 zone；多个包围盒命中时取最小的。
    // 存档模型里的未加载地块没有 GameObject，也拿不到 zone —— 调用方返回 null 即可，
    // 由 Python 侧退回「离来源最近的已加载筒仓」近似。
    internal static class ToolsZone
    {
        internal static ZoneDefinition Of(GameObject obj, Vector3? position)
        {
            var fromMember = FromRegionMember(obj);
            if (fromMember != null) return fromMember;
            return position.HasValue ? FromRegionBounds(position.Value) : null;
        }

        // 把 zone 的稳定 ID 和本地化名写进返回行；没有 zone 时写 null，不编造。
        internal static void AddZone(Row row, GameObject obj, Vector3? position)
        {
            var zone = Of(obj, position);
            row["zone"] = zone?.ReferenceId;
            row["zone_title"] = zone == null ? null : Il2CppUtil.Localized(zone.Name);
        }

        internal static bool IsLoaded(string id) =>
            UnityEngine.Object.FindObjectsByType<Region>(FindObjectsSortMode.None)
                .Any(region => region != null && region.GetZoneId()?.ReferenceId == id);

        static ZoneDefinition FromRegionMember(GameObject obj)
        {
            if (obj == null) return null;
            try
            {
                var member = FindComponentInParent(obj, typeof(RegionMember));
                if (member == null) return null;
                var zones = Il2CppUtil.Invoke(member, "GetCurrentZones");
                foreach (var zone in Il2CppUtil.Enumerate(zones))
                    if (zone is ZoneDefinition definition && definition != null) return definition;
            }
            catch (Exception)
            {
                // RegionMember 缺失/接口变化都只影响精度，交给 Region 包围盒兜底。
            }
            return null;
        }

        static ZoneDefinition FromRegionBounds(Vector3 position)
        {
            ZoneDefinition found = null;
            float foundSize = float.MaxValue;
            try
            {
                foreach (var region in UnityEngine.Object.FindObjectsByType<Region>(FindObjectsSortMode.None))
                {
                    if (region == null) continue;
                    var bounds = Il2CppUtil.Try(region, "Bounds");
                    if (bounds is not Bounds box || !box.Contains(position)) continue;
                    float size = box.size.magnitude;
                    if (size >= foundSize) continue;
                    var zone = region.GetZoneId();
                    if (zone == null) continue;
                    found = zone;
                    foundSize = size;
                }
            }
            catch (Exception)
            {
                return null;
            }
            return found;
        }

        // Il2CppInterop 里泛型 GetComponentInParent<T>(bool) 要用反射拼出来，和 ToolsNavigation 的做法一致。
        static Component FindComponentInParent(GameObject obj, Type componentType)
        {
            var lookup = obj.GetType().GetMethods().FirstOrDefault(m => m.Name == "GetComponentInParent"
                && m.IsGenericMethodDefinition && m.GetGenericArguments().Length == 1
                && m.GetParameters() is { Length: 1 } parameters && parameters[0].ParameterType == typeof(bool));
            if (lookup == null) return null;
            return lookup.MakeGenericMethod(componentType).Invoke(obj, new object[] { true }) as Component;
        }
    }
}
