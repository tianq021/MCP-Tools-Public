using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using UnityEngine;

internal sealed class DiseaseMenu
{
    private static readonly MethodInfo SelectSpeed = typeof(CMainSpeedSubScreen).GetMethod("SetCurrentDisease", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly MethodInfo SelectTemplate = typeof(CGSDiseaseXSubScreen).GetMethod("OnClickDisease", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo SpeedSelection = typeof(CMainSpeedSubScreen).GetField("diseaseType", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo Templates = typeof(CGSDiseaseXSubScreen).GetField("buttons", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo OutbreakSelection = typeof(CGSOutbreakSubScreen).GetField("selectedSeed", BindingFlags.Instance | BindingFlags.NonPublic);

    private readonly CGSScreen setup;
    private readonly IGameSetupSubScreen step;
    private readonly CMainSpeedSubScreen speed;

    public DiseaseMenu(CGSScreen setup, IGameSetupSubScreen step, CMainSpeedSubScreen speed)
    {
        this.setup = setup;
        this.step = step;
        this.speed = speed;
    }

    public bool IsSelection => speed != null || step is CGSDiseaseSubScreen || IsTemplateSelection;
    public bool IsOutbreakSelection => step is CGSOutbreakSubScreen;
    public int? SelectedOutbreak => IsOutbreakSelection ? (int?)OutbreakSelection?.GetValue(step) : null;

    private bool IsTemplateSelection => step is CGSDiseaseXSubScreen template &&
        template.diseaseXTypeSelection != null && template.diseaseXTypeSelection.activeInHierarchy;

    public Disease.EDiseaseType? Selected
    {
        get
        {
            if (speed != null) return (Disease.EDiseaseType?)SpeedSelection?.GetValue(speed);
            if (step is CGSDiseaseSubScreen && setup != null) return setup.DiseaseType;
            if (IsTemplateSelection && Templates?.GetValue(step) is List<CGSDiseaseSelectButton> buttons)
            {
                foreach (var button in buttons)
                {
                    if (button != null && button.isActiveAndEnabled && button.selectedSprite != null &&
                        button.selectedSprite.gameObject.activeInHierarchy) return button.diseaseType;
                }
            }
            return null;
        }
    }

    public IEnumerable<DiseaseOption> Options()
    {
        if (speed != null && speed.diseaseParent != null)
        {
            foreach (var button in speed.diseaseParent.GetComponentsInChildren<CMainDiseaseSelectButton>())
            {
                if (button == speed.diseaseTemplate || !button.isActiveAndEnabled) continue;
                yield return new DiseaseOption(button.diseaseType, button.diseaseName, button.locked,
                    SelectSpeed != null && SpeedSelection != null && MainMenu.ButtonAvailable(button.uiButton),
                    () => SelectSpeed.Invoke(speed, new object[] { button }));
            }
        }
        else if (step is CGSDiseaseSubScreen standard && standard.diseaseTable != null)
        {
            foreach (var button in standard.diseaseTable.GetComponentsInChildren<CGSDiseaseSelectButton>())
            {
                if (!button.isActiveAndEnabled || button.uiButton == null) continue;
                yield return new DiseaseOption(button.diseaseType, button.diseaseName, button.locked,
                    MainMenu.ButtonAvailable(button.uiButton), () => standard.SelectDisease(button, false, false));
            }
        }
        else if (IsTemplateSelection && Templates?.GetValue(step) is List<CGSDiseaseSelectButton> templates)
        {
            foreach (var button in templates)
            {
                if (button == null || !button.isActiveAndEnabled || button.uiButton == null) continue;
                yield return new DiseaseOption(button.diseaseType, button.diseaseName, button.locked,
                    SelectTemplate != null && MainMenu.ButtonAvailable(button.uiButton),
                    () => SelectTemplate.Invoke(step, new object[] { button }));
            }
        }
    }

    public static string Id(Disease.EDiseaseType type) => type.ToString().ToLowerInvariant();

    public IEnumerable<OutbreakOption> Outbreaks()
    {
        if (!(step is CGSOutbreakSubScreen outbreak) || outbreak.missionWidgetContainer == null || MissionController.Instance == null)
            yield break;

        foreach (var tile in outbreak.missionWidgetContainer.GetComponentsInChildren<MissionButton>())
        {
            if (!tile.isActiveAndEnabled || tile == outbreak.missionButtonPrefab) continue;
            var mission = MissionController.Instance.GetMission(tile.seed);
            if (mission == null) continue;
            var button = MainMenu.ButtonAvailable(tile.button) ? tile.button : tile.completedButton;
            yield return new OutbreakOption(mission, MainMenu.ButtonAvailable(button) ? button : null);
        }
    }
}

internal sealed class OutbreakOption
{
    public Mission Mission { get; }
    public UIButton Button { get; }
    public string Id => "outbreak_" + unchecked((uint)Mission.seed).ToString("x8", CultureInfo.InvariantCulture);
    public bool Locked => !Mission.IsPlayable();
    public bool Available => !Locked && Button != null;

    public OutbreakOption(Mission mission, UIButton button)
    {
        Mission = mission;
        Button = button;
    }
}

internal sealed class DiseaseOption
{
    public Disease.EDiseaseType Type { get; }
    public string Id => DiseaseMenu.Id(Type);
    public string Label { get; }
    public bool Locked { get; }
    public bool Available { get; }
    public Action Select { get; }

    public DiseaseOption(Disease.EDiseaseType type, UILabel label, bool locked, bool enabled, Action select)
    {
        Type = type;
        Label = label != null && !string.IsNullOrWhiteSpace(label.text) ? label.text.Trim() : type.ToString();
        Locked = locked;
        Available = !locked && enabled;
        Select = select;
    }
}
