extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;
using GameAssembly::com.ndemiccreations.survivors.ui;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    // Open the settlement screen on its morale/service tab (this is where fire pits, toilets,
    // housing upgrades, town square and kitchen rows live).
    private object OpenMorale(Work work)
    {
        RequireReady(); RequireOwner(work);
        var screen = game.gameView?.settlementScreen;
        if (screen == null) throw new BridgeError("SETTLEMENT_UI_UNAVAILABLE", "Settlement screen is not ready.");
        sequence++; handles.Clear();
        screen.OpenMorale();
        return new { submitted = true, opened = true, tab = "morale", next = "get_game_controls" };
    }

    // Click the game's own buy button for the row that matches a concept ident.
    // This is the same UnityEvent that a mouse click fires, so the game refreshes itself.
    private object BuyNative(Work work)
    {
        RequireReady(); RequireOwner(work);
        var ident = work.Args.GetProperty("tech_id").GetString();
        var screen = game.gameView?.settlementScreen;
        if (screen == null) throw new BridgeError("SETTLEMENT_UI_UNAVAILABLE", "Settlement screen is not ready.");
        if (!screen.gameObject.activeInHierarchy) screen.Open();

        var rows = screen.GetComponentsInChildren<SettlementRowAction>(true);
        SettlementRowAction match = null;
        var seen = new System.Collections.Generic.List<string>();
        if (rows != null)
        {
            foreach (var row in rows)
            {
                if (row == null) continue;
                var concept = row.cachedConcept;
                if (concept == null) continue;
                string rowIdent;
                try { rowIdent = concept.GetConceptIdent(); } catch { continue; }
                if (string.IsNullOrEmpty(rowIdent)) continue;
                if (!seen.Contains(rowIdent)) seen.Add(rowIdent);
                if (rowIdent == ident) { match = row; break; }
            }
        }
        if (match == null)
        {
            throw new BridgeError("ROW_NOT_VISIBLE",
                "No instantiated settlement row for '" + ident + "'. Rows currently present: " + string.Join(", ", seen.ToArray()));
        }
        var button = match.buyButton;
        if (button == null) throw new BridgeError("ROW_NOT_BUYABLE", "Row for '" + ident + "' has no buy button.");
        sequence++;
        button.onClick.Invoke();
        return new { submitted = true, tech_id = ident, clicked = true, next = "get_game_controls" };
    }
}

