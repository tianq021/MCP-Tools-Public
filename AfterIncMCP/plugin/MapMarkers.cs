extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private sealed record Marker(string marker_id, int? zone_id, string kind, string? icon, string? frame,
        string? event_id, string? title, string? description, int? event_time_left, float? lifetime_turns,
        float? lifetime_progress, bool can_activate, string? interaction_blocker, int? fade_in_turns = null,
        int? exploration_turns = null, float? exploration_progress = null)
    {
        public string? symbol => System.IO.Path.GetFileName(icon) switch {
            "icon_exclaimationmark" or "icon_exclaimationmark_dark" => "!",
            "icon_questionmark" or "icon_questionmark_spooky" or
                "overlay_icon_questionmark" or "overlay_icon_questionmark_blue" => "?",
            _ => null
        };
    }

    private List<(Marker info, Action activate)> MapTokens()
    {
        var result = new List<(Marker info, Action activate)>();
        bool blocked = BlockingDialog(AutopauseController.SharedInstance);
        var events = game!.gameDecisions?.engaged;
        if (events != null) foreach (var e in events) {
            if (e == null || e.destroyMe || !e.HasVisibleToken() || !e.optionalToken.HasValue()) continue;
            var token = e.optionalToken.value;
            if (token == null || !token.isActiveAndEnabled || !token.isShown || token.isDestroyed) continue;
            var zone = e.zone?.index;
            var eventId = e.gameEvent?.ident;
            var id = Hash(new { session, instance = token.GetInstanceID(), eventId, zone, turn = e.turnOfEngagement });
            var icon = e.tokenParams?.icon;
            if (string.IsNullOrEmpty(icon)) icon = token.icon?.sprite?.name ?? e.icon;
            var blocker = blocked ? "menu_or_dialog" : token.FaderBlockingInteraction() ? "appearing" :
                !e.AllowInteraction() ? "game_disallows_interaction" : null;
            var info = new Marker(id, zone, "event", icon, e.tokenParams?.framePrefab, eventId,
                Text(e.GetTitle()), Text(e.description), e.timeleft >= 0 ? e.timeleft : null, null, null,
                blocker == null, blocker, e.tokenParams?.fadeInTurns, e.tokenParams?.exploreTimer,
                e.tokenParams?.exploreTimer > 0 ? e.CalculateExplorationProgress() : null);
            result.Add((info, token.GoClick));
        }
        var bubbles = game.global.bubbles?.allBubbles;
        if (bubbles != null) foreach (var b in bubbles) {
            if (b == null || b.wasClaimed) continue;
            var token = b.token;
            if (token == null || !token.isActiveAndEnabled) continue;
            var zone = b.zone?.index;
            var icon = token.icon?.sprite?.name;
            var id = Hash(new { session, instance = token.GetInstanceID(), zone, icon });
            var info = new Marker(id, zone, "bubble", icon, token.bubbleImage?.sprite?.name,
                null, null, null, null, b.isTimed ? b.turnLifeTime : null, b.isTimed ? b.progress : null, !blocked, blocked ? "menu_or_dialog" : null);
            result.Add((info, b.PopBubble));
        }
        return result;
    }

    private List<Marker> VisibleMapMarkers() => MapTokens().Select(t => t.info).OrderBy(t => t.marker_id).ToList();

    private object ReadMapMarkers()
    {
        RequireReady();
        return new { session_id = session, turn = game!.simulator.gameTurn, markers = VisibleMapMarkers() };
    }

    private object ActivateMapMarker(Work work)
    {
        RequireReady(); RequireOwner(work); RequireActiveSettlement();
        var id = work.Args.GetProperty("marker_id").GetString();
        var target = MapTokens().SingleOrDefault(t => t.info.marker_id == id);
        if (target.info == null || !target.info.can_activate)
            throw new BridgeError("MARKER_UNAVAILABLE", "Read current map markers and resolve any open dialog.");
        target.activate();
        sequence++; handles.Clear();
        return new { submitted = true, marker_id = id, target.info.kind, target.info.zone_id };
    }
}
