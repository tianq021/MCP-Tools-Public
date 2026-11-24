using System;
using System.Text.Json;
using System.Threading.Tasks;
using Il2Cpp;
using SlimeRancher2Bridge;

namespace Il2Cpp
{
    public record SlotFixture(IdentifiableType Id, int Count, bool IsUnlocked);
    public class SlotArrayFixture
    {
        public SlotFixture[] Values;
        public int Length => Values.Length;
        public SlotFixture this[int index] => Values[index];
    }
    public class AmmoFixture
    {
        public SlotArrayFixture Slots { get; } = new();
        public int _selectedAmmoIdx;
        public bool SetSelectedSlot(int index) { _selectedAmmoIdx = index; return true; }
    }
}

internal static partial class Program
{
    static async Task TestRescue(Func<string, object, string, Task<JsonElement>> write)
    {
        async Task<JsonElement> Data(string name, object args, string id = null) =>
            (await write(name, args, id)).GetProperty("data");
        var scene = SceneContext.Instance;
        var player = scene.PlayerState;
        const string health = "sr2_restore_health", energy = "sr2_restore_energy", slot = "sr2_select_vacpack_slot";
        const string mark = "sr2_mark_return_point", go = "sr2_return_to_mark";
        var execute = new { dry_run = false };
        var plan = await Data(health, new { points = 15 });
        Check("healing previews partial recovery", plan.GetProperty("target").GetInt32() == 52 && player.Health == 37);
        var heal = new { points = 15, dry_run = false };
        Check("partial healing uses actual readback", (await Data(health, heal, "recovery-hp")).GetProperty("delta").GetInt32() == 15 && player.Health == 52);
        Check("healing replay does not stack", (await write(health, heal, "recovery-hp")).GetProperty("cached").GetBoolean() && player.Health == 52);
        var capped = await Data(health, new { points = int.MaxValue, dry_run = false }, "recovery-cap");
        Check("large healing clamps without overflow", capped.GetProperty("after").GetInt32() == player.GetMaxHealth()
            && player.Health == 100 && player.Energy == 82);
        Check("full health is a no-op", (await Data(health, execute, "full-hp")).GetProperty("already_full").GetBoolean());
        Check("invalid energy amount rejected", (await Data(energy, new { points = -1, dry_run = false }, "bad-energy"))
            .GetProperty("reason").GetString() == "invalid_points" && player.Energy == 82);
        player.Health = 0;
        Check("knockout is preserved", (await Data(health, execute, "knocked-out")).GetProperty("reason").GetString() == "player_knocked_out" && player.Health == 0);
        player.Health = 37;
        Check("energy defaults to fill upgraded capacity", (await Data(energy, execute, "recovery-energy")).GetProperty("after").GetInt32() == 150 && player.Health == 37);

        var ammo = player.Ammo;
        var water = new IdentifiableType { ReferenceId = "WATER" };
        ammo.Slots.Values = new SlotFixture[] { new(null, 0, true), null, new(null, 0, false), new(water, 17, true) };
        var listed = (await Data("sr2_vacpack", new { })).GetProperty("slots");
        Check("vacpack list retains native index after missing slot", listed[2].GetProperty("index").GetInt32() == 3);
        var select = await Data(slot, new { index = 3 });
        Check("slot preview identifies contents", select.GetProperty("item_id").GetString() == "WATER" && ammo._selectedAmmoIdx == 0);
        Check("locked slot is rejected", (await Data(slot, new { index = 2, dry_run = false }, "locked-slot")).GetProperty("reason").GetString() == "slot_unavailable" && ammo._selectedAmmoIdx == 0);
        Check("out-of-range slot is rejected", (await Data(slot, new { index = int.MaxValue, dry_run = false }, "large-slot")).GetProperty("reason").GetString() == "invalid_slot_index");
        var selected = await Data(slot, new { index = 3, dry_run = false }, "select-water");
        Check("slot selection preserves contents", selected.GetProperty("applied").GetBoolean() && ammo._selectedAmmoIdx == 3 && ammo.Slots[3].Count == 17);

        var controller = scene.Controller;
        int moves = controller.MoveCount;
        var loader = SystemContext.Instance;
        var context = GameContext.Instance;
        scene.position = new(1, 2, 3);
        var markPlan = await Data(mark, new { });
        Check("previewing mark creates no return point", markPlan.GetProperty("can_mark").GetBoolean()
            && (await Data(go, execute, "empty-point")).GetProperty("reason").GetString() == "no_return_point");
        Check("mark records player's position", (await Data(mark, execute, "mark-start")).GetProperty("applied").GetBoolean());
        scene.position = new(9, 8, 7);
        var replace = await Data(mark, new { });
        Check("mark replacement preview shows previous coordinates", replace.GetProperty("previous_mark").GetProperty("position").GetProperty("x").GetSingle() == 1);
        ToolsLanding.Suitable = false;
        Check("failed probe preserves old mark", !(await Data(mark, execute, "unsafe-mark")).GetProperty("applied").GetBoolean());
        var unsafeReturn = await Data(go, new { dry_run = false, force = true }, "unsafe-return");
        Check("return rejects unsafe terrain even with a force argument", !unsafeReturn.GetProperty("applied").GetBoolean()
            && unsafeReturn.GetProperty("target").GetProperty("x").GetSingle() == 1 && controller.MoveCount == moves);
        ToolsLanding.Suitable = true;
        context.SaveName = "second-save";
        Check("return points isolated by save", (await Data(go, execute, "other-save")).GetProperty("reason").GetString() == "no_return_point" && controller.MoveCount == moves);
        context.SaveName = "test-save";
        var originalGroup = loader.CurrentSceneGroup;
        loader.CurrentSceneGroup = new("other-group");
        Check("return refuses different scene group", (await Data(go, execute, "other-group")).GetProperty("reason").GetString() == "different_scene_group" && controller.MoveCount == moves);
        loader.CurrentSceneGroup = originalGroup;
        var returnPlan = await Data(go, new { });
        Check("return preview probes without moving", returnPlan.GetProperty("can_return").GetBoolean() && controller.MoveCount == moves);
        var returned = await Data(go, execute, "return-start");
        Check("return reaches marked point exactly once", returned.GetProperty("applied").GetBoolean() && controller.TransientPosition == new Il2CppMonomiPark.SlimeRancher.Player.CharacterController.Vector(1, 2, 3)
            && (await write(go, execute, "return-start")).GetProperty("cached").GetBoolean() && controller.MoveCount == moves + 1);
        await Data(mark, execute, "mark-again");
        Check("explicit marking replaces only current save's point", (await Data(go, new { })).GetProperty("target").GetProperty("x").GetSingle() == 9);
    }
}
