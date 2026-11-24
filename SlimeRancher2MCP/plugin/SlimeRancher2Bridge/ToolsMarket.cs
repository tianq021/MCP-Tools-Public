using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace SlimeRancher2Bridge
{
    internal static class ToolsMarket
    {
        // Il2CppInterop exposes reference-bearing native fields as properties.
        // Blittable structs may instead retain their managed fields.
        static object Member(object instance, string name)
        {
            Type type = instance.GetType();
            var property = type.GetProperty(name);
            if (property != null) return property.GetValue(instance);
            var field = type.GetField(name);
            if (field != null) return field.GetValue(instance);
            throw new MissingMemberException(type.FullName, name);
        }

        internal static Dictionary<string, object> Read(Dictionary<string, object> args)
            => ToolsNavigation.WithScene(scene =>
            {
                var director = Member(scene, "PlortEconomyDirector");
                if (director == null || !(bool)Member(director, "_hasInitializedMarketPrices"))
                    return new() { ["available"] = false, ["reason"] = "market_not_ready" };
                var settings = Member(director, "_settings");
                if (settings == null) return new() { ["available"] = false, ["reason"] = "settings_unavailable" };
                var table = Member(settings, "PlortsTable");
                if (table == null || Member(table, "Plorts") is not IEnumerable entries)
                    return new() { ["available"] = false, ["reason"] = "catalog_unavailable" };

                var directorType = director.GetType();
                var closed = directorType.GetMethod("IsMarketShutdown", Type.EmptyTypes)
                    ?? throw new MissingMethodException(directorType.FullName, "IsMarketShutdown");
                var query = directorType.GetMethod("TryGetMarketValues")
                    ?? throw new MissingMethodException(directorType.FullName, "TryGetMarketValues");
                var prices = new List<object>();
                int quoted = 0;
                foreach (var entry in entries)
                {
                    if (entry == null) continue;
                    var item = Member(entry, "Type");
                    if (item == null) continue;
                    object[] values = { item, 0, 0 };
                    bool found = (bool)query.Invoke(director, values);
                    if (found) quoted++;
                    prices.Add(new
                    {
                        id = Member(item, "ReferenceId"), name = Member(item, "name"),
                        price = found ? values[1] : null, change = found ? values[2] : null,
                        base_price = Member(entry, "InitialValue"), full_saturation = Member(entry, "FullSaturation")
                    });
                }
                return new() { ["available"] = quoted > 0, ["quoted_count"] = quoted, ["listed_count"] = prices.Count,
                    ["market_closed"] = closed.Invoke(director, null), ["prices"] = prices };
            });
    }
}
