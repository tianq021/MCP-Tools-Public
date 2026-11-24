using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;

internal sealed class GameSetupMenu
{
    private static readonly FieldInfo GeneCategory = typeof(CGSGeneSubScreen).GetField("selectedCategoryButton", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo ViewedGene = typeof(CGSGeneSubScreen).GetField("selectedGene", BindingFlags.Instance | BindingFlags.NonPublic);

    private readonly CGSScreen setup;
    private readonly IGameSetupSubScreen step;
    private readonly CMainSpeedSubScreen speed;

    public GameSetupMenu(CGSScreen setup, IGameSetupSubScreen step, CMainSpeedSubScreen speed)
    {
        this.setup = setup;
        this.step = step;
        this.speed = speed;
    }

    public string Stage => step switch
    {
        CGSDiseaseSubScreen => "disease",
        CGSDiseaseXSubScreen x => x.diseaseXTypeSelection != null && x.diseaseXTypeSelection.activeInHierarchy
            ? "disease_template" : "disease_template_options",
        CGSGeneSubScreen => "genes",
        CGSDifficultySubScreen => "difficulty",
        CGSNameSubScreen => "name",
        CGSOutbreakSubScreen => "outbreak",
        CGSOutbreakLoadoutSubScreen => "outbreak_loadout",
        _ => speed != null ? "speed_run_disease" : "setup"
    };

    public string Title => setup?.titleLabelContainer?.activeInHierarchy == true ? Text(setup.titleLabel) : null;
    public string Name => step is CGSNameSubScreen name && name.input != null ? name.input.value : null;
    public uint? Difficulty => setup != null ? setup.Difficulty : (uint?)null;
    public int? NameLimit => step is CGSNameSubScreen name && name.input != null ? name.input.characterLimit : (int?)null;

    public string GeneView => step is CGSGeneSubScreen genes
        ? $"{Category(genes)?.category}:{Preview(genes)?.id}:{genes.geneListPanel?.activeInHierarchy}"
        : null;

    public string[] SelectedGenes => setup?.SelectedGenes?.Values.Where(gene => gene != null)
        .Select(gene => gene.id).OrderBy(id => id, StringComparer.Ordinal).ToArray() ?? Array.Empty<string>();

    public bool TrySetName(string value)
    {
        if (!(step is CGSNameSubScreen name) || name.input == null || !name.input.isActiveAndEnabled ||
            string.IsNullOrWhiteSpace(value) || value.Any(char.IsControl) ||
            (name.input.characterLimit > 0 && value.Length > name.input.characterLimit)) return false;
        name.input.value = value;
        return true;
    }

    public IEnumerable<SetupOption> Options()
    {
        if (speed != null)
        {
            yield return SetupOption.Button("next", speed.continueButton, "Start Game", true);
            yield return SetupOption.Button("back", speed.buttonBack, "Back", true);
            yield break;
        }
        if (setup == null || step == null) yield break;

        var next = setup.nextButton;
        var back = setup.previousButton;
        if (step is CGSDiseaseXSubScreen template)
        {
            if (Visible(template.nextButton)) next = template.nextButton;
            if (Visible(template.previousButton)) back = template.previousButton;
        }
        yield return SetupOption.Button("next", next, Text(setup.nextText) ?? "Continue", true);
        yield return SetupOption.Button("back", back, "Back", true);

        if (step is CGSDifficultySubScreen difficulty)
        {
            foreach (var option in difficulty.buttons ?? Array.Empty<DifficultySelectButton>())
            {
                if (option == null || !option.isActiveAndEnabled || !Visible(option.button)) continue;
                var level = (uint)option.difficulty;
                yield return new SetupOption("difficulty_" + Number(option.difficulty),
                    CGameManager.GetDifficultyLocalisedString(option.difficulty, difficulty.isCureUI),
                    option.button.isEnabled, () => difficulty.ChooseDifficulty(option, false),
                    () => setup.Difficulty == level, selected: setup.Difficulty == level);
            }
        }
        else if (step is CGSNameSubScreen name)
        {
            yield return new SetupOption("set_name", "Set Name", name.input != null && name.input.isActiveAndEnabled, null);
            yield return SetupOption.Button("random_name", name.randomButton, "Random Name");
        }
        else if (step is CGSGeneSubScreen genes)
        {
            foreach (var option in GeneOptions(genes)) yield return option;
        }
        else if (step is CGSOutbreakLoadoutSubScreen loadout && loadout.loadoutButtonContainer != null)
        {
            foreach (var option in loadout.loadoutButtonContainer.GetComponentsInChildren<LoadoutButton>())
            {
                if (option == loadout.loadoutButtonPrefab || !option.isActiveAndEnabled || !Visible(option.button)) continue;
                yield return new SetupOption("loadout_" + Number(option.loadoutIndex), Text(option.loadoutTitle) ?? Number(option.loadoutIndex),
                    MainMenu.ButtonAvailable(option.button), option.button.OnClick, transition: true);
            }
        }
    }

    private IEnumerable<SetupOption> GeneOptions(CGSGeneSubScreen genes)
    {
        var category = Category(genes);
        var preview = Preview(genes);
        foreach (var slot in genes.geneTypeButtons ?? Array.Empty<GeneTypeButton>())
        {
            if (slot == null || !slot.isActiveAndEnabled) continue;
            yield return new SetupOption("gene_slot_" + Number((int)slot.category), Text(slot.nameLabel) ?? slot.category.ToString(),
                GeneCategory != null, slot.OnClick, () => Category(genes) == slot, selected: category == slot);
        }

        var entries = genes.geneSelectButtons ?? Array.Empty<GeneSelectButton>();
        for (var index = 0; index < entries.Length; index++)
        {
            var entry = entries[index];
            if (entry == null || !entry.isActiveAndEnabled || entry.gene == null) continue;
            var gene = entry.gene;
            var unlocked = setup.UnlockedGenes?.Contains(gene) == true;
            yield return new SetupOption("gene_" + Number(index), Text(entry.nameLabel) ?? gene.geneName,
                unlocked && ViewedGene != null, entry.OnClick, () => Preview(genes) == gene, selected: preview == gene);
        }

        var insert = genes.insertGeneButton;
        if (preview != null && setup.UnlockedGenes?.Contains(preview) == true && Visible(insert) &&
            setup.GetSelectedGene(preview.geneCategory) != preview)
        {
            yield return new SetupOption("insert_gene", MainMenu.ButtonLabel(insert, "Insert Gene"), MainMenu.ButtonAvailable(insert),
                insert.OnClick, () => setup.GetSelectedGene(preview.geneCategory) == preview);
        }

        if (category != null && Visible(genes.clearGeneButton))
        {
            yield return new SetupOption("clear_gene", MainMenu.ButtonLabel(genes.clearGeneButton, "Clear Gene"),
                MainMenu.ButtonAvailable(genes.clearGeneButton), genes.clearGeneButton.OnClick,
                () => setup.GetSelectedGene(category.category) == null);
        }
    }

    private static GeneTypeButton Category(CGSGeneSubScreen genes) => GeneCategory?.GetValue(genes) as GeneTypeButton;
    private static Gene Preview(CGSGeneSubScreen genes) => ViewedGene?.GetValue(genes) as Gene;
    private static bool Visible(UIButton button) => button != null && button.gameObject.activeInHierarchy;
    internal static string Text(UILabel label) => label != null && !string.IsNullOrWhiteSpace(label.text) ? label.text.Trim() : null;
    private static string Number(int number) => number.ToString(CultureInfo.InvariantCulture);
}

internal sealed class SetupDialog
{
    private readonly CGameOverlay overlay;

    public SetupDialog(CGameOverlay overlay) => this.overlay = overlay;

    public string Title => overlay switch
    {
        CConfirmOverlay confirm => GameSetupMenu.Text(confirm.msgTitle),
        CPopupOverlay popup => GameSetupMenu.Text(popup.title),
        _ => null
    };

    public string Text => overlay switch
    {
        CConfirmOverlay confirm => GameSetupMenu.Text(confirm.msgText),
        CPopupOverlay popup => GameSetupMenu.Text(popup.description),
        _ => null
    };

    public IEnumerable<SetupOption> Options()
    {
        if (overlay is CConfirmOverlay confirm)
        {
            if (!confirm.blockButtonA && confirm.buttonA != null && confirm.buttonA.gameObject.activeInHierarchy)
                yield return SetupOption.Button("dialog_a", confirm.buttonA, GameSetupMenu.Text(confirm.buttonAText) ?? "Confirm", true);
            if (!confirm.blockButtonB && confirm.buttonB != null && confirm.buttonB.gameObject.activeInHierarchy)
                yield return SetupOption.Button("dialog_b", confirm.buttonB, GameSetupMenu.Text(confirm.buttonBText) ?? "Cancel", true);
        }
        else if (overlay is CPopupOverlay popup && popup.close != null && popup.close.gameObject.activeInHierarchy)
        {
            yield return SetupOption.Button("dialog_close", popup.close, "Close", true);
        }
    }
}

internal sealed class SetupOption
{
    public string Id { get; }
    public string Label { get; }
    public bool Available { get; }
    public Action Apply { get; }
    public Func<bool> Verify { get; }
    public bool Transition { get; }
    public bool Selected { get; }

    public SetupOption(string id, string label, bool available, Action apply, Func<bool> verify = null, bool transition = false, bool selected = false)
    {
        Id = id;
        Label = label;
        Available = available;
        Apply = apply;
        Verify = verify;
        Transition = transition;
        Selected = selected;
    }

    public static SetupOption Button(string id, UIButton button, string fallback, bool transition = false) =>
        new SetupOption(id, MainMenu.ButtonLabel(button, fallback), MainMenu.ButtonAvailable(button),
            button != null ? (Action)button.OnClick : null, transition: transition);
}
