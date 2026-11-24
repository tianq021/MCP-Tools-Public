using System;
using BroforceLifeTrainer;

// Minimal model of the inspected game's Flex Power API.
public enum PickupType
{
    None = 0,
    FlexAirJump = 10,
    FlexInvulnerability = 11,
    FlexGoldenLight = 12,
    FlexTeleport = 13,
    FlexAlluring = 14,
}

public class Player
{
    public PickupType power = PickupType.None;
    public bool alive = true;
    public bool honorForced = true;
    public int writes;
    public int ignored;
    public bool IsAlive() { return alive; }
    public bool HasFlexPower() { return power != PickupType.None; }
    public bool HasFlexPower(PickupType pickup) { return power == pickup; }
    public PickupType GetFlexPower() { return power; }
    public void AddFlexPower(PickupType pickup, bool forced)
    {
        if (!forced && power != PickupType.None) { ignored++; return; }
        writes++;
        if (honorForced) power = pickup;
    }
}

class FlexPowerSmoke
{
    static int checks;
    static void Check(bool value, string message) { checks++; if (!value) throw new Exception(message); }

    static void Main()
    {
        Player player = new Player();

        // Off must never touch the game; only 0 and 10..14 are accepted.
        Check(FlexPowers.Maintain(0, true, true, player) == 0 && player.writes == 0, "off must not write");
        foreach (int bad in new int[] { 1, 9, 15, 99, -11 })
            Check(FlexPowers.Maintain(bad, true, true, player) == 5 && player.writes == 0, "unsupported type " + bad);
        Check(FlexPowers.IsSupported(0) && FlexPowers.IsSupported(10) && FlexPowers.IsSupported(14), "supported range");
        Check(!FlexPowers.IsSupported(9) && !FlexPowers.IsSupported(15), "outside range rejected");

        // Online rooms are refused before anything is written.
        Check(FlexPowers.Maintain(11, false, true, player) == 3 && player.writes == 0, "online denied");

        // Waiting states never mutate the game.
        Check(FlexPowers.Maintain(11, true, false, player) == 2 && player.writes == 0, "loading or paused");
        Check(FlexPowers.Maintain(11, true, true, null) == 2, "absent player");
        player.alive = false;
        Check(FlexPowers.Maintain(11, true, true, player) == 2 && player.writes == 0, "dead player skipped");
        player.alive = true;

        // Granting replaces the single Flex Power slot and is forced.
        Check(FlexPowers.Maintain(11, true, true, player) == 1, "grant invulnerable");
        Check(player.power == PickupType.FlexInvulnerability && player.writes == 1, "forced write applied");
        Check(FlexPowers.Maintain(11, true, true, player) == 1 && player.writes == 1, "already active is idempotent");
        Check(FlexPowers.Maintain(12, true, true, player) == 1, "switch to golden light");
        Check(player.power == PickupType.FlexGoldenLight && player.writes == 2, "switching overwrites the slot");
        Check(FlexPowers.Maintain(10, true, true, player) == 1 && player.power == PickupType.FlexAirJump, "air jump");
        Check(FlexPowers.Maintain(13, true, true, player) == 1 && player.power == PickupType.FlexTeleport, "teleport");
        Check(FlexPowers.Maintain(14, true, true, player) == 1 && player.power == PickupType.FlexAlluring, "alluring");

        // A rejected native write must be reported, never silently accepted.
        Player broken = new Player();
        broken.honorForced = false;
        Check(FlexPowers.Maintain(11, true, true, broken) == 4, "failed native write reported");

        // Clear only removes the slot; dead players are untouched.
        Player clearable = new Player();
        clearable.power = PickupType.FlexInvulnerability;
        Check(FlexPowers.Clear(clearable) && clearable.power == PickupType.None, "clear removes the granted power");
        Player dead = new Player();
        dead.alive = false;
        Check(!FlexPowers.Clear(dead), "no clear on dead player");
        Check(!FlexPowers.Clear(null), "no clear without player");

        Console.WriteLine("PASS: " + checks + " flex power behavior checks");
    }
}
