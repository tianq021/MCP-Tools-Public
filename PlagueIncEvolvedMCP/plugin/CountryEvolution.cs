using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using UnityEngine;

// Reads the single-player start-country choice screen (CCountrySelect / StartCountryScreen).
internal static class StartCountry
{
    private static readonly FieldInfo AllowInteractionField =
        typeof(CCountrySelect).GetField("mbAllowInteraction", BindingFlags.Instance | BindingFlags.NonPublic);

    public static CCountrySelect Select => CCountrySelect.instance;

    public static bool IsChoosing => CGameManager.game != null &&
        CGameManager.game.CurrentGameState == IGame.GameState.ChoosingCountry;

    public static bool InProgress => CGameManager.game != null &&
        CGameManager.game.CurrentGameState == IGame.GameState.InProgress;

    public static bool Active => IsChoosing && Select != null && CUIManager.instance != null &&
        CUIManager.instance.GetCurrentScreen() == Select;

    public static bool Started => CGameManager.game != null &&
        CGameManager.game.CurrentGameState == IGame.GameState.InProgress &&
        CUIManager.instance != null && CUIManager.instance.GetCurrentScreen() is CHUDScreen;

    public static CHUDPopupSubScreen Popup => Select != null ? Select.popupSubscreen : null;

    public static bool PopupActive => Popup != null && Popup.isActive;

    public static string PopupTitle => PopupActive && Popup.title != null ? Text(Popup.title.text) : null;
    public static string PopupText => PopupActive && Popup.description != null ? Text(Popup.description.text) : null;

    public static bool AllowsInteraction
    {
        get
        {
            var select = Select;
            if (select == null || AllowInteractionField == null) return false;
            return AllowInteractionField.GetValue(select) is bool allowed && allowed;
        }
    }

    public static string SelectedCountryId =>
        CInterfaceManager.instance != null && CInterfaceManager.instance.SelectedCountryView != null
            ? CInterfaceManager.instance.SelectedCountryView.GetCountry()?.id
            : null;

    public static string Signature()
    {
        if (!Active) return null;
        return string.Join("|", PopupActive ? "1" : "0", PopupTitle, PopupText, AllowsInteraction ? "1" : "0", SelectedCountryId);
    }

    public static IEnumerable<CountryOption> Options()
    {
        if (World.instance == null) yield break;
        foreach (var country in World.instance.countries)
        {
            if (country == null || string.IsNullOrEmpty(country.id)) continue;
            var view = CInterfaceManager.instance != null ? CInterfaceManager.instance.GetCountryView(country.id) : null;
            yield return new CountryOption(country, view);
        }
    }

    public static CountryOption Find(string countryId) =>
        Options().FirstOrDefault(option => string.Equals(option.Id, countryId, StringComparison.OrdinalIgnoreCase));

    // The same call the country bubble makes when the player confirms the start country.
    public static bool Choose(CountryOption option)
    {
        var view = option.View ?? CInterfaceManager.instance.GetCountryView(option.Id);
        if (view == null || Select == null) return false;
        Select.CountrySelected(view, view.transform.position);
        return true;
    }

    private static string Text(string value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

internal sealed class CountryOption
{
    public string Id { get; }
    public string Label { get; }
    public long Population { get; }
    public int Bonus { get; }
    public bool Island { get; }
    public string Continent { get; }
    public CountryView View { get; }

    public CountryOption(Country country, CountryView view)
    {
        Id = country.id;
        Label = CLocalisationManager.GetText(country.name) ?? country.name ?? country.id;
        Population = country.totalHealthy;
        Bonus = country.startCountryEvoBonus;
        Island = country.IsIsland();
        Continent = country.continentType.ToString().ToLowerInvariant();
        View = view;
    }
}

// Reads DNA points and the evolution (technology) tree of the local player's disease.
internal static class Evolution
{
    public static Disease Disease => CGameManager.localPlayerInfo != null ? CGameManager.localPlayerInfo.disease : null;

    public static bool Active => Disease != null && CGameManager.game != null &&
        CGameManager.game.CurrentGameState == IGame.GameState.InProgress;

    public static int Dna => Disease != null ? Disease.evoPoints : 0;
    public static int Spent => Disease != null ? Disease.evoPointsSpent : 0;
    public static int Earned => Disease != null ? Disease.dnaPointsGained : 0;
    public static int Turn => Disease != null ? Disease.turnNumber : 0;

    public static string Signature()
    {
        var disease = Disease;
        if (!Active) return null;
        return string.Join(",", disease.technologies.Where(tech => tech != null && disease.IsTechEvolved(tech))
            .Select(tech => tech.id).OrderBy(id => id, StringComparer.Ordinal));
    }

    public static IEnumerable<Technology> Available()
    {
        var disease = Disease;
        if (disease == null) yield break;
        foreach (var tech in disease.technologies)
        {
            if (tech == null || disease.IsTechEvolved(tech) || !disease.CanEvolve(tech)) continue;
            yield return tech;
        }
    }

    public static IEnumerable<Technology> Evolved()
    {
        var disease = Disease;
        if (disease == null) yield break;
        foreach (var tech in disease.technologies)
        {
            if (tech != null && disease.IsTechEvolved(tech)) yield return tech;
        }
    }

    public static string Label(Technology tech) => CLocalisationManager.GetText(tech.name) ?? tech.name ?? tech.id;

    public static string TypeId(Technology tech) => tech.gridType switch
    {
        Technology.ETechType.transmission => "transmission",
        Technology.ETechType.ability => "ability",
        Technology.ETechType.symptom => "symptom",
        _ => "other"
    };

    public static int Cost(Technology tech) => Disease != null ? Disease.GetEvolveCost(tech) : tech.cost;

    // Mirrors the disease screen's evolve button: only evolves when the game itself allows it.
    public static string TryEvolve(string techId)
    {
        var disease = Disease;
        if (disease == null) return "WRONG_MENU";
        var tech = disease.technologies.FirstOrDefault(candidate =>
            candidate != null && string.Equals(candidate.id, techId, StringComparison.OrdinalIgnoreCase));
        if (tech == null) return "OPTION_UNAVAILABLE";
        if (disease.IsTechEvolved(tech)) return "ALREADY_EVOLVED";
        if (!disease.CanEvolve(tech)) return "OPTION_LOCKED";
        if (disease.GetEvolveCost(tech) > disease.evoPoints) return "INSUFFICIENT_DNA";
        if (!CGameManager.game.EvolveTech(tech)) return "EVOLVE_FAILED";
        return disease.IsTechEvolved(tech) ? null : "EVOLVE_UNCONFIRMED";
    }
}
