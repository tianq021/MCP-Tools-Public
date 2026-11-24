using IfzNpcEnhanced;
using Mono.Cecil;

var count = 0;
void Check(bool condition, string name) { if (!condition) throw new Exception("FAIL: " + name); count++; Console.WriteLine("PASS: " + name); }
Check(Policy.RejectDepot(10, 1, true, false), "NPC automatic return cannot use host depot");
Check(Policy.RejectDepot(10, 11, true, false), "NPC automatic return cannot use another NPC depot");
Check(Policy.RejectDepot(10, 0, true, false), "unknown depot ownership cannot silently use host fallback");
Check(!Policy.RejectDepot(10, 10, true, false), "own depot return remains allowed");
Check(!Policy.RejectDepot(10, 0, false, false), "ordinary scavenge destination remains allowed");
Check(!Policy.RejectDepot(10, 1, true, true), "explicit trade exchange remains allowed");
Check(!Policy.RejectDepot(0, 1, true, false), "unmanaged human orders remain untouched");
Check(Policy.CanAssign(10, 10, 10), "own workers allowed");
Check(!Policy.CanAssign(10, 11, 10) && !Policy.CanAssign(10, 10, 11), "cross-faction assignment blocked in both directions");
Check(!Policy.CanAssign(0, 0, 0) && !Policy.CanAssign(10, 0, 10), "unknown ownership blocked");
Check(!Policy.MayClaim(true, false, false, false, true, 50, 250), "explicitly owned site protected");
Check(!Policy.MayClaim(false, true, false, false, true, 50, 250), "unregistered player claim protected");
Check(!Policy.MayClaim(false, false, true, false, true, 50, 250), "HQ protected");
Check(!Policy.MayClaim(false, false, false, true, true, 50, 250), "vanilla NPC zone protected");
Check(!Policy.MayClaim(false, false, false, false, true, 500, 250), "expansion bounded");
Check(Policy.MayClaim(false, false, false, false, true, 80, 250), "valid nearby site accepted");
Check(Policy.SquadAction(true, false, false, false, false, true, false, false) == "return", "danger interrupts work");
Check(Policy.SquadAction(false, false, true, false, false, true, false, false) == "return", "wounded squad retreats");
Check(Policy.SquadAction(false, false, false, true, false, false, false, true) == "recover", "unarmed squad stays home");
Check(Policy.SquadAction(false, true, false, false, false, true, false, false) == "return", "night recalls squad");
Check(Policy.SquadAction(false, false, false, false, true, false, false, false) == "return", "cargo returns before new scavenge");
Check(Policy.SquadAction(false, false, false, false, false, true, false, false) == "continue", "existing order preserved after reattachment");
Check(Policy.SquadAction(false, false, false, false, false, false, true, true) == "defend", "home defense takes precedence");
Check(Policy.SquadAction(false, false, false, false, false, false, false, false) == "scavenge", "daytime idle squad searches");
Check(Policy.Allocate(4, new[] {2,2,4}, new[] {90,60,50}).SequenceEqual(new[] {2,2,0}), "food and construction share small population");
Check(Policy.Allocate(3, new[] {3,3}, new[] {60,60}).SequenceEqual(new[] {2,1}), "equal priority jobs share workers");
Check(Policy.Allocate(9, new[] {2,2}, new[] {0,100}).SequenceEqual(new[] {0,2}), "blocked job receives no workers and capacity respected");
for (int n = 0; n < 100; n++)
{
    var r = Policy.Allocate(n, new[] {2,5,3}, new[] {90,60,50});
    if (r.Sum() > n || r.Where((v,i) => v > new[] {2,5,3}[i] || v < 0).Any()) throw new Exception("worker conservation");
}
Check(true, "worker conservation across 100 population sizes");
Check(Policy.RetryAt(10, 1) == 10.5 && Policy.RetryAt(10, 9) == 22, "retry backs off and is bounded");
Check(Policy.ThreatNear(false, 120, 180), "visible infected near home is a threat");
Check(!Policy.ThreatNear(true, 120, 180), "hiding lair group is not a standing threat");
Check(!Policy.ThreatNear(false, 260, 180), "distant infected outside radius is not a home threat");
Check(Policy.Engage(1f, 4, 8), "healthy squad may fight a 2x group");
Check(!Policy.Engage(1f, 4, 12), "heavily outnumbered squad does not engage");
Check(!Policy.Engage(.55f, 4, 3), "wounded squad does not start a fight");

var root = Path.GetFullPath(args.Length > 0 ? args[0] : ".");
using var coop = AssemblyDefinition.ReadAssembly(Path.Combine(root, ".local/coop-inspect-rc7-p7/IFZ.Multiplayer.GameIntegration.dll"));
TypeDefinition Type(string name) => coop.MainModule.Types.Single(t => t.FullName == "IFZ.Multiplayer.GameIntegration." + name);
void Method(string type, string method, int parameters)
{
    var m = Type(type).Methods.Single(m => m.Name == method && m.Parameters.Count == parameters);
    Check(m.IsStatic, type + "." + method + " metadata contract");
}
Method("NpcBrainService", "Update", 1); Method("NpcBrainService", "TickFaction", 2);
Method("NpcLifecycleService", "BootstrapIfNeeded", 1);
Method("Patches.EconomyPartition", "TryGetExplicitBuildingOwner", 2);
Method("Patches.EconomyPartition", "GetContainer", 2);
Method("OwnershipRegistry", "TryGetExplicitOwner", 2);
Method("SettlementFounder", "RegisterBuildingOwnership", 3);
Method("Patches.NpcOrderContext", "Enter", 0);
Method("Patches.EconomyPartition", "PushOwnerContext", 1);
Method("Patches.EconomyPartition", "PopOwnerContext", 0);
// 0.2.0: worker ownership must be read through the mod's own Character overload (group -> id -> house).
Check(Type("Patches.EconomyPartition").Methods.Any(m => m.Name == "GetOwner" && m.Parameters.Count == 1 &&
    m.Parameters[0].ParameterType.Name == "Character"), "EconomyPartition.GetOwner(Character) overload contract");
foreach (var (type, member) in new[] { ("GameServices","Container"), ("GameServices","GameplayReady"),
    ("NpcLifecycleService","All"), ("NpcSessionSettings","Count"), ("CampaignPersistence","CampaignId"),
    ("CampaignPersistence","PendingLoadedCampaign"), ("Patches.EconomyPartition","Enabled"), ("MpRuntime","Host") })
    Check(Type(type).Fields.Any(f => f.Name == member) || Type(type).Properties.Any(p => p.Name == member), type + "." + member + " member contract");
using var plugin = AssemblyDefinition.ReadAssembly(Path.Combine(root, "npc-enhanced/bin/Release/netstandard2.1/IfzNpcEnhanced.dll"));
using var game = AssemblyDefinition.ReadAssembly(Path.Combine(root, ".local/game/Infection Free Zone_Data/Managed/Ifz.dll"));
var handlers = game.MainModule.Types.Single(t => t.FullName == "Gameplay.Units.Player.OrdersHandlers");
Check(handlers.Fields.Any(f => f.Name == "_group" && f.FieldType.FullName == "Gameplay.Units.Group"), "native order handler group contract");
Check(handlers.Methods.Any(m => m.Name == "AddOrderMoveToNearestStockroom" && m.Parameters.Count == 0), "native automatic return hook contract");
var moveOrder = game.MainModule.Types.Single(t => t.FullName == "Gameplay.Units.Orders.MoveToBuildingOrder");
Check(moveOrder.Fields.Any(f => f.Name == "_isForExchange" && f.FieldType.FullName == "System.Boolean"), "explicit exchange flag contract");
Check(moveOrder.Methods.Any(m => m.Name == "StartOrder" && m.Parameters.Count == 0) && moveOrder.Methods.Any(m => m.Name == "GetState" && m.Parameters.Count == 0), "foreign depot order rejection hook contract");
Check(game.MainModule.Types.Single(t => t.FullName == "Analytics.AnalyticsSender").Methods.Any(m => m.Name == "OnMissionStart" && m.Parameters.Count == 1 && !m.Parameters[0].ParameterType.IsValueType), "nullable mission analytics hook contract");
var workBase = game.MainModule.Types.Single(t => t.FullName == "Gameplay.Units.Player.Workers.WorkSystem.WorkBase");
Check(workBase.Properties.Any(p => p.Name == "MaxWorkers" && p.SetMethod != null), "workplace staffing cap contract");
var workController = game.MainModule.Types.Single(t => t.FullName == "Gameplay.Units.Workers.WorkSystem.WorkController");
Check(workController.Properties.Any(p => p.Name == "Works"), "workplace list contract");
// 0.2.0: the citizens/employment panel must be able to show host-only numbers.
var panel = game.MainModule.Types.Single(t => t.FullName == "WorkersPanelController");
Check(panel.Fields.Any(f => f.Name == "_labels") && panel.Fields.Any(f => f.Name == "unemployedCount"), "citizens panel count fields contract");
Check(panel.Methods.Any(m => m.Name == "SetAllCitizensCount" && m.Parameters.Count == 1) &&
    panel.Methods.Any(m => m.Name == "SetUnemployedCount" && m.Parameters.Count == 1) &&
    panel.Methods.Any(m => m.Name == "SetSquadMembers" && m.Parameters.Count == 0) &&
    panel.Methods.Any(m => m.Name == "UpdateUnemployedText" && m.Parameters.Count == 0), "citizens panel update methods contract");
Check(!plugin.MainModule.AssemblyReferences.Any(a => a.Name.StartsWith("IFZ.Multiplayer")), "addon has no hard dependency on private mod types");
Console.WriteLine($"{count} checks passed. Static/policy tests only; game simulation not executed.");
