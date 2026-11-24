using System.Collections.Generic;
using Il2Cpp;

namespace SlimeRancher2Bridge
{
    internal static class ToolsSession
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_state", State, mainThread: true);
            server.Register("sr2_time", Time, mainThread: true);
            server.Register("sr2_player", Player, mainThread: true);
        }

        static Dictionary<string, object> State(Dictionary<string, object> args)
        {
            var loader = SystemContext.Instance?.SceneLoader;
            if (loader == null)
                return new() { ["available"] = false, ["reason"] = "session_unavailable" };

            var group = loader.CurrentSceneGroup;
            bool loading = loader.IsSceneLoadInProgress;
            string phase = "other";
            if (loading) phase = "loading";
            else if (group != null && loader.IsCurrentSceneGroupGameplay()) phase = "gameplay";
            else if (group != null && loader.IsCurrentSceneGroupMainMenu()) phase = "main_menu";

            return new()
            {
                ["available"] = true,
                ["phase"] = phase,
                ["loading"] = loading,
                ["scene_group"] = group?.ReferenceId,
                ["save_name"] = phase == "gameplay" ? GameContext.Instance?.AutoSaveDirector?.CurrentSaveGameName() : null
            };
        }

        static Dictionary<string, object> Time(Dictionary<string, object> args)
            => ToolsNavigation.WithScene(context =>
            {
                var clock = ((SceneContext)context).TimeDirector;
                if (clock == null || clock._worldModel == null)
                    return new() { ["available"] = false, ["reason"] = "time_unavailable" };

                return new()
                {
                    ["available"] = true,
                    ["day"] = clock.CurrDay(),
                    ["hour"] = clock.CurrHour(),
                    ["paused"] = clock.isPaused(),
                    ["fast_forwarding"] = clock.IsFastForwarding()
                };
            });

        static Dictionary<string, object> Player(Dictionary<string, object> args)
            => ToolsNavigation.WithScene(context =>
            {
                var scene = (SceneContext)context;
                var state = scene.PlayerState;
                var player = scene.Player;
                if (player == null || state == null || state._model == null)
                    return new() { ["available"] = false, ["reason"] = "player_unavailable" };

                var position = player.transform.position;
                var zone = scene.PlayerZoneTracker?.GetCurrentZone();
                return new()
                {
                    ["available"] = true,
                    ["health"] = state.GetCurrHealth(),
                    ["max_health"] = state.GetMaxHealth(),
                    ["energy"] = state.GetCurrEnergy(),
                    ["max_energy"] = state.GetMaxEnergy(),
                    ["coins"] = ToolsCurrency.ReadDefaultAmount(state),
                    ["position"] = new { position.x, position.y, position.z },
                    ["zone"] = zone?.ReferenceId
                };
            });
    }
}
