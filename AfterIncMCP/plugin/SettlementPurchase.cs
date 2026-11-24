extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private sealed record PurchaseOffer(string tech_id, string name, string category, bool available,
        string? blocked_reason, List<object> costs, int times_applied, int in_construction);
    private sealed record PurchaseRow(PurchaseOffer Offer, Tech Tech, ActionWrapper Row);

    private (string revision, string? blocker, List<PurchaseRow> rows) SettlementPurchaseRows()
    {
        RequireReady();
        var screen = game!.gameView.settlementScreen;
        var controller = screen?.actionController;
        if (controller == null || screen!.overviewPanel?.rowTemplate == null)
            throw new BridgeError("SHOP_NOT_READY", "Settlement shop is still initialising.");

        var pause = AutopauseController.SharedInstance;
        var menus = PauseCount(pause?.IsMenuShown);
        bool shopOpen = menus == 1 && pause!.IsMenuShown.IsNamedRetainRetained("com.ndemiccreations.survivors.ui.SettlementScreen");
        var blocker = game.optionalScoreCard.HasValue() ? "scenario_finished" :
            !((game.global.settlement.nexusWagon?.isSettled) ?? false) ? "not_settled" :
            RealDialog(pause) || (menus > 0 && !shopOpen) ? "menu_or_dialog" : null;
        // Gather the resource pages plus the actual overview, training and outpost categories of this game.
        var groups = new Dictionary<string, DisplayClassification>(StringComparer.Ordinal);
        var pages = controller.GetDisplayClassifications();
        var count = pages.Cast<Il2CppSystem.Collections.Generic.ICollection<DisplayClassification>>().Count;
        for (int i = 0; i < count; i++) {
            var page = pages[i];
            if (page != null) groups.TryAdd(page.ident, page);
        }
        foreach (var t in game.global.techManager.all) {
            if (t?.ident == null || t.classifications == null ||
                !(Training(t) || t.research_level_increase || t.ident.StartsWith("outpost_", StringComparison.Ordinal))) continue;
            foreach (var page in t.classifications)
                if (page != null) groups.TryAdd(page.ident, page);
        }

        var rows = new List<PurchaseRow>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        // NEVER hand a managed delegate to the game here. Converting System.Action to
        // Il2CppSystem.Action goes through Il2CppInterop's ClassInjector, which hooks Class::Init;
        // on Unity 6000 that signature scan fails ("signatures have been exhausted") and the
        // process dies with 0xC0000005 a moment later. onRefresh only repaints the panel after a
        // purchase, so passing null is safe and PurchaseSettlement repaints the panel itself.
        foreach (var page in groups.Values) {
            var candidates = controller.CalculateRows(page, null, screen.overviewPanel.rowTemplate, null);
            foreach (var row in candidates) {
                var tech = row.concept?.TryCast<Tech>();
                if (tech?.ident == null || row.isDevolving || !seen.Add(tech.ident)) continue;
                var locked = tech.IsConceptLocked();
                var reason = blocker ?? (locked.HasValue() ? Text(locked.value.longDescription) :
                    row.onActivation == null ? "no_purchase_action" :
                    row.commodities != null && !row.commodities.CanAfford() ? "cannot_afford" : null);
                var offer = new PurchaseOffer(tech.ident, Text(tech.name), page.ident, reason == null, reason,
                    CostItems(row.commodities), tech.timesApplied, row.constructionCount);
                rows.Add(new(offer, tech, row));
            }
        }
        rows.Sort((a,b) => StringComparer.Ordinal.Compare(a.Offer.tech_id, b.Offer.tech_id));
        var revision = Hash(new { current = Revision(), blocker, offers = rows.Select(r => r.Offer) });
        return (revision, blocker, rows);
    }

    private object GetSettlementPurchases(Work work)
    {
        var shop = SettlementPurchaseRows();
        return new { session_id = session, turn = game!.simulator.gameTurn, shop.revision, shop.blocker,
            offers = shop.rows.Select(r => r.Offer).ToArray() };
    }

    private object PurchaseSettlement(Work work)
    {
        RequireReady(); RequireOwner(work); RequireActiveSettlement();
        var shop = SettlementPurchaseRows();
        if (work.Args.GetProperty("expected_revision").GetString() != shop.revision)
            throw new BridgeError("STALE_STATE", "Read current settlement purchases before buying.");
        var id = work.Args.GetProperty("tech_id").GetString();
        var target = shop.rows.Find(r => r.Offer.tech_id == id);
        if (target == null || !target.Offer.available)
            throw new BridgeError("PURCHASE_UNAVAILABLE", target?.Offer.blocked_reason ?? "No such current shop offer.");
        var before = target.Tech.timesApplied;
        sequence++; handles.Clear();
        // row.onActivation is an IL2CPP delegate the game built itself, so invoking it needs no
        // class injection. The row was built with a null refresh callback, so repaint the panel
        // ourselves afterwards; if the game's own callback throws after the purchase was applied,
        // report the real timesApplied delta instead of failing the whole call.
        string? activation_error = null;
        try { target.Row.onActivation.Invoke(); }
        catch (Exception e) { activation_error = e.Message; }
        var applied = target.Tech.timesApplied;
        try {
            var screen = game!.gameView.settlementScreen;
            if (screen != null && screen.isActiveAndEnabled) screen.RefreshUI();
        } catch (Exception e) { Trace("shop refresh after purchase: " + e.Message); }
        return new { submitted = true, tech_id = id, name = target.Offer.name, times_applied_before = before,
            times_applied_after = applied, applied = applied > before, activation_error = activation_error,
            constructed = game!.global.techManager.IsAppliedAndConstructed(target.Tech.ident),
            next = applied > before ? "get_settlement_purchases" : "get_game_controls" };
    }
}
