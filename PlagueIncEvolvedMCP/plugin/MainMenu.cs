using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

internal sealed class MainMenu
{
    private readonly string session = Guid.NewGuid().ToString("N");
    private int revision;
    private (string page, int screen, int subScreen, int overlay, IGame.GameType? mode, Disease.EDiseaseType? disease,
        bool diseaseSelection, int? outbreak, string stage, string name, uint? difficulty, string genes, string geneView,
        string dialogTitle, string dialogText, string countryState, string evolutionState, string statusState) context;
    private IGameSubScreen activeMenu;
    private DiseaseMenu diseaseMenu;
    private GameSetupMenu setupMenu;
    private SetupDialog setupDialog;
    private readonly Dictionary<string, (MenuRequest request, object response)> completed =
        new Dictionary<string, (MenuRequest, object)>();
    private readonly Queue<string> completionOrder = new Queue<string>();

    public void Observe()
    {
        var ui = CUIManager.instance;
        var screen = ui != null ? ui.GetCurrentScreen() : null;
        activeMenu = screen is CMainMenuScreen ? ActiveMenu(screen) : null;
        var overlay = ui != null && ui.GetOpenOverlay(out var open) ? open : null;
        var page = screen == null ? "loading" : screen is CMainMenuScreen ? "unavailable_submenu" : "outside_main_menu";
        if (activeMenu is CMainStartSubScreen) page = "main";
        else if (activeMenu is CMainNewSubScreen) page = "single_player";
        else if (activeMenu is CMainLoadSubScreen) page = "load_game";
        else if (activeMenu is CMainSpeedSubScreen) page = "speed_run_setup";
        else if (activeMenu is CMainOfficialSubScreen) page = "official_scenarios";
        else if (activeMenu is CMainCustomSubScreen) page = "custom_scenarios";
        else if (screen is CGSScreen && screen.gameObject.activeInHierarchy) page = "game_setup";

        var gameState = CGameManager.game != null ? CGameManager.game.CurrentGameState : IGame.GameState.None;
        var singlePlayer = CGameManager.gameType is IGame.GameType.Classic or IGame.GameType.Cure or IGame.GameType.Outbreak or
            IGame.GameType.SpeedRun or IGame.GameType.Official or IGame.GameType.Custom or IGame.GameType.Tutorial or IGame.GameType.CureTutorial;
        if (page == "outside_main_menu" && singlePlayer && screen.gameObject.activeInHierarchy &&
            (screen is CHUDScreen || screen == ui.GetScreen("StartCountryScreen") || screen == ui.GetScreen("StartCountryScreen_Cure")) &&
            gameState is IGame.GameState.ChoosingCountry or IGame.GameState.InProgress)
        {
            page = "in_game";
        }

        var setup = page == "game_setup" ? (CGSScreen)screen : null;
        var step = setup != null ? ActiveSetupStep(setup) : null;
        var speed = activeMenu as CMainSpeedSubScreen;
        diseaseMenu = setup != null || speed != null ? new DiseaseMenu(setup, step, speed) : null;
        setupMenu = setup != null || speed != null ? new GameSetupMenu(setup, step, speed) : null;
        setupDialog = overlay != null && (setupMenu != null || page == "in_game") ? new SetupDialog(overlay) : null;
        IGame.GameType? gameType = page is "single_player" or "game_setup" or "speed_run_setup" or "official_scenarios" or "custom_scenarios" or "in_game"
            ? CGameManager.gameType : (IGame.GameType?)null;
        var stage = page == "in_game" ? gameState == IGame.GameState.ChoosingCountry ? "choose_country" : "in_progress" : setupMenu?.Stage;
        var nextContext = (page, ObjectId(screen), ObjectId(step != null ? step : activeMenu), ObjectId(overlay),
            gameType, diseaseMenu?.Selected, diseaseMenu?.IsSelection == true, diseaseMenu?.SelectedOutbreak,
            stage, setupMenu?.Name, setupMenu?.Difficulty,
            stage == "genes" ? string.Join("|", setupMenu.SelectedGenes) : null, setupMenu?.GeneView,
            setupDialog?.Title, setupDialog?.Text, StartCountry.Signature(), Evolution.Signature(), GameStatus.Signature());
        if (nextContext != context)
        {
            context = nextContext;
            revision++;
        }
    }

    public object Call(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        Observe();
        if (request.Choice == null) return Reply(request);

        if (completed.TryGetValue(request.Id, out var previous))
        {
            return previous.request.Choice == request.Choice && previous.request.StateId == request.StateId && previous.request.Text == request.Text &&
                previous.request.View == request.View
                ? previous.response : Reply(request, false, "REQUEST_ID_REUSED");
        }

        object response;
        if (request.StateId != StateId)
        {
            response = Reply(request, false, "STALE_MENU");
        }
        else if (context.overlay != 0 && request.View != MenuView.Setup && request.View != MenuView.Evolution &&
            request.View != MenuView.Status)
        {
            response = Reply(request, false, "MENU_BLOCKED_BY_OVERLAY");
        }
        else if (request.View == MenuView.SinglePlayer)
        {
            response = SelectMode(request, out confirm);
            if (response == null) return null;
        }
        else if (request.View == MenuView.Disease)
        {
            response = SelectDisease(request);
        }
        else if (request.View == MenuView.Setup)
        {
            response = ApplySetup(request, out confirm);
            if (response == null) return null;
        }
        else if (request.View == MenuView.Country)
        {
            response = ApplyCountry(request, out confirm);
            if (response == null) return null;
        }
        else if (request.View == MenuView.Evolution)
        {
            response = ApplyEvolution(request, out confirm);
            if (response == null) return null;
        }
        else if (request.View == MenuView.Status)
        {
            response = ApplyStatus(request, out confirm);
            if (response == null) return null;
        }
        else
        {
            var option = Options().FirstOrDefault(candidate => candidate.Id == request.Choice);
            if (option == null || !option.Available)
            {
                response = Reply(request, false, "OPTION_UNAVAILABLE");
            }
            else
            {
                revision++;
                option.Button.OnClick();
                Observe();
                var expected = request.Choice == "back" ? "main" : request.Choice;
                response = context.page == expected
                    ? Reply(request, true) : Reply(request, null, "MENU_TRANSITION_UNCONFIRMED");
            }
        }

        return Remember(request, response);
    }

    private object SelectDisease(MenuRequest request)
    {
        if (diseaseMenu?.IsOutbreakSelection == true) return SelectOutbreak(request);
        if (!context.diseaseSelection) return Reply(request, false, "WRONG_MENU");
        var option = diseaseMenu.Options().FirstOrDefault(candidate => candidate.Id == request.Choice);
        if (option == null) return Reply(request, false, "OPTION_UNAVAILABLE");
        if (option.Locked) return Reply(request, false, "DISEASE_LOCKED");
        if (!option.Available) return Reply(request, false, "OPTION_UNAVAILABLE");

        if (context.disease != option.Type)
        {
            revision++;
            option.Select();
            Observe();
        }
        return context.diseaseSelection && context.disease == option.Type
            ? Reply(request, true) : Reply(request, null, "DISEASE_SELECTION_UNCONFIRMED");
    }

    private object SelectOutbreak(MenuRequest request)
    {
        var outbreak = diseaseMenu.Outbreaks().FirstOrDefault(option => option.Id == request.Choice);
        if (outbreak == null) return Reply(request, false, "OPTION_UNAVAILABLE");
        if (outbreak.Locked) return Reply(request, false, "OUTBREAK_LOCKED");
        if (!outbreak.Available) return Reply(request, false, "OPTION_UNAVAILABLE");

        if (context.outbreak != outbreak.Mission.seed)
        {
            revision++;
            outbreak.Button.OnClick();
            Observe();
        }
        return context.outbreak == outbreak.Mission.seed
            ? Reply(request, true) : Reply(request, null, "OUTBREAK_SELECTION_UNCONFIRMED");
    }

    private object SelectMode(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        if (!(activeMenu is CMainNewSubScreen page)) return Reply(request, false, "WRONG_MENU");
        if (request.Choice == "start")
        {
            var selected = SinglePlayerMenu.Selected(page);
            if (selected == null || !selected.CanStart) return Reply(request, false, "OPTION_UNAVAILABLE");

            revision++;
            selected.StartButton.OnClick();
            confirm = () => context.page == selected.Destination && context.overlay == 0 &&
                (selected.Destination != "game_setup" || context.mode == selected.GameType);
            return null;
        }

        var mode = SinglePlayerMenu.Modes(page).FirstOrDefault(candidate => candidate.Id == request.Choice);
        if (mode == null || !mode.Available) return Reply(request, false, "OPTION_UNAVAILABLE");
        if (!mode.Selected)
        {
            revision++;
            mode.Toggle.value = true;
            Observe();
        }
        return mode.Selected ? Reply(request, true) : Reply(request, null, "MODE_SELECTION_UNCONFIRMED");
    }

    private object ApplySetup(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        if (setupMenu == null && setupDialog == null) return Reply(request, false, "WRONG_MENU");
        var option = SetupOptions().FirstOrDefault(candidate => candidate.Id == request.Choice);
        if (option == null || !option.Available)
            return Reply(request, false, context.overlay != 0 ? "MENU_BLOCKED_BY_OVERLAY" : "OPTION_UNAVAILABLE");

        var before = context;
        if (request.Choice == "set_name")
        {
            if (!setupMenu.TrySetName(request.Text)) return Reply(request, false, "INVALID_GAME_NAME");
            Observe();
            return context.name == request.Text ? Reply(request, true) : Reply(request, null, "NAME_ADJUSTED_BY_GAME");
        }

        revision++;
        option.Apply();
        Observe();
        if (option.Transition)
        {
            if (before.overlay != 0)
                confirm = () => context.overlay != before.overlay || context.dialogTitle != before.dialogTitle || context.dialogText != before.dialogText;
            else confirm = () => request.Choice == "back"
                ? context.page != "outside_main_menu" && (context.page != before.page || context.subScreen != before.subScreen ||
                    context.stage != before.stage || context.outbreak != before.outbreak || context.geneView != before.geneView)
                : context.page == "in_game" || (context.page == "game_setup" && (context.page != before.page ||
                    context.subScreen != before.subScreen || context.stage != before.stage));
            if (!confirm()) return null;
            confirm = null;
            return Reply(request, true);
        }
        return (option.Verify?.Invoke() ?? (context.name != before.name))
            ? Reply(request, true) : Reply(request, null, "SETUP_SELECTION_UNCONFIRMED");
    }

    private object ApplyCountry(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        if (!StartCountry.Active) return Reply(request, false, "WRONG_MENU");
        if (request.Choice == "dialog_close")
        {
            var popup = StartCountry.Popup;
            if (popup == null || !popup.isActive) return Reply(request, false, "NO_POPUP");
            revision++;
            popup.OnMessageClose();
            Observe();
            return Reply(request, true);
        }

        if (StartCountry.PopupActive) return Reply(request, false, "POPUP_BLOCKING");
        if (!StartCountry.AllowsInteraction) return Reply(request, false, "INTERACTION_NOT_ALLOWED");
        var option = StartCountry.Find(request.Choice);
        if (option == null) return Reply(request, false, "OPTION_UNAVAILABLE");

        revision++;
        if (!StartCountry.Choose(option)) return Reply(request, false, "COUNTRY_SELECT_FAILED");
        confirm = () => StartCountry.Started;
        if (!confirm()) return null;
        confirm = null;
        return Reply(request, true);
    }

    private object ApplyEvolution(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        if (!Evolution.Active) return Reply(request, false, "WRONG_MENU");
        var error = Evolution.TryEvolve(request.Choice);
        if (error != null) return Reply(request, false, error);
        revision++;
        Observe();
        return Reply(request, true);
    }

    private object ApplyStatus(MenuRequest request, out Func<bool> confirm)
    {
        confirm = null;
        if (!GameStatus.HasGame) return Reply(request, false, "WRONG_MENU");
        switch (request.Choice)
        {
            case "paused": GameStatus.SetSpeed(0); break;
            case "normal": GameStatus.SetSpeed(1); break;
            case "faster": GameStatus.SetSpeed(2); break;
            case "fastest": GameStatus.SetSpeed(3); break;
            case "popup_close":
                if (!GameStatus.CloseHudPopup()) return Reply(request, false, "NO_POPUP");
                break;
            case "collect":
                GameStatus.CollectBubbles();
                break;
            case "autocollect":
                GameStatus.AutoCollect = true;
                break;
            case "autocollect_off":
                GameStatus.AutoCollect = false;
                break;
            case "tech_tree":
                revision++;
                Observe();
                return new
                {
                    ok = true,
                    screen = context.page,
                    stage = context.stage,
                    game_state = GameStatus.StateId,
                    state_id = StateId,
                    tree = GameStatus.TechTree(),
                    applied = true,
                    error = (string)null,
                    request_id = request.Id
                };
            default:
            {
                if (setupDialog == null) return Reply(request, false, "NO_DIALOG");
                var option = SetupOptions().FirstOrDefault(candidate => candidate.Id == request.Choice);
                if (option == null || !option.Available) return Reply(request, false, "OPTION_UNAVAILABLE");
                revision++;
                option.Apply();
                Observe();
                return Reply(request, true);
            }
        }
        revision++;
        Observe();
        return Reply(request, true);
    }

    private IEnumerable<SetupOption> SetupOptions() => setupDialog != null ? setupDialog.Options()
        : context.overlay == 0 && setupMenu != null ? setupMenu.Options() : Array.Empty<SetupOption>();

    public object ConfirmTransition(MenuRequest request, Func<bool> confirm, bool expired)
    {
        var arrived = confirm();
        if (!arrived && !expired) return null;
        return Remember(request, arrived ? Reply(request, true) : Reply(request, null, "MENU_TRANSITION_UNCONFIRMED"));
    }

    private object Remember(MenuRequest request, object response)
    {
        completed.Add(request.Id, (request, response));
        completionOrder.Enqueue(request.Id);
        if (completionOrder.Count > 128) completed.Remove(completionOrder.Dequeue());
        return response;
    }

    private string StateId => $"{session}-{revision}";

    private object Reply(MenuRequest request, bool? applied = null, string error = null)
    {
        if (request.View == MenuView.Setup)
        {
            return new
            {
                ok = error == null,
                screen = context.page,
                mode = ModeId(context.mode),
                stage = context.stage,
                title = setupMenu?.Title,
                in_game = context.page == "in_game",
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                name = context.name,
                name_limit = setupMenu?.NameLimit,
                difficulty = context.difficulty,
                selected_genes = setupMenu?.SelectedGenes ?? Array.Empty<string>(),
                dialog = setupDialog != null ? new { title = context.dialogTitle, text = context.dialogText } : null,
                options = SetupOptions().Select(option => new
                {
                    id = option.Id,
                    label = option.Label,
                    selected = option.Selected,
                    available = option.Available
                }).ToArray(),
                request_id = request.Id,
                applied,
                error
            };
        }

        if (request.View == MenuView.Disease)
        {
            var options = context.diseaseSelection ? diseaseMenu.Options().ToArray() : Array.Empty<DiseaseOption>();
            var isOutbreakPage = diseaseMenu?.IsOutbreakSelection == true;
            var outbreaks = isOutbreakPage ? diseaseMenu.Outbreaks().ToArray() : Array.Empty<OutbreakOption>();
            return new
            {
                ok = error == null,
                screen = context.page,
                mode = ModeId(context.mode),
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                selection_status = isOutbreakPage ? "choose_outbreak" : context.diseaseSelection ? "selectable" : context.page switch
                {
                    "official_scenarios" or "custom_scenarios" => "choose_scenario_first",
                    "game_setup" => "other_setup_step",
                    _ => "wrong_menu"
                },
                selected_disease = options.FirstOrDefault(option => option.Type == context.disease)?.Id,
                diseases = options.Select(option => new
                {
                    id = option.Id,
                    label = option.Label,
                    locked = option.Locked,
                    available = option.Available && context.overlay == 0
                }).ToArray(),
                selected_outbreak = outbreaks.FirstOrDefault(option => option.Mission.seed == context.outbreak)?.Id,
                outbreaks = outbreaks.Select(option =>
                {
                    var assigned = option.Mission.GetDiseaseType();
                    return new
                    {
                        id = option.Id,
                        label = option.Mission.name ?? option.Id,
                        disease_id = DiseaseMenu.Id(assigned),
                        disease_label = CGameManager.GetDiseaseNameLoc(assigned),
                        locked = option.Locked,
                        available = option.Available && context.overlay == 0
                    };
                }).ToArray(),
                request_id = request.Id,
                applied,
                error
            };
        }

        if (request.View == MenuView.Country)
        {
            var choosing = StartCountry.Active;
            var countries = choosing ? StartCountry.Options().ToArray() : Array.Empty<CountryOption>();
            return new
            {
                ok = error == null,
                screen = choosing ? "choose_country" : StartCountry.InProgress ? "in_game" : context.page,
                stage = choosing ? "choose_country" : StartCountry.InProgress ? "in_progress" : context.stage,
                in_game = choosing || StartCountry.InProgress || context.page == "in_game",
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                popup = choosing && StartCountry.PopupActive ? new { title = StartCountry.PopupTitle, text = StartCountry.PopupText } : null,
                allows_choice = choosing ? (bool?)StartCountry.AllowsInteraction : null,
                selected_country = choosing ? StartCountry.SelectedCountryId : null,
                country_total = countries.Length,
                countries = countries.Select(option => new
                {
                    id = option.Id,
                    label = option.Label,
                    population = option.Population,
                    bonus = option.Bonus,
                    island = option.Island,
                    continent = option.Continent
                }).ToArray(),
                request_id = request.Id,
                applied,
                error
            };
        }

        if (request.View == MenuView.Evolution)
        {
            var available = Evolution.Available().ToArray();
            var evolved = Evolution.Evolved().ToArray();
            return new
            {
                ok = error == null,
                screen = context.page,
                stage = context.stage,
                in_game = context.page == "in_game",
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                dna_points = Evolution.Dna,
                dna_spent = Evolution.Spent,
                dna_earned = Evolution.Earned,
                turn = Evolution.Turn,
                available = available.Select(tech => new
                {
                    id = tech.id,
                    label = Evolution.Label(tech),
                    type = Evolution.TypeId(tech),
                    level = tech.level,
                    cost = Evolution.Cost(tech),
                    lethality = tech.changeToLethality,
                    severity = tech.changeToSeverity,
                    infectiousness = tech.changeToInfectiousness
                }).ToArray(),
                evolved = evolved.Select(tech => new
                {
                    id = tech.id,
                    label = Evolution.Label(tech),
                    type = Evolution.TypeId(tech),
                    level = tech.level
                }).ToArray(),
                request_id = request.Id,
                applied,
                error
            };
        }

        if (request.View == MenuView.Status)
        {
            var disease = GameStatus.Disease;
            var world = World.instance;
            var options = new List<object>();
            if (setupDialog != null)
            {
                foreach (var option in SetupOptions())
                    options.Add(new { id = option.Id, label = option.Label, selected = option.Selected, available = option.Available });
            }
            else if (GameStatus.HudPopupActive)
            {
                options.Add(new { id = "popup_close", label = "Close", selected = false, available = true });
            }

            var population = world != null ? world.totalPopulation : 0L;
            var infected = disease != null ? disease.totalInfected : 0L;
            return new
            {
                ok = error == null,
                screen = GameStatus.Ended ? "end_game" : context.page,
                stage = GameStatus.Ended ? "end_game" : context.stage,
                game_state = GameStatus.StateId,
                in_game = context.page == "in_game",
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                speed = GameStatus.Speed,
                paused = GameStatus.Speed == 0,
                turn = GameStatus.Turn,
                date = GameStatus.Date,
                dna_points = disease != null ? disease.evoPoints : 0,
                dna_spent = disease != null ? disease.evoPointsSpent : 0,
                dna_earned = disease != null ? disease.dnaPointsGained : 0,
                population,
                infected,
                healthy = disease != null ? disease.totalHealthy : 0L,
                dead = disease != null ? disease.totalDead : 0L,
                killed = disease != null ? disease.totalKilled : 0L,
                zombie = disease != null ? disease.totalZombie : 0L,
                infected_percent = population > 0 ? (double)infected / population : 0,
                severity = disease != null ? disease.globalSeverity : 0f,
                lethality = disease != null ? disease.globalLethality : 0f,
                infectiousness = disease != null ? disease.globalInfectiousness : 0f,
                cure_percent = disease != null ? disease.cureCompletePercent : 0f,
                disease = disease != null ? disease.diseaseType.ToString().ToLowerInvariant() : null,
                disease_name = disease != null ? disease.name : null,
                difficulty = disease != null ? (int?)disease.difficulty : null,
                infected_countries = disease != null && disease.infectedCountries != null ? disease.infectedCountries.Count : 0,
                game_ended = GameStatus.Ended,
                game_won = GameStatus.Ended ? (bool?)GameStatus.Won : null,
                game_result = GameStatus.Ended ? GameStatus.Result : null,
                collected = GameStatus.LastCollected,
                bubbles = GameStatus.ActiveBubbles(),
                dialog = setupDialog != null ? new { title = context.dialogTitle, text = context.dialogText } : null,
                popup = GameStatus.HudPopupActive ? new { title = GameStatus.HudPopupTitle, text = GameStatus.HudPopupText } : null,
                options = options.ToArray(),
                request_id = request.Id,
                applied,
                error
            };
        }

        if (request.View == MenuView.SinglePlayer)
        {
            return new
            {
                ok = error == null,
                screen = context.page,
                blocked_by_overlay = context.overlay != 0,
                state_id = StateId,
                single_player = activeMenu is CMainNewSubScreen page ? SinglePlayerMenu.Describe(page, context.overlay != 0) : null,
                request_id = request.Id,
                applied,
                error
            };
        }

        return new
        {
            ok = error == null,
            screen = context.page switch
            {
                "game_setup" or "in_game" => "outside_main_menu",
                "speed_run_setup" or "official_scenarios" or "custom_scenarios" => "unavailable_submenu",
                _ => context.page
            },
            blocked_by_overlay = context.overlay != 0,
            state_id = StateId,
            options = Options().Select(option => new
            {
                id = option.Id,
                label = option.Label,
                available = option.Available && context.overlay == 0
            }).ToArray(),
            request_id = request.Id,
            applied,
            error
        };
    }

    private static string ModeId(IGame.GameType? mode) => mode switch
    {
        IGame.GameType.Classic => "classic",
        IGame.GameType.Outbreak => "outbreak",
        IGame.GameType.Cure => "cure",
        IGame.GameType.SpeedRun => "speed_run",
        IGame.GameType.Official => "official_scenarios",
        IGame.GameType.Custom => "custom_scenarios",
        _ => null
    };

    private IEnumerable<MenuOption> Options()
    {
        if (activeMenu is CMainStartSubScreen root)
        {
            yield return new MenuOption("single_player", "Single Player", root.buttonSinglePlayer);
            yield return new MenuOption("load_game", "Load Game", root.buttonLoad);
        }
        else if (activeMenu is CMainNewSubScreen || activeMenu is CMainLoadSubScreen)
        {
            yield return new MenuOption("back", "Back", ((CMainSubScreen)activeMenu).buttonBack);
        }
    }

    private static IGameSubScreen ActiveMenu(IGameScreen screen) => screen.subScreens.FirstOrDefault(sub =>
        sub != null && sub.isActive && (sub is CMainStartSubScreen || sub is CMainNewSubScreen || sub is CMainLoadSubScreen ||
            sub is CMainSpeedSubScreen || sub is CMainOfficialSubScreen || sub is CMainCustomSubScreen) &&
        screen.GetActiveSubScreen(sub.category) == sub);

    private static IGameSetupSubScreen ActiveSetupStep(CGSScreen screen) => screen.subScreens.OfType<IGameSetupSubScreen>()
        .FirstOrDefault(sub => sub != null && sub.isActive && screen.GetActiveSubScreen(sub.category) == sub);

    private static int ObjectId(UnityEngine.Object value) => value != null ? value.GetInstanceID() : 0;

    internal static bool ButtonAvailable(UIButton button) => button != null && button.gameObject.activeInHierarchy &&
        button.isEnabled && button.onClick.Count > 0;

    internal static string ButtonLabel(Component button, string fallback)
    {
        var label = button != null ? button.GetComponentInChildren<UILabel>() : null;
        return label != null && !string.IsNullOrWhiteSpace(label.text) ? label.text.Trim() : fallback;
    }

    private sealed class MenuOption
    {
        private readonly string fallback;
        public string Id { get; }
        public UIButton Button { get; }
        public bool Available => ButtonAvailable(Button);
        public string Label => ButtonLabel(Button, fallback);

        public MenuOption(string id, string fallback, UIButton button)
        {
            Id = id;
            this.fallback = fallback;
            Button = button;
        }
    }
}
