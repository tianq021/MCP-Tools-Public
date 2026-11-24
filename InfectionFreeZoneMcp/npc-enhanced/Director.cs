using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Controllers;
using Controllers.Time;
using Data.Building;
using Data.Resorces;
using Gameplay.Buildings;
using Gameplay.GameResources;
using Gameplay.Rebuilding;
using Gameplay.Units;
using Gameplay.Units.Characters;
using Gameplay.Units.Enemy;
using Gameplay.Units.Orders;
using Gameplay.Units.Player.Workers;
using Gameplay.Units.Player.Workers.WorkSystem;
using Gameplay.Units.Workers.WorkSystem;
using Newtonsoft.Json;
using StoresHelper;
using UnityEngine;
using Zenject;

namespace IfzNpcEnhanced;

internal sealed partial class Director
{
    readonly Plugin plugin;
    readonly CoopAdapter adapter;
    readonly object owner;
    readonly string name, hqId;
    readonly Dictionary<string, Job> jobs = new();
    readonly Dictionary<string, Failure> blocked = new();
    readonly Dictionary<string, int> workTargets = new();
    readonly Queue<string> recent = new();
    readonly HashSet<string> recovery = new();
    float nextTick, nextEconomy, nextReport;
    double nextBuild;
    int errorCount;
    public ulong FactionId { get; }
    public string Campaign { get; }
    public string Summary { get; private set; }
    static readonly HashSet<int> Food = new() { 1, 11, 12, 33, 34, 48, 75 };
    sealed class Job
    {
        internal Order Order;
        internal string Kind, Target;
        internal Vector3 Position;
        internal float Cargo, LastCargo, Progress;
        internal double LastProgress;
    }
    sealed class Failure { internal int Count; internal double Until; }
    public Director(Plugin plugin, CoopAdapter adapter, object record)
    {
        this.plugin = plugin; this.adapter = adapter; owner = adapter.Owner(record);
        FactionId = CoopAdapter.Id(owner); Campaign = adapter.Campaign;
        name = (string)CoopAdapter.Get(record, "Name"); hqId = (string)CoopAdapter.Get(record, "HqBuildingId");
        Note("Attached to " + name + " [" + FactionId + "]. Existing native orders are preserved.");
    }
    double Hours => TimeController.Day * 24.0 + TimeController.Hour;
    void Note(string text)
    {
        text = $"D{TimeController.Day} {TimeController.Hour:F1}h {text}";
        recent.Enqueue(text); while (recent.Count > 12) recent.Dequeue();
        plugin.Log("[NPC " + FactionId + "] " + text);
    }
    bool Cooling(string key) => blocked.TryGetValue(key, out var f) && Hours < f.Until;
    void Failed(string key, string reason)
    {
        if (!blocked.TryGetValue(key, out var f)) blocked[key] = f = new Failure();
        f.Until = Policy.RetryAt(Hours, ++f.Count);
        Note(reason + "; retry after " + f.Until.ToString("F1") + " game hours");
    }
    static float Distance(Vector3 a, Vector3 b) => Vector2.Distance(new Vector2(a.x, a.z), new Vector2(b.x, b.z));
    bool Owned(Structure structure) => adapter.OwnerOf(structure) == FactionId;
    bool SafeUnclaimed(Building building) => adapter.OwnerOf(building) == 0 && adapter.OwnerOfEntity(building.Id) == 0 &&
        !building.IsClaimedByPlayer && !building.IsHq && !building.IsNpcStructure;
    static float Cargo(Group group)
    {
        var total = group.ResourcesContainer?.Resources.Sum(p => p.Value) ?? 0;
        foreach (var c in group.Characters)
            if (c.ResourceInHandModule.HaveResource()) total += c.ResourceInHandModule.Resource.ResourceCrate.Quantity;
        var trunk = group.VehicleHandler?.Vehicle?.Trunk;
        if (trunk != null && !ReferenceEquals(trunk, group.ResourcesContainer)) total += trunk.Resources.Sum(p => p.Value);
        return total;
    }
    static float FoodAmount(ResourcesContainer stock) => stock.Resources.Where(p => Food.Contains((int)p.Key)).Sum(p => p.Value);
    bool Active(Job job, Group squad) => ReferenceEquals(job.Order, squad.OrdersHandlers.OrdersQueue.CurrentOrder) ||
        squad.OrdersHandlers.OrdersQueue.Orders.Contains(job.Order);

    public void Tick()
    {
        if (Time.realtimeSinceStartup < nextTick) return;
        nextTick = Time.realtimeSinceStartup + 2;
        try
        {
            var container = adapter.Container;
            var buildings = container.Resolve<BuildingsController>().Buildings;
            var hq = buildings.FirstOrDefault(b => b != null && b.Id == hqId);
            if (hq == null || hq.IsRuined || !Owned(hq)) { Summary = name + "：总部失效或被摧毁，已停止新订单。"; return; }
            var stock = adapter.Stock(owner);
            if (stock == null) { Summary = name + "：等待阵营库存恢复。"; return; }
            var squads = container.Resolve<SquadsController>().Squads
                .Where(s => s != null && !s.IsDestroying && adapter.OwnerOfEntity(s.Id) == FactionId).ToArray();
            var citizens = container.Resolve<WorkersController>().Workers
                .Where(w => w != null && !w.IsDead() && adapter.OwnerOf(w) == FactionId).ToArray();
            // Every currently hostile group (infected, bandits, hostile army/provirus), not just infected.
            var enemies = container.Resolve<GroupsController>().Groups
                .Where(g => g != null && !g.IsDestroying && !g.IsEmpty() && g.AffiliationProvider.Get(Fraction.Player) == Affiliation.Hostile).ToArray();
            var threat = enemies.Any(g => Policy.ThreatNear(g.IsHiding(), Distance(g.Position, hq.CenterPosition), 180));
            var food = FoodAmount(stock);
            using (adapter.Scope(owner))
            {
                // AI takeover keeps the local director as a reporting/observer layer only.
                if (!manualMode)
                {
                    foreach (var squad in squads) RunSquad(squad, hq, buildings, enemies, threat, food < Math.Max(6, citizens.Length));
                    if (Time.realtimeSinceStartup >= nextEconomy)
                    {
                        nextEconomy = Time.realtimeSinceStartup + 10;
                        Production(container, buildings, stock, citizens.Length);
                        Workforce(container, citizens, threat, food < Math.Max(6, citizens.Length),
                            citizens.Any(w => w.IsSick || w.Health.Hp < w.Health.MaxHp) || squads.Any(s => recovery.Contains(s.Id)));
                        if (allowExpansion && focus != "defense" && !threat && !TimeController.IsNight() && Hours >= nextBuild)
                        {
                            nextBuild = Hours + 0.5;
                            Expand(container, buildings, hq, stock, citizens.Length);
                        }
                    }
                }
            }
            var host = HqController.MainHeadquarter;
            var offset = host == null ? Vector3.zero : hq.CenterPosition - host.Position;
            Summary = $"{name}  [阵营 {FactionId}]\n总部坐标：x {hq.CenterPosition.x:F0}，z {hq.CenterPosition.z:F0}\n" +
                $"相对你的总部：向东 {offset.x:F0}米，向北 {offset.z:F0}米\n工人 {citizens.Length} | 小队 {squads.Length} | 食物 {food:F1}\n" +
                $"{(manualMode ? "AI 接管" : threat ? "组织防守" : TimeController.IsNight() ? "夜间守卫" : "自主发展")} | 行动 {jobs.Count} | 异常 {errorCount}\n" + recent.LastOrDefault();
            if (Time.realtimeSinceStartup >= nextReport)
            {
                nextReport = Time.realtimeSinceStartup + 15;
                WriteReport(hq, stock, citizens, squads, threat, container);
            }
        }
        catch (Exception ex)
        {
            errorCount++; nextTick = Time.realtimeSinceStartup + Math.Min(60, errorCount * 5);
            Summary = name + "：发生异常，稍后重试：" + ex.GetBaseException().Message;
            plugin.Error(ex);
        }
    }

    void RunSquad(Group squad, Building hq, IList<Building> buildings, Group[] enemies, bool threat, bool needFood)
    {
        if (squad.IsOnExpedition() || squad.Characters.Count == 0) return;
        var atHome = ReferenceEquals(squad.Enterable, hq) || Distance(squad.Position, hq.CenterPosition) < 30;
        var hp = squad.Characters.Where(c => !c.IsDead()).Select(c => c.Health.Hp / Math.Max(1f, c.Health.MaxHp)).DefaultIfEmpty(0).Min();
        if (hp < .6f) recovery.Add(squad.Id);
        if (hp >= .9f) recovery.Remove(squad.Id);
        var armed = squad.Characters.Any(c => c.ItemsContainer?.WeaponHandler?.CurrentWeapon != null &&
            (c.ItemsContainer.AmmunitionHandler?.Ammunition?.Quantity ?? 0) > 0);
        if (atHome)
        {
            foreach (var member in squad.Characters.Where(c => !c.IsDead()))
            {
                var handler = member.ItemsContainer?.WeaponHandler;
                var stock = adapter.Stock(owner);
                if (handler?.CurrentWeapon?.IsMelee == true)
                {
                    var weapon = stock.Resources.Where(p => p.Value >= 1 && ResourcesDataContainer.Instance.IsWeapon(p.Key))
                        .Select(p => ResourcesDataContainer.Instance.GetWeaponById(p.Key))
                        .FirstOrDefault(w => w != null && !w.IsMelee && !w.AccessoryUseOnly &&
                            w.AmmoDraft != null && stock.GetAvailableResourceQuantity(w.AmmoDraft.ResourceType) > 0);
                    if (weapon != null && stock.TryRemoveResource(weapon.ResourceType, 1)) handler.AssignWeapon(weapon);
                }
                member.ItemsContainer?.AmmunitionHandler?.RefillAmmoFromContainer(adapter.Stock(owner));
            }
            armed = squad.Characters.Any(c => (c.ItemsContainer?.AmmunitionHandler?.Ammunition?.Quantity ?? 0) > 0);
        }
        var danger = enemies.Where(e => !e.IsHiding() && Distance(e.Position, squad.Position) < 70).Sum(e => e.Characters.Count) > squad.Characters.Count * 3;
        if (ManualActive(squad) && !danger && !recovery.Contains(squad.Id) && !threat && !TimeController.IsNight()) return;
        if (danger || recovery.Contains(squad.Id) || threat || TimeController.IsNight()) manual.Remove(squad.Id);
        if (jobs.TryGetValue(squad.Id, out var job))
        {
            if (!Active(job, squad))
            {
                jobs.Remove(squad.Id);
                if (job.Kind == "scavenge" && Cargo(squad) <= job.Cargo)
                    Failed(job.Target, "Scavenge ended without cargo: " + job.Target);
                else if (job.Kind == "return" && Cargo(squad) > 0)
                    Failed("return:" + squad.Id, "Return ended with cargo still aboard");
                else Note(job.Kind + " finished for squad " + squad.Id + "; cargo=" + Cargo(squad));
            }
            else
            {
                var target = buildings.FirstOrDefault(b => b != null && b.Id == job.Target);
                var progress = target?.ScavengeWork?.TimeLeftToScavenge ?? 0;
                if (Distance(job.Position, squad.Position) > 2 || Math.Abs(progress - job.Progress) > .1f || Math.Abs(Cargo(squad) - job.LastCargo) > .01f)
                { job.LastProgress = Hours; job.Position = squad.Position; job.Progress = progress; job.LastCargo = Cargo(squad); }
                if (Hours - job.LastProgress > 4)
                {
                    Failed(job.Target, "Stalled " + job.Kind + " for squad " + squad.Id);
                    Submit(squad, new MoveToBuildingOrder(hq), "return", hq.Id, true);
                    return;
                }
            }
        }
        var action = Policy.SquadAction(danger, TimeController.IsNight() || focus == "defense", recovery.Contains(squad.Id), !armed,
            Cargo(squad) > 0, !squad.OrdersHandlers.HasNoOrder(), threat, atHome);
        if ((action == "recover" || action == "guard") && !ReferenceEquals(squad.Enterable, hq) &&
            (!jobs.TryGetValue(squad.Id, out var homeJob) || homeJob.Kind != "return")) action = "return";
        if (action == "return")
        {
            if (!Cooling("return:" + squad.Id) && (!jobs.TryGetValue(squad.Id, out var current) || current.Kind != "return"))
                Submit(squad, new MoveToBuildingOrder(hq), "return", hq.Id, true);
            return;
        }
        if (action == "defend")
        {
            if (!squad.OrdersHandlers.HasNoOrder()) return;
            var enemy = enemies.Where(e => Distance(e.Position, hq.CenterPosition) < 100 && e.CanBeAttacked() && squad.EnemiesProvider.IsEnemy(e))
                .OrderBy(e => Distance(e.Position, squad.Position))
                .FirstOrDefault(e => Policy.Engage(hp, squad.Characters.Count, e.Characters.Count));
            if (enemy != null) Submit(squad, new AttackGroupOrder(enemy), "defend", enemy.Id, false);
            return;
        }
        if (action != "scavenge") return;
        var reservations = new HashSet<string>(jobs.Values.Where(j => j.Kind == "scavenge").Select(j => j.Target));
        var choices = buildings.Where(b => b != null && SafeUnclaimed(b) && !Cooling(b.Id) && !reservations.Contains(b.Id) &&
            b.CanScavenge() && !b.IsScavenged() && !b.IsStockroom() && Distance(b.CenterPosition, hq.CenterPosition) < searchRadius &&
            !enemies.Any(e => !e.IsHiding() && Distance(e.Position, b.CenterPosition) < 100))
            .OrderBy(b => Distance(squad.Position, b.CenterPosition) / FoodPreference(b, needFood || focus == "food")).Take(24);
        foreach (var target in choices)
        {
            if (!squad.CanGoToStructure(target)) { Failed(target.Id, "Unreachable scavenge building " + target.Id); continue; }
            Submit(squad, new MoveToBuildingOrder(target), "scavenge", target.Id, false); return;
        }
    }
    static float FoodPreference(Building b, bool needFood)
    {
        if (!needFood) return 1;
        // Only inspect already generated loot. Do not generate/reveal resources for planning.
        return b.ScavengeWork?.ResourcesToFind?.Any(r => r != null && Food.Contains((int)r.ResourceID) && r.Quantity > 0) == true ? 3 : 1;
    }
    void Submit(Group squad, Order order, string kind, string target, bool replace)
    {
        squad.OrdersHandlers.GiveOrder(order, clearOrders: replace);
        var job = new Job { Order = order, Kind = kind, Target = target, Position = squad.Position, Cargo = Cargo(squad), LastCargo = Cargo(squad), LastProgress = Hours };
        if (!Active(job, squad)) { order.Dispose(); Failed(target, "Order rejected: " + kind); return; }
        jobs[squad.Id] = job;
        Note($"小队 {squad.Id}：{(kind == "scavenge" ? "搜刮" : kind == "return" ? "回仓" : "防守")} → {target}");
    }

    float ResourceValue(ResourceID id, ResourcesContainer stock, int population)
    {
        if (Food.Contains((int)id)) return FoodAmount(stock) < Math.Max(10, population * 2) ? 12 : focus == "food" ? 6 : 2;
        if (focus == "materials" && ((int)id == 4 || (int)id == 5 || (int)id == 6)) return stock.GetResourceQuantity(id) < 60 ? 8 : 1;
        if ((int)id == 8) return stock.GetResourceQuantity(id) < 4 ? 6 : .2f;
        return stock.GetResourceQuantity(id) < 10 ? 4 : stock.GetResourceQuantity(id) < 25 ? 1 : .1f;
    }
    float RecipeScore(ProductionData recipe, ResourcesContainer stock, int population)
    {
        if (!SaveHandler.SaveData.ContentStateController.IsContentUnlocked(recipe.Id)) return -1;
        if (recipe.GetCycleCost().Any(p => stock.GetAvailableResourceQuantity(p.Key) < p.Value)) return -1;
        var benefit = recipe.GetCycleProfit().Sum(p => p.Value * ResourceValue(p.Key, stock, population));
        var cost = recipe.GetCycleCost().Sum(p => p.Value * ResourceValue(p.Key, stock, population));
        return benefit - cost * .5f;
    }
    void Production(DiContainer container, IList<Building> buildings, ResourcesContainer stock, int population)
    {
        foreach (var b in buildings.Where(b => b != null && Owned(b) && b.IsDone && b.ProductionWork != null))
        {
            if (Locked(recipeLocks, b.Id)) continue;
            var work = b.ProductionWork;
            var recipes = b.Draft.ProductionsData?.productions;
            if (recipes == null) continue;
            var best = recipes.Select(r => new { Recipe = r, Score = RecipeScore(r, stock, population) })
                .Where(r => r.Score > 0).OrderByDescending(r => r.Score).FirstOrDefault();
            if (best == null || work.NextProductionData != null || work.ProductionData == best.Recipe) continue;
            var currentScore = work.ProductionData == null ? -1 : RecipeScore(work.ProductionData, stock, population);
            if (best.Score <= currentScore * 1.25f) continue;
            work.ChangeProduction(best.Recipe);
            Note("生产设施 " + b.Id + " 切换配方：" + best.Recipe.Id);
        }
    }
    void Workforce(DiContainer container, Character[] citizens, bool threat, bool lowFood, bool needsHealth)
    {
        var controller = container.Resolve<WorkController>();
        var works = controller.Works.Where(w => !w.IsDone && w.InitialMaxWorkers > 0 && Owned(w.GetRelatedStructure()) && !w.IsPaused).ToArray();
        var priorities = works.Select(w =>
        {
            var s = w.GetRelatedStructure();
            var can = w.CanExecute();
            if (!can) return 0;
            if (s.DefenseWork == w) return threat ? 100 : 10;
            if (threat) return 0;
            if (s.TreatmentSubWork != null || s.HealingSubWork != null) return needsHealth ? 80 : 0;
            if (!s.IsDone) return 60;
            if (s.ProductionWork == w)
                return s.ProductionWork.ProductionData?.GetCycleProfit().Any(p => Food.Contains((int)p.Key)) == true ? (lowFood ? 90 : 65) : 50;
            return 20;
        }).ToArray();
        var able = citizens.Count(c => !c.IsSick && !c.IsUnderSquadProduction);
        var target = Policy.Allocate(able, works.Select(w => Math.Min(threat ? 4 : 2, w.InitialMaxWorkers)).ToArray(), priorities);
        // AI-issued staffing choices win for their hold window; the director only fills the rest.
        for (var i = 0; i < works.Length; i++)
            if (Locked(staffLocks, works[i].GetRelatedStructure().GetId())) target[i] = works[i].Workers.Count;
        // Release surplus first; never invoke global priority realignment or change player-wide management mode.
        for (var i = 0; i < works.Length; i++)
        {
            var work = works[i];
            while (work.Workers.Count > target[i])
            {
                var before = work.Workers.Count;
                var worker = work.Workers.FirstOrDefault(c => adapter.OwnerOf(c) == FactionId);
                if (worker == null) break;
                worker.WorkModule.UnassignWork(areaWork: true);
                if (work.Workers.Count >= before) break;
            }
            work.MaxWorkers = target[i];
        }
        for (var i = 0; i < works.Length; i++)
        {
            var work = works[i];
            if (target[i] <= 0) continue;
            work.IsSuspended = false;
            foreach (var worker in controller.AvailableWorkers.Where(c => adapter.OwnerOf(c) == FactionId && !c.IsSick && !c.IsUnderSquadProduction)
                .OrderBy(c => Distance(c.Position, work.GetPosition())).ToArray())
            {
                if (work.Workers.Count >= target[i]) break;
                work.AddWorker(worker);
            }
        }
        workTargets.Clear();
        for (var i = 0; i < works.Length; i++) workTargets[works[i].GetRelatedStructure().GetId() + ":" + works[i].GetType().Name] = target[i];
    }

    IEnumerable<PlaceableObjectDraft> Drafts()
    {
        var data = BuildingsDatas.Instance;
        for (var i = 0; i < data.BuildingMenuSectionCount; i++)
        {
            var section = data.GetBuildingMenuSection(i);
            for (var j = 0; j < section.PlaceableObjectCount; j++)
            {
                var d = section.GetPlaceableObject(j);
                if (d.PlaceableObjectType != PlaceableObjectType.AdoptBuilding || d.IsHq || d.HasCarWorkshopModule) continue;
                if (d.IsUnlockedByDLC && !DlcHelper.IsInstalled(d.UnlockingDLC)) continue;
                if (SaveHandler.SaveData.ContentStateController.IsContent(d.Id, out var state) && !state.Unlocked) continue;
                yield return d;
            }
        }
    }
    void Expand(DiContainer container, IList<Building> buildings, Building hq, ResourcesContainer stock, int population)
    {
        if (population == 0 || Cooling("build")) return;
        var owned = buildings.Where(b => b != null && Owned(b)).ToArray();
        // One project at a time, including after load, inferred from the native construction work.
        if (container.Resolve<WorkController>().Works.Any(w => !w.IsDone && Owned(w.GetRelatedStructure()) && !w.GetRelatedStructure().IsDone)) return;
        var housing = owned.Where(b => b.Draft != null && b.Draft.HasLivingQuartersModule)
            .Sum(b => b.Draft.GetLivingCapacity(b.Volume, 1f));
        var choices = Drafts().Select(d => new
        {
            Draft = d,
            Score = d.HasLivingQuartersModule ? (housing < population ? 150f : -1) :
                d.HasStorageModule ? (stock.Capacity > 0 && stock.GetCratesCount() > stock.Capacity * .8f ? 140 : -1) :
                (d.ProductionsData?.productions?.Select(r => RecipeScore(r, stock, population)).DefaultIfEmpty(-1).Max() ?? -1) /
                    (1 + owned.Count(b => b.Draft?.Id == d.Id) * 3)
        }).Where(x => x.Score > 0 && owned.Count(b => b.Draft?.Id == x.Draft.Id) < 2).OrderByDescending(x => x.Score).ToArray();
        foreach (var choice in choices)
        foreach (var b in buildings.Where(b => b != null && !Cooling(b.Id) && SafeUnclaimed(b) &&
            (int)b.MarkedAsPartOfZoneType == 0 &&
            !buildings.Any(other => other != null && other != b && other.IsHq && !Owned(other) && Distance(other.Position, b.CenterPosition) < 250) &&
            Policy.MayClaim(false, b.IsClaimedByPlayer, b.IsHq, b.IsNpcStructure, b.CanAdapt, Distance(b.CenterPosition, hq.CenterPosition), 250))
            .OrderBy(b => b.Volume).Take(16))
        {
            var costs = new Dictionary<ResourceID, int>();
            Building.GetBuildingCostForDraft(b.Volume, choice.Draft, ref costs);
            if (costs.Any(p => stock.GetAvailableResourceQuantity(p.Key) < p.Value)) continue;
            // Claim BEFORE native StartAdapt creates work: synchronous worker/resource hooks need the correct owner.
            adapter.Claim(b, owner);
            try
            {
                container.Resolve<AdaptController>().StartFullAdapt(b, choice.Draft);
                Note("已安排施工：" + choice.Draft.Id + "，建筑 " + b.Id + "；等待工人和材料");
                nextBuild = Hours + 2;
            }
            catch
            {
                // Ownership stays reserved if StartAdapt partially mutated the building. Do not replay or silently release it.
                Failed("build", "Construction result uncertain at " + b.Id + "; retained ownership, inspect log");
                throw;
            }
            return;
        }
    }
    void WriteReport(Building hq, ResourcesContainer stock, Character[] workers, Group[] squads, bool threat, DiContainer container)
    {
        var report = new
        {
            version = "0.2.0", campaign = Campaign, faction_id = FactionId.ToString(), faction_name = name,
            day = TimeController.Day, hour = TimeController.Hour, sampled_utc = DateTime.UtcNow,
            hq = new { id = hq.Id, x = hq.CenterPosition.x, z = hq.CenterPosition.z },
            workers = workers.Length, food = FoodAmount(stock), threat, errors = errorCount,
            mode = manualMode ? "ai" : "auto",
            resources = stock.Resources.ToDictionary(p => p.Key.ToString(), p => p.Value),
            squads = squads.Select(s => new { id = s.Id, cargo = Cargo(s), order = s.OrdersHandlers.OrdersQueue.CurrentOrder?.GetType().Name,
                target_building_id = (s.OrdersHandlers.OrdersQueue.CurrentOrder as MoveToBuildingOrder)?.Building?.Id,
                target_owner = (s.OrdersHandlers.OrdersQueue.CurrentOrder is MoveToBuildingOrder move && move.Building != null)
                    ? adapter.OwnerOf(move.Building).ToString() : null }),
            tasks = container.Resolve<WorkController>().Works.Where(w => !w.IsDone && Owned(w.GetRelatedStructure()))
                .Select(w => new { building = w.GetRelatedStructure().GetId(), type = w.GetType().Name, workers = w.Workers.Count,
                    target = w.MaxWorkers, progress = w.Progress, executable = w.CanExecute(), suspended = w.IsSuspended }),
            recent = recent.ToArray(), model_calls = 0
        };
        var path = Path.Combine(plugin.DataDirectory, "status.json");
        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonConvert.SerializeObject(report, Formatting.Indented));
        if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path);
    }
}
