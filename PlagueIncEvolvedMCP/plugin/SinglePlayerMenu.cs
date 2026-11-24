using System.Collections.Generic;
using System.Linq;
using UnityEngine;

internal sealed class SinglePlayerMode
{
    public string Id { get; }
    public string FallbackLabel { get; }
    public IGame.GameType GameType { get; }
    public UIToggle Toggle { get; }
    public UIButton StartButton { get; }
    public string Destination { get; }
    public bool Selected => Toggle != null && Toggle.value && CGameManager.gameType == GameType;
    public bool Visible => Toggle != null && Toggle.gameObject.activeInHierarchy;
    public bool Available
    {
        get
        {
            if (!Visible || !Toggle.enabled || Toggle.onChange.Count == 0) return false;
            var button = Toggle.GetComponent<UIButton>();
            return button == null || button.isEnabled;
        }
    }

    public bool CanStart => MainMenu.ButtonAvailable(StartButton) &&
        (GameType != IGame.GameType.Cure || Main.HasCureDLC());

    public SinglePlayerMode(string id, string label, IGame.GameType gameType, UIToggle toggle, UIButton start, string destination)
    {
        Id = id;
        FallbackLabel = label;
        GameType = gameType;
        Toggle = toggle;
        StartButton = start;
        Destination = destination;
    }
}

internal static class SinglePlayerMenu
{
    public static IEnumerable<SinglePlayerMode> Modes(CMainNewSubScreen page)
    {
        yield return new SinglePlayerMode("classic", "Classic", IGame.GameType.Classic,
            page.buttonClassic, page.buttonStart, "game_setup");
        yield return new SinglePlayerMode("outbreak", "Outbreak", IGame.GameType.Outbreak,
            page.buttonOutbreak, page.buttonStartOutbreak, "game_setup");
        yield return new SinglePlayerMode("cure", "The Cure", IGame.GameType.Cure,
            page.buttonCure, page.buttonStartCure, "game_setup");
        yield return new SinglePlayerMode("speed_run", "Speed Run", IGame.GameType.SpeedRun,
            page.buttonSpeed, page.buttonStart, "speed_run_setup");
        yield return new SinglePlayerMode("official_scenarios", "Official Scenarios", IGame.GameType.Official,
            page.buttonOfficial, page.buttonStart, "official_scenarios");
        yield return new SinglePlayerMode("custom_scenarios", "Custom Scenarios", IGame.GameType.Custom,
            page.buttonCustom, page.buttonStart, "custom_scenarios");
    }

    public static SinglePlayerMode Selected(CMainNewSubScreen page) =>
        Modes(page).FirstOrDefault(mode => mode.Visible && mode.Selected);

    public static object Describe(CMainNewSubScreen page, bool blocked)
    {
        var selected = Selected(page);
        var hasDedicatedPanel = selected?.GameType == IGame.GameType.Cure || selected?.GameType == IGame.GameType.Outbreak;
        var title = hasDedicatedPanel ? page.textTypeTitleCure : page.textTypeTitle;
        var description = hasDedicatedPanel ? page.textTypeDescriptionCure : page.textTypeDescription;
        return new
        {
            selected_mode = selected?.Id,
            title = title != null ? title.text : null,
            description = description != null ? description.text : null,
            modes = Modes(page).Where(mode => mode.Visible).Select(mode => new
            {
                id = mode.Id,
                label = MainMenu.ButtonLabel(mode.Toggle, mode.FallbackLabel),
                available = !blocked && mode.Available
            }).ToArray(),
            start = selected?.StartButton != null && selected.StartButton.gameObject.activeInHierarchy ? new
            {
                label = MainMenu.ButtonLabel(selected.StartButton, "Start Game"),
                available = !blocked && selected.CanStart
            } : null
        };
    }
}
