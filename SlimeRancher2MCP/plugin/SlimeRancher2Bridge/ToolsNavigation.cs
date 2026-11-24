using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MelonLoader;
using UnityEngine;

namespace SlimeRancher2Bridge
{
    internal static class ToolsNavigation
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_minimap", Minimap, mainThread: true);
            server.Register("sr2_player_position", Position, mainThread: true);
            server.Register("sr2_teleport", Teleport, mainThread: true, write: true);
        }

        static Dictionary<string, object> Minimap(Dictionary<string, object> args)
        {
            var mod = MelonMod.RegisteredMelons.FirstOrDefault(m => m.Info.Name == "SlimeMap");
            var dir = Path.Combine(ToolsCore.GameDirectory, "UserData", "SlimeMap");
            return new Dictionary<string, object>
            {
                ["loaded"] = mod != null,
                ["version"] = mod?.Info.Version,
                ["saved_mode"] = ReadSetting(dir, "navigation.cfg", "minimap"),
                ["saved_menu_key"] = ReadSetting(dir, "hotkey.cfg", "menu.key"),
                ["default_menu_key"] = "F7"
            };
        }

        static string ReadSetting(string dir, string file, string key)
        {
            var path = Path.Combine(dir, file);
            if (!File.Exists(path)) return null;
            string found = null;
            foreach (string line in File.ReadLines(path))
            {
                var parts = line.Split('=', 2);
                if (parts.Length == 2 && string.Equals(parts[0].Trim(), key, StringComparison.OrdinalIgnoreCase))
                    found = parts[1].Trim();
            }
            return found;
        }

        // The interop assembly is generated on the target computer at first startup.
        // Resolve only known game members and keep Unity objects on the main thread.
        static Type FindGameType(string name)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(name.Contains('.') ? name : "Il2Cpp." + name);
                if (type != null) return type;
            }
            return null;
        }

        static object Property(Type type, object instance, string name)
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.FlattenHierarchy;
            var property = type.GetProperty(name, flags | (instance == null ? BindingFlags.Static : BindingFlags.Instance));
            if (property == null) throw new MissingMemberException(type.FullName, name);
            return property.GetValue(instance);
        }

        static object Read(object obj, string name) => Property(obj.GetType(), obj, name);

        static float Component(object vector, string axis)
        {
            var type = vector.GetType();
            var field = type.GetField(axis);
            return (float)(field != null ? field.GetValue(vector) : Read(vector, axis));
        }

        static Dictionary<string, object> Position(Dictionary<string, object> args)
            => WithPlayer(player =>
            {
                var transform = Read(player, "transform");
                return new()
                {
                    ["available"] = true,
                    ["position"] = Coordinates(Read(transform, "position")),
                    ["forward"] = Coordinates(Read(transform, "forward"))
                };
            });

        static object Coordinates(object v) => new { x = Component(v, "x"), y = Component(v, "y"), z = Component(v, "z") };

        internal static float Axis(Dictionary<string, object> args, string key)
        {
            if (!args.TryGetValue(key, out var value) || value is not (long or double or int or float))
                throw new ArgumentException("Missing or non-numeric coordinate: " + key);
            float number = Convert.ToSingle(value);
            if (!float.IsFinite(number)) throw new ArgumentException("Non-finite coordinate: " + key);
            return number;
        }

        static Dictionary<string, object> Teleport(Dictionary<string, object> args)
        {
            float x = Axis(args, "x"), y = Axis(args, "y"), z = Axis(args, "z");
            bool preview = !args.TryGetValue("dry_run", out var mode) || mode switch
            {
                bool flag => flag,
                _ => throw new ArgumentException("dry_run must be a boolean")
            };
            bool force = args.TryGetValue("force", out var forceArg) && forceArg is bool forced && forced;
            return TeleportTo(x, y, z, preview, force);
        }

        internal static Dictionary<string, object> TeleportTo(float x, float y, float z, bool preview, bool force = false)
        {
            return WithPlayer(player =>
            {
                Type type = FindGameType("Il2CppMonomiPark.SlimeRancher.Player.CharacterController.SRCharacterController");
                var lookup = player.GetType().GetMethods().FirstOrDefault(m => m.Name == "GetComponentInChildren"
                    && m.IsGenericMethodDefinition && m.GetGenericArguments().Length == 1
                    && m.GetParameters() is { Length: 1 } p && p[0].ParameterType == typeof(bool));
                var controller = type == null || lookup == null ? null : lookup.MakeGenericMethod(type).Invoke(player, new object[] { true });
                var motor = controller == null ? null : Read(controller, "Motor");
                if (motor == null) return new() { ["available"] = false, ["reason"] = "controller_unavailable" };
                var current = Read(motor, "TransientPosition");
                var vector = current.GetType();
                var move = motor.GetType().GetMethod("SetPosition", new[] { vector, typeof(bool) });
                var reset = controller.GetType().GetMethod("ResetVelocity", new[] { typeof(bool) });
                var stop = controller.GetType().GetMethod("StopAbilitiesForTeleport", Type.EmptyTypes);
                if (move == null || reset == null || stop == null) return new() { ["available"] = false, ["reason"] = "unsupported_game_api" };
                var target = Activator.CreateInstance(vector, x, y, z);
                var result = new Dictionary<string, object> { ["available"] = true, ["dry_run"] = preview,
                    ["from"] = Coordinates(current), ["target"] = Coordinates(target) };
                // Crossing into an unloaded scene group leaves the player without terrain
                // and drops them into the sea. Always probe the landing point first.
                var probe = ToolsLanding.Probe(new Vector3(x, y, z));
                result["probe"] = probe;
                double dx = Component(target, "x") - Component(current, "x");
                double dy = Component(target, "y") - Component(current, "y");
                double dz = Component(target, "z") - Component(current, "z");
                result["distance"] = Math.Round(Math.Sqrt(dx * dx + dy * dy + dz * dz), 2);
                bool suitable = probe.TryGetValue("suitable", out var ok) && ok is bool landable && landable;
                if (!preview && !suitable && !force)
                {
                    result["blocked"] = true;
                    result["reason"] = probe.ContainsKey("available") && probe["available"] is bool available && !available
                        ? "probe_unavailable"
                        : "target_not_landable";
                    result["hint"] = "目标点没有可用地面（区域可能未加载/是水面/被阻挡）。确认安全后可用 force=true 强制传送";
                    return result;
                }
                if (!preview)
                {
                    stop.Invoke(controller, null);
                    reset.Invoke(controller, new object[] { false });
                    move.Invoke(motor, new object[] { target, true });
                    var after = Read(motor, "TransientPosition");
                    result["position_after"] = Coordinates(after);
                    result["reached"] = Math.Abs(Component(after, "x") - x) < 0.01f
                        && Math.Abs(Component(after, "y") - y) < 0.01f && Math.Abs(Component(after, "z") - z) < 0.01f;
                }
                return result;
            });
        }

        static Dictionary<string, object> WithPlayer(Func<object, Dictionary<string, object>> action)
            => WithScene(scene =>
            {
                var player = Read(scene, "Player");
                if (player == null || Read(scene, "PlayerState") == null)
                    return new() { ["available"] = false, ["reason"] = "player_unavailable" };
                return action(player);
            });

        internal static Dictionary<string, object> WithScene(Func<object, Dictionary<string, object>> action)
        {
            Dictionary<string, object> Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };
            var sceneType = FindGameType("SceneContext");
            var systemType = FindGameType("SystemContext");
            if (sceneType == null || systemType == null) return Unavailable("interop_unavailable");
            try
            {
                var system = Property(systemType, null, "Instance");
                if (system == null) return Unavailable("session_unavailable");
                var loader = Read(system, "SceneLoader");
                if (loader == null || (bool)Read(loader, "IsSceneLoadInProgress")) return Unavailable("loading");
                var gameplay = loader.GetType().GetMethod("IsCurrentSceneGroupGameplay", Type.EmptyTypes);
                if (gameplay == null) return Unavailable("unsupported_game_api");
                if (!(bool)gameplay.Invoke(loader, null)) return Unavailable("outside_gameplay");
                var scene = Property(sceneType, null, "Instance");
                return scene == null ? Unavailable("player_unavailable") : action(scene);
            }
            catch (MissingMemberException e)
            {
                return new() { ["available"] = false, ["reason"] = "unsupported_game_api", ["detail"] = e.Message };
            }
            catch (TargetInvocationException e)
            {
                return new() { ["available"] = false, ["reason"] = "runtime_read_failed", ["detail"] = e.GetBaseException().Message };
            }
        }
    }
}
