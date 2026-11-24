using System;
using System.Reflection;
using UnityEngine;

// Reads and controls the running single-player game: clock, totals, cure, speed and in-game popups.
internal static class GameStatus
{
    private static readonly FieldInfo HudPopupField =
        typeof(CHUDScreen).GetField("popupSubscreen", BindingFlags.Instance | BindingFlags.NonPublic);

    public static bool HasGame => CGameManager.game != null && CGameManager.localPlayerInfo != null &&
        CGameManager.localPlayerInfo.disease != null;

    public static Disease Disease => HasGame ? CGameManager.localPlayerInfo.disease : null;
    public static IGame.GameState State => CGameManager.game != null ? CGameManager.game.CurrentGameState : IGame.GameState.None;
    public static bool InProgress => State == IGame.GameState.InProgress;
    public static bool Ended => (World.instance != null && World.instance.gameEnded) || State == IGame.GameState.EndGame;
    public static bool Won => World.instance != null && World.instance.gameWon;
    public static string Result => World.instance != null ? World.instance.gameEndResult.ToString().ToLowerInvariant() : null;
    public static int Speed => CGameManager.game != null ? CGameManager.game.WantedSpeed : 0;

    public static CHUDPopupSubScreen HudPopup
    {
        get
        {
            var hud = CHUDScreen.instance;
            return hud != null && HudPopupField != null ? HudPopupField.GetValue(hud) as CHUDPopupSubScreen : null;
        }
    }

    public static bool HudPopupActive => HudPopup != null && HudPopup.isActive;
    public static string HudPopupTitle => HudPopupActive && HudPopup.title != null ? Trim(HudPopup.title.text) : null;
    public static string HudPopupText => HudPopupActive && HudPopup.description != null ? Trim(HudPopup.description.text) : null;

    public static string StateId => State switch
    {
        IGame.GameState.None => "none",
        IGame.GameState.Initialise => "initialising",
        IGame.GameState.Choosing => "choosing",
        IGame.GameState.ChoosingCountry => "choose_country",
        IGame.GameState.InProgress => "in_progress",
        IGame.GameState.EndGame => "end_game",
        _ => "unknown"
    };

    public static string Signature()
    {
        if (!HasGame) return null;
        var disease = Disease;
        return string.Join("|", Speed, StateId, Ended ? "1" : "0", Won ? "1" : "0", Result,
            HudPopupTitle, HudPopupText, disease.name);
    }

    public static int Turn => World.instance != null ? World.instance.DiseaseTurn : Disease != null ? Disease.turnNumber : 0;

    public static string Date => World.instance != null
        ? CUtils.FormatDateShort(new DateTime(World.instance.startDate).AddDays(World.instance.DiseaseTurn))
        : null;

    public static void SetSpeed(int speed)
    {
        var game = CGameManager.game;
        if (game == null || speed < 0 || speed > 3) return;
        if (speed == 0)
        {
            game.SetSpeed(0);
        }
        else
        {
            CGameManager.SetPaused(false, forced: true);
            game.SetSpeed(speed);
        }
    }

    public static bool CloseHudPopup()
    {
        var popup = HudPopup;
        if (popup == null || !popup.isActive) return false;
        popup.OnMessageClose();
        return true;
    }

    // Clicks active DNA/cure/infection bubbles through the same handler the mouse uses.
    public static int CollectBubbles()
    {
        var bubbles = UnityEngine.Object.FindObjectsByType<BonusObject>(FindObjectsSortMode.None);
        var clicked = 0;
        foreach (var bubble in bubbles)
        {
            if (bubble == null || bubble.HasBeenClicked || !bubble.isVisible) continue;
            try
            {
                bubble.OnMouseDown();
                if (bubble.HasBeenClicked) clicked++;
            }
            catch { }
        }
        LastCollected += clicked;
        return clicked;
    }

    public static bool AutoCollect { get; set; }
    public static int LastCollected { get; private set; }
    private static float lastCollectAt;

    public static int ActiveBubbles()
    {
        var bubbles = UnityEngine.Object.FindObjectsByType<BonusObject>(FindObjectsSortMode.None);
        var active = 0;
        foreach (var bubble in bubbles)
        {
            if (bubble != null && !bubble.HasBeenClicked && bubble.isVisible) active++;
        }
        return active;
    }

    public static object[] TechTree()
    {
        var disease = Disease;
        var list = new System.Collections.Generic.List<object>();
        if (disease == null) return list.ToArray();
        foreach (var tech in disease.technologies)
        {
            if (tech == null) continue;
            list.Add(new
            {
                id = tech.id,
                label = Evolution.Label(tech),
                type = Evolution.TypeId(tech),
                level = tech.level,
                cost = Evolution.Cost(tech),
                lethality = tech.changeToLethality,
                severity = tech.changeToSeverity,
                infectiousness = tech.changeToInfectiousness,
                evolved = disease.IsTechEvolved(tech),
                available = !disease.IsTechEvolved(tech) && disease.CanEvolve(tech),
                requires_all = tech.requiredTechAND ?? new System.Collections.Generic.List<string>(),
                requires_any = tech.requiredTechOR ?? new System.Collections.Generic.List<string>()
            });
        }
        return list.ToArray();
    }

    public static void TickAutoCollect()
    {
        if (!AutoCollect || !HasGame || !InProgress) return;
        if (Time.realtimeSinceStartup - lastCollectAt < 0.2f) return;
        lastCollectAt = Time.realtimeSinceStartup;
        CollectBubbles();
    }

    private static string Trim(string value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}
