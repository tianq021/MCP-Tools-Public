using System;
using System.Collections.Generic;
using System.Linq;

namespace IfzNpcEnhanced;

// Pure policy: no Unity objects, networking, or model dependency.
public static class Policy
{
    public static bool RejectDepot(ulong npc, ulong depotOwner, bool isDepot, bool exchange) =>
        npc != 0 && isDepot && !exchange && depotOwner != npc;
    // Only a group that is NOT hiding can act right now. Infected lairs keep their members in a
    // hiding state until they spawn, so counting them as a standing threat froze scavenging and
    // construction forever whenever a lair happened to sit near the NPC headquarters.
    public static bool ThreatNear(bool hiding, float distance, float radius) => !hiding && distance <= radius;
    // Do not trade lives for nothing: only engage when the squad is healthy and not heavily outnumbered.
    public static bool Engage(float squadHp, int squadSize, int enemySize) =>
        squadHp >= .7f && squadSize > 0 && enemySize <= Math.Max(1, squadSize) * 2;
    public static bool CanAssign(ulong faction, ulong worker, ulong workplace) =>
        faction != 0 && worker == faction && workplace == faction;

    public static bool MayClaim(bool explicitOwner, bool claimed, bool headquarters, bool npcZone,
        bool canAdapt, float distance, float limit) =>
        !explicitOwner && !claimed && !headquarters && !npcZone && canAdapt &&
        distance >= 0 && distance <= limit;

    public static string SquadAction(bool endangered, bool night, bool wounded, bool unarmed,
        bool cargo, bool busy, bool homeThreat, bool atHome)
    {
        if (endangered || wounded || unarmed) return atHome ? "recover" : "return";
        if (homeThreat) return atHome ? "defend" : "return";
        if (night) return atHome ? "guard" : "return";
        if (busy) return "continue";
        return cargo ? "return" : "scavenge";
    }

    // Round-robin within each priority avoids one enormous workplace taking all citizens.
    public static int[] Allocate(int workers, IReadOnlyList<int> capacities, IReadOnlyList<int> priorities)
    {
        var result = new int[capacities.Count];
        foreach (var priority in priorities.Where(p => p > 0).Distinct().OrderByDescending(p => p))
        {
            bool changed;
            do
            {
                changed = false;
                for (var i = 0; i < result.Length && workers > 0; i++)
                    if (priorities[i] == priority && result[i] < Math.Max(0, capacities[i]))
                    { result[i]++; workers--; changed = true; }
            } while (changed && workers > 0);
        }
        return result;
    }

    public static double RetryAt(double nowHours, int failures) => nowHours + Math.Min(12, 0.5 * Math.Pow(2, Math.Min(5, Math.Max(0, failures - 1))));
}
