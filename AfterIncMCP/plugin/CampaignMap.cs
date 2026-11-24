extern alias GameAssembly;
extern alias FoundationAssembly;
using GameAssembly::com.ndemiccreations.survivors;
using GameAssembly::com.ndemiccreations.survivors.oogui;
using GameAssembly::com.ndemiccreations.survivors.ui;
using FoundationAssembly::com.ndemiccreations.foundation;
using UnityEngine;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private OoGUI? menu;
    private static bool Same(UnityEngine.Object? a, UnityEngine.Object? b) =>
        a != null && b != null && a.Pointer == b.Pointer;

    internal void MenuTick(IntPtr animator)
    {
        if (Broker.Queue.IsEmpty && owner == null && advancing == null) return;
        if (menu == null) menu = UnityEngine.Object.FindObjectOfType<OoGUI>();
        if (menu != null && menu.screenSelect != null && menu.screenSelect.Pointer == animator)
            Tick(IntPtr.Zero);
    }

    private OoGUIScreen? CurrentScreen()
    {
        if (game != null || menu == null || menu.screenSelect == null) return null;
        var current = menu.screenSelect.GetCurrent();
        return current.HasValue() ? current.value : null;
    }

    private static string? LockReason(Campaigns.Campaign campaign, LevelBuilder level)
    {
        var campaignLock = campaign.IsConceptLocked();
        if (campaignLock.HasValue()) return Text(campaignLock.value.longDescription);
        var levelLock = level.IsConceptLocked();
        if (levelLock.HasValue()) return Text(levelLock.value.longDescription);
        return level.CompletedPreviousLevels() ? null : "请先完成前置关卡";
    }

    private static bool Usable(OoGUITouchToken? token) =>
        token != null && token.isActiveAndEnabled && !token.blockInteraction;

    private bool SetupScreen(OoGUIScreen? current) => menu != null &&
        (Same(current, menu.leaderSelect) || Same(current, menu.diffultySelect) ||
         Same(current, menu.originSelect) || Same(current, menu.loadoutSelect) ||
         Same(current, menu.nameScreen) || Same(current, menu.cheatSelect));

    private static bool VisibleButton(SimpFrame? frame) =>
        frame != null && frame.isActiveAndEnabled && frame.button != null &&
        frame.button.isActiveAndEnabled && frame.button.interactable && frame.onClickAction.HasValue();

    private IEnumerable<(string id, string label, Action invoke)> SetupButtons(OoGUIScreen current)
    {
        if (Same(current, menu!.nameScreen)) {
            var naming = menu.nameScreen;
            var title = "开始游戏：" + naming.GetSettlementName();
            yield return (Hash(new { instance = naming.GetInstanceID(), title }), title, naming.GoStartGame);
            yield break;
        }
        var screen = current.Cast<SurvivorsOoGUIScreenBase>();
        ThreeButtonCompanion? cards = Same(current, menu!.leaderSelect) ? menu.leaderSelect.threeButtons :
            Same(current, menu.diffultySelect) ? menu.diffultySelect.threeButtons :
            Same(current, menu.originSelect) ? menu.originSelect.threeButtons :
            Same(current, menu.loadoutSelect) ? menu.loadoutSelect.threeButtons : null;
        if (cards != null && cards.buttons != null) {
            foreach (var card in cards.buttons) {
                if (card == null || !VisibleButton(card.frame)) continue;
                yield return Button(card.frame, Text(card.titleText));
            }
        }
        var primary = screen.bottomButtons?.rightBlueButton;
        if (VisibleButton(primary)) yield return Button(primary!, primary!.text?.text ?? "");
    }

    private static (string, string, Action) Button(SimpFrame frame, string label)
        => (Hash(new { instance = frame.GetInstanceID(), label }), label, frame.GoClick);

    private object CampaignMap()
    {
        var current = CurrentScreen();
        var levels = new List<object>();
        var regions = new List<object>();
        var options = new List<object>();
        var gui = menu;
        var phase = Ready() ? "in_settlement" : "unavailable";
        if (current != null && gui != null) {
            phase = Same(current, gui.levelSelect) ? "campaign_map" : Same(current, gui.welcome) ?
                "welcome" : SetupScreen(current) ? "level_setup" : "other_screen";
            if (phase == "campaign_map" && gui.worldMap != null) {
                var map = gui.worldMap;
                if (map.anchors != null) foreach (var anchor in map.anchors) {
                    if (anchor == null || !anchor.hasToken || anchor.token == null || !anchor.token.isActiveAndEnabled) continue;
                    regions.Add(new { region_id = anchor.campaign.ToString(), title = anchor.token.text?.text,
                        selectable = Usable(anchor.token) && anchor.token.touchEndAction != null });
                }
                if (map.levelTokens != null) foreach (var bubble in map.levelTokens) {
                    if (bubble?.level == null || bubble.campaign == null) continue;
                    var level = bubble.level;
                    var reason = LockReason(bubble.campaign, level);
                    levels.Add(new { campaign_id = bubble.campaign.GetConceptIdent(), level_id = level.ident,
                        title = Text(level.GetConceptTitle()), unlocked = reason == null, locked_reason = reason,
                        selectable = reason == null && Usable(bubble.token), completed = level.persistentSettings?.gamesWon > 0 });
                }
            } else if (phase == "level_setup") {
                foreach (var button in SetupButtons(current)) options.Add(new { option_id = button.id, title = button.label });
            }
        }
        var levelId = Ready() ? game!.launchData?.builder?.ident :
            (current != null && gui!.selected.HasValue() ? gui.selected.value.builder?.ident : null);
        var screen = current?.name;
        var revision = Hash(new { session, screen, instance = current?.GetInstanceID(), phase, levelId, levels, regions, options });
        return new CampaignView(phase, screen, levelId, regions, levels, options, revision);
    }

    private sealed record CampaignView(string phase, string? screen, string? level_id, List<object> regions,
        List<object> levels, List<object> options, string revision);

    private object NavigateCampaign(Work work)
    {
        RequireOwner(work);
        var view = (CampaignView)CampaignMap();
        if (work.Args.GetProperty("expected_revision").GetString() != view.revision)
            throw new BridgeError("STALE_STATE", "Read the campaign map again.");
        var current = CurrentScreen();
        if (current == null || menu == null)
            throw new BridgeError("NOT_IN_MENU", "Campaign navigation requires the game menu.");
        if ((menu.screenSelect.inputBlocker?.activeInHierarchy ?? false) || menu.worldMap?.isTransitioning == true)
            throw new BridgeError("MENU_TRANSITION", "Wait for the menu animation to finish.");
        var a = work.Args;
        switch (work.Command) {
            case "open_campaign_map":
                if (!Same(current, menu.welcome)) throw new BridgeError("WRONG_SCREEN", "Open the map from the welcome screen.");
                menu.screenSelect.GoForward(menu.levelSelect);
                break;
            case "select_campaign_region": {
                RequireMap(current);
                var id = a.GetProperty("region_id").GetString();
                var anchors = menu.worldMap!.anchors.Where(x => x != null && x.hasToken && Usable(x.token)
                    && x.campaign.ToString() == id && x.token.touchEndAction != null).ToArray();
                if (anchors.Length != 1) throw new BridgeError("REGION_UNAVAILABLE", "Select an available region from the current map.");
                anchors[0].token.touchEndAction.Invoke();
                break;
            }
            case "enter_campaign_level": {
                RequireMap(current);
                var id = a.GetProperty("level_id").GetString();
                var campaign = a.GetProperty("campaign_id").GetString();
                var matches = new List<LevelBubble>();
                foreach (var item in menu.worldMap!.levelTokens)
                    if (item != null && item.level != null && item.level.ident == id &&
                        item.campaign != null && item.campaign.GetConceptIdent() == campaign) matches.Add(item);
                if (matches.Count != 1) throw new BridgeError("LEVEL_UNAVAILABLE", "Select a level from the current region.");
                var bubble = matches[0];
                var reason = LockReason(bubble.campaign, bubble.level);
                if (reason != null) throw new BridgeError("LEVEL_LOCKED", reason);
                if (!Usable(bubble.token)) throw new BridgeError("LEVEL_UNAVAILABLE", "The map token is inactive.");
                menu.levelSelect.SelectLevelBuilder(bubble.level);
                var selected = menu.levelSelect.mostRecentlySelected;
                if (!selected.HasValue() || selected.value.Pointer != bubble.level.Pointer)
                    throw new BridgeError("SELECTION_CHANGED", "The game did not select the requested level.");
                menu.levelSelect.detailPanel.GoPlay(bubble.level);
                break;
            }
            case "choose_campaign_option": {
                if (!SetupScreen(current)) throw new BridgeError("WRONG_SCREEN", "Requires a level setup screen.");
                var id = a.GetProperty("option_id").GetString();
                var options = SetupButtons(current).Where(b => b.id == id).ToList();
                if (options.Count != 1) throw new BridgeError("OPTION_UNAVAILABLE", "Choose an option from the current setup screen.");
                options[0].invoke();
                break;
            }
        }
        return new { submitted = true, previous_screen = view.screen, next = "get_campaign_map" };
    }

    private void RequireMap(OoGUIScreen current)
    {
        if (menu?.worldMap == null || !Same(current, menu.levelSelect))
            throw new BridgeError("NOT_ON_CAMPAIGN_MAP", "Open the campaign map first.");
    }
}
