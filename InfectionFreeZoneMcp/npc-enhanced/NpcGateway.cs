using System;
using System.Collections.Generic;
using System.Linq;
using Controllers;
using Controllers.Time;
using Data.Building;
using Data.Resorces;
using Gameplay.Buildings;
using Gameplay.GameResources;
using Gameplay.Production;
using Gameplay.Rebuilding;
using Gameplay.Units;
using Gameplay.Units.Characters;
using Gameplay.Units.Enemy;
using Gameplay.Units.Orders;
using Gameplay.Units.Player.Workers;
using Gameplay.Units.Player.Workers.WorkSystem;
using Gameplay.Units.Workers.WorkSystem;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using Zenject;

namespace IfzNpcEnhanced;

internal sealed class NpcFault : Exception
{
    internal readonly string Code;
    internal NpcFault(string code, string message) : base(message) { Code = code; }
}

// Called by IFZ MCP on the Unity thread. No separate listener or access to the player's controls.
public static class NpcGateway
{
    public static string Invoke(string method, string input)
    {
        try
        {
            var args = JObject.Parse(input);
            var p = Plugin.Instance;
            if (p?.Adapter == null || !p.Adapter.Ready || SaveHandler.LoadingData)
                throw new NpcFault("npc_not_ready", "联机阵营尚未就绪。");
            var a = p.Adapter;
            object result;
            if (method == "npc_list_factions")
                result = new { campaign = a.Campaign, selected_faction_id = p.SelectedFaction, factions = a.Records.Select(r => new {
                    faction_id = CoopAdapter.Id(a.Owner(r)).ToString(), name = CoopAdapter.Get(r, "Name"),
                    founded = CoopAdapter.Get(r, "Founded"), hq_id = CoopAdapter.Get(r, "HqBuildingId"),
                    controllable = CoopAdapter.Id(a.Owner(r)).ToString() == p.SelectedFaction }) };
            else
            {
                var d = p.McpDirector((string)args["faction_id"]);
                result = method == "npc_get_faction" ? d.ReadFaction() : method == "npc_command" ? d.Command(args) :
                    throw new NpcFault("unknown_method", "未知 NPC 接口。");
            }
            var json = JObject.FromObject(result); json["ok"] = true;
            return json.ToString(Formatting.None);
        }
        catch (NpcFault ex) { return JsonConvert.SerializeObject(new { ok = false, code = ex.Code, message = ex.Message }); }
    }
}

internal sealed partial class Director
{
    string focus = "balanced";
    bool allowExpansion = true;
    // AI takeover: the local economy/squad scheduler stops issuing orders and only reports.
    // Safety hooks (foreign depot rejection, native combat, starvation) stay active.
    bool manualMode;
    float searchRadius = 800;
    readonly Dictionary<string, double> manual = new();
    readonly Dictionary<string, double> recipeLocks = new();
    readonly Dictionary<string, double> staffLocks = new();
    readonly Dictionary<string, (string input, object result)> receipts = new();
    readonly Queue<string> receiptOrder = new();
    static readonly string[] SquadActions = { "scavenge", "move", "return", "auto", "attack" };

    bool ManualActive(Group squad)
    {
        if (!manual.TryGetValue(squad.Id, out var until)) return false;
        if (Hours < until && !squad.OrdersHandlers.HasNoOrder()) return true;
        manual.Remove(squad.Id); return false;
    }
    bool Locked(Dictionary<string, double> locks, string id) => locks.TryGetValue(id, out var until) && Hours < until;
    Building Home() => adapter.Container.Resolve<BuildingsController>().Buildings.FirstOrDefault(b => b != null && b.Id == hqId && !b.IsRuined && Owned(b))
        ?? throw new NpcFault("hq_unavailable", "NPC 总部不可用。");
    Group OwnSquad(string id) => adapter.Container.Resolve<SquadsController>().Squads.FirstOrDefault(s =>
        s != null && !s.IsDestroying && s.Id == id && adapter.OwnerOfEntity(s.Id) == FactionId)
        ?? throw new NpcFault("squad_forbidden", "该小队不属于指定 NPC 阵营。");
    Building OwnedBuilding(string id) => adapter.Container.Resolve<BuildingsController>().Buildings.FirstOrDefault(b =>
        b != null && b.Id == id && Owned(b)) ?? throw new NpcFault("target_forbidden", "该建筑不属于指定 NPC 阵营。");
    WorkBase OwnedWork(string buildingId, Func<WorkBase, bool> extra = null) => adapter.Container.Resolve<WorkController>().Works.FirstOrDefault(w =>
        !w.IsDone && w.GetRelatedStructure() != null && w.GetRelatedStructure().GetId() == buildingId &&
        Owned(w.GetRelatedStructure()) && (extra == null || extra(w)))
        ?? throw new NpcFault("target_forbidden", "该建筑没有可操作的岗位。");

    // Full snapshot for the model: everything needed to reason without guessing.
    internal object ReadFaction()
    {
        var hq = Home();
        var c = adapter.Container;
        var stock = adapter.Stock(owner) ?? throw new NpcFault("npc_not_ready", "阵营库存尚未就绪。");
        var buildings = c.Resolve<BuildingsController>().Buildings;
        var squads = c.Resolve<SquadsController>().Squads.Where(s => s != null && !s.IsDestroying && adapter.OwnerOfEntity(s.Id) == FactionId).ToArray();
        var allWorkers = c.Resolve<WorkersController>().Workers.Where(w => w != null && !w.IsDead()).ToArray();
        var workers = allWorkers.Where(w => adapter.OwnerOf(w) == FactionId).ToArray();
        var works = c.Resolve<WorkController>().Works.Where(w => !w.IsDone && w.GetRelatedStructure() != null && Owned(w.GetRelatedStructure())).ToArray();
        var enemies = c.Resolve<GroupsController>().Groups.Where(g => g != null && !g.IsDestroying && !g.IsEmpty() &&
            g.AffiliationProvider.Get(Fraction.Player) == Affiliation.Hostile).ToArray();
        var owned = buildings.Where(b => b != null && Owned(b)).ToArray();
        return new
        {
            faction_id = FactionId.ToString(), campaign = Campaign, name, day = TimeController.Day, hour = TimeController.Hour,
            night = TimeController.IsNight(),
            strategy = new { focus, allow_expansion = allowExpansion, search_radius = searchRadius, mode = manualMode ? "ai" : "auto" },
            headquarters = new { id = hq.Id, x = hq.Position.x, z = hq.Position.z },
            food = FoodAmount(stock),
            resources = stock.Resources.ToDictionary(p => p.Key.ToString(), p => p.Value),
            population = new
            {
                workers_owned = workers.Length, workers_total_world = allWorkers.Length,
                workers = workers.Select(w => new
                {
                    id = w.Id, group_id = w.Group?.Id, hp = w.Health.Hp, sick = w.IsSick,
                    job = w.WorkModule.CurrentWork?.GetType().Name, x = w.Position.x, z = w.Position.z
                }),
                // If workers_owned looks wrong, this block names the exact ownership source for samples.
                unowned_samples = allWorkers.Where(w => adapter.OwnerOf(w) != FactionId).Take(6).Select(w => new
                {
                    id = w.Id, group_id = w.Group?.Id, owner_mod = adapter.OwnerOf(w).ToString(),
                    owner_by_id = adapter.OwnerOfEntity(w.Id).ToString(),
                    owner_by_group = w.Group == null ? null : adapter.OwnerOfEntity(w.Group.Id).ToString(),
                    house_id = (w.HouseData?.House is Structure house) ? house.GetId() : null,
                    owner_by_house = (w.HouseData?.House is Structure house2) ? adapter.OwnerOf(house2).ToString() : null
                })
            },
            squads = squads.Select(s => new
            {
                id = s.Id, x = s.Position.x, z = s.Position.z, members = s.Characters.Count, cargo = Cargo(s),
                hp = s.Characters.Count == 0 ? 0 : s.Characters.Min(m => m.Health.Hp / Math.Max(1f, m.Health.MaxHp)),
                armed = s.Characters.Count(m => (m.ItemsContainer?.AmmunitionHandler?.Ammunition?.Quantity ?? 0) > 0),
                ammo = s.Characters.Sum(m => m.ItemsContainer?.AmmunitionHandler?.Ammunition?.Quantity ?? 0),
                order = s.OrdersHandlers.OrdersQueue.CurrentOrder?.GetType().Name,
                target_building_id = (s.OrdersHandlers.OrdersQueue.CurrentOrder as MoveToBuildingOrder)?.Building?.Id,
                target_owner = (s.OrdersHandlers.OrdersQueue.CurrentOrder is MoveToBuildingOrder move && move.Building != null)
                    ? adapter.OwnerOf(move.Building).ToString() : null,
                manual_until = manual.TryGetValue(s.Id, out var until) ? (double?)until : null
            }),
            works = works.Select(w => new
            {
                building_id = w.GetRelatedStructure().GetId(), type = w.GetType().Name,
                workers = w.Workers.Count, max_workers = w.MaxWorkers, initial_max_workers = w.InitialMaxWorkers,
                executable = w.CanExecute(), suspended = w.IsSuspended, progress = w.Progress,
                staff_locked_until = Locked(staffLocks, w.GetRelatedStructure().GetId()) ? (double?)staffLocks[w.GetRelatedStructure().GetId()] : null,
                x = w.GetPosition().x, z = w.GetPosition().z
            }),
            construction = works.Where(w => !w.GetRelatedStructure().IsDone).Select(w => new
            {
                building_id = w.GetRelatedStructure().GetId(), draft = w.GetRelatedStructure().Draft?.Id,
                progress = w.Progress, executable = w.CanExecute(), workers = w.Workers.Count
            }),
            production = owned.Where(b => b.IsDone && b.ProductionWork != null).Select(b =>
            {
                var recipes = b.Draft?.ProductionsData?.productions;
                return new
                {
                    building_id = b.Id, current = b.ProductionWork.ProductionData?.Id, next = b.ProductionWork.NextProductionData?.Id,
                    progress = b.ProductionWork.Progress,
                    recipe_locked_until = Locked(recipeLocks, b.Id) ? (double?)recipeLocks[b.Id] : null,
                    recipes = recipes?.Select(r => new
                    {
                        id = r.Id, unlocked = SaveHandler.SaveData.ContentStateController.IsContentUnlocked(r.Id),
                        affordable = !r.GetCycleCost().Any(p => stock.GetAvailableResourceQuantity(p.Key) < p.Value),
                        score = RecipeScore(r, stock, workers.Length),
                        cost = r.GetCycleCost().ToDictionary(p => p.Key.ToString(), p => p.Value),
                        profit = r.GetCycleProfit().ToDictionary(p => p.Key.ToString(), p => p.Value)
                    })
                };
            }),
            buildings = owned.Take(100).Select(b => new
            {
                id = b.Id, x = b.Position.x, z = b.Position.z, draft = b.Draft?.Id, complete = b.IsDone,
                workers = b.CurrentWork?.Workers.Count ?? 0, production = b.ProductionWork?.ProductionData?.Id
            }),
            threats = enemies.Select(g => new
            {
                group_id = g.Id, fraction = g.Fraction.ToString(), members = g.Characters.Count, distance = Distance(g.Position, hq.CenterPosition),
                attackable = g.CanBeAttacked(), hiding = g.IsHiding()
            }).Where(g => g.distance < 500).OrderBy(g => g.distance).Take(10),
            build_targets = buildings.Where(b => b != null && SafeUnclaimed(b) && b.CanAdapt &&
                    Distance(b.CenterPosition, hq.CenterPosition) <= 250 && (int)b.MarkedAsPartOfZoneType == 0)
                .OrderBy(b => Distance(b.CenterPosition, hq.CenterPosition)).Take(24)
                .Select(b => new { id = b.Id, x = b.Position.x, z = b.Position.z, volume = b.Volume, distance = Distance(b.CenterPosition, hq.CenterPosition),
                    draft = b.Draft?.Id }),
            scavenge_candidates = buildings.Where(b => b != null && SafeUnclaimed(b) && b.CanScavenge() && !b.IsScavenged() &&
                !b.IsStockroom() && Distance(b.CenterPosition, hq.CenterPosition) <= searchRadius)
                .OrderBy(b => Distance(b.CenterPosition, hq.CenterPosition)).Take(30)
                .Select(b => new { id = b.Id, x = b.Position.x, z = b.Position.z, distance = Distance(b.CenterPosition, hq.CenterPosition) }),
            recent = recent.ToArray(), model_calls = 0
        };
    }

    internal object Command(JObject args)
    {
        if (!Guid.TryParse((string)args["request_id"], out var request)) throw new NpcFault("invalid_request", "request_id 必须为 UUID。");
        var key = request.ToString();
        var normalized = (JObject)args.DeepClone(); normalized.Remove("lease");
        var input = normalized.ToString(Formatting.None);
        if (receipts.TryGetValue(key, out var old))
        {
            if (old.input != input) throw new NpcFault("request_conflict", "相同 request_id 的参数不同。");
            if (old.result == null) throw new NpcFault("result_unknown", "上次结果不确定，请读取阵营状态，不要重复创建订单。");
            return old.result;
        }
        var action = (string)args["action"] ?? "";
        var hq = Home();
        var hold = (double?)args["hold_hours"] ?? 3;
        if (!double.IsFinite(hold) || hold < .25 || hold > 12) throw new NpcFault("invalid_request", "hold_hours 范围 0.25–12。");
        // ---- validation pass: faults here must not poison the receipt table ----
        Group squad = null; Building scavengeTarget = null; Building buildTarget = null; PlaceableObjectDraft draft = null;
        ProductionData recipe = null; WorkBase work = null; Group enemyGroup = null; var workerMax = 0;
        if (action == "strategy")
        {
            if (!new[] { "balanced", "food", "materials", "defense" }.Contains((string)args["focus"])) throw new NpcFault("invalid_request", "无效经营重点。");
            var radius = (float?)args["search_radius"] ?? searchRadius;
            if (!float.IsFinite(radius) || radius < 200 || radius > 1200) throw new NpcFault("invalid_request", "搜索半径范围 200–1200 米。");
        }
        else if (action == "mode")
        {
            if (args["take_over"] == null) throw new NpcFault("invalid_request", "mode 需要 take_over（true=AI 接管，false=恢复本地自动）。");
        }
        else if (action == "workers")
        {
            var building = OwnedBuilding((string)args["building_id"]);
            work = OwnedWork(building.Id);
            workerMax = (int?)args["max_workers"] ?? throw new NpcFault("invalid_request", "workers 需要 max_workers。");
            if (workerMax < 0 || workerMax > work.InitialMaxWorkers) throw new NpcFault("invalid_request", "max_workers 超出岗位容量（0–" + work.InitialMaxWorkers + "）。");
        }
        else if (action == "production")
        {
            var building = OwnedBuilding((string)args["building_id"]);
            var production = building.ProductionWork ?? throw new NpcFault("target_forbidden", "该建筑不是生产设施。");
            work = production;
            recipe = (building.Draft?.ProductionsData?.productions ?? new List<ProductionData>())
                .FirstOrDefault(r => r.Id == (string)args["recipe_id"]) ?? throw new NpcFault("target_forbidden", "该配方不属于此设施。");
            if (!SaveHandler.SaveData.ContentStateController.IsContentUnlocked(recipe.Id)) throw new NpcFault("target_forbidden", "配方尚未解锁。");
            var stock = adapter.Stock(owner) ?? throw new NpcFault("npc_not_ready", "阵营库存尚未就绪。");
            if (recipe.GetCycleCost().Any(p => stock.GetAvailableResourceQuantity(p.Key) < p.Value))
                throw new NpcFault("insufficient_resources", "当前库存不足该配方原料。");
        }
        else if (action == "build")
        {
            if (adapter.Container.Resolve<WorkController>().Works.Any(w => !w.IsDone && w.GetRelatedStructure() != null &&
                Owned(w.GetRelatedStructure()) && !w.GetRelatedStructure().IsDone))
                throw new NpcFault("construction_busy", "已有一个施工中的工程，完成后再下新工程。");
            buildTarget = adapter.Container.Resolve<BuildingsController>().Buildings.FirstOrDefault(b => b != null && b.Id == (string)args["building_id"])
                ?? throw new NpcFault("target_forbidden", "建筑不存在。");
            draft = Drafts().FirstOrDefault(d => d.Id == (string)args["draft_id"]) ?? throw new NpcFault("target_forbidden", "该改造方案不可用或未解锁。");
            if (!SafeUnclaimed(buildTarget) || !buildTarget.CanAdapt ||
                !Policy.MayClaim(false, buildTarget.IsClaimedByPlayer, buildTarget.IsHq, buildTarget.IsNpcStructure, buildTarget.CanAdapt,
                    Distance(buildTarget.CenterPosition, hq.CenterPosition), 250))
                throw new NpcFault("target_forbidden", "目标必须是总部 250 米内无人占有的可改造建筑。");
            var costs = new Dictionary<ResourceID, int>();
            Building.GetBuildingCostForDraft(buildTarget.Volume, draft, ref costs);
            var stock = adapter.Stock(owner) ?? throw new NpcFault("npc_not_ready", "阵营库存尚未就绪。");
            if (costs.Any(p => stock.GetAvailableResourceQuantity(p.Key) < p.Value))
                throw new NpcFault("insufficient_resources", "开工材料不足。");
        }
        else if (SquadActions.Contains(action))
        {
            squad = OwnSquad((string)args["squad_id"]);
            if (action != "auto" && squad.IsOnExpedition()) throw new NpcFault("squad_unavailable", "小队正在远征。");
            if (action == "scavenge")
            {
                scavengeTarget = adapter.Container.Resolve<BuildingsController>().Buildings.FirstOrDefault(b => b != null && b.Id == (string)args["building_id"]);
                if (scavengeTarget == null || !SafeUnclaimed(scavengeTarget) || scavengeTarget.IsStockroom() || !scavengeTarget.CanScavenge() ||
                    scavengeTarget.IsScavenged() || Distance(scavengeTarget.CenterPosition, hq.CenterPosition) > searchRadius || !squad.CanGoToStructure(scavengeTarget))
                    throw new NpcFault("target_forbidden", "目标须为搜索半径内可到达、无人占有且可搜刮的建筑。");
                if (jobs.Values.Any(j => j.Kind == "scavenge" && j.Target == scavengeTarget.Id)) throw new NpcFault("target_reserved", "已有另一支小队搜刮这个目标。");
            }
            else if (action == "move")
            {
                var x = (float?)args["x"]; var z = (float?)args["z"];
                if (!x.HasValue || !z.HasValue || !float.IsFinite(x.Value) || !float.IsFinite(z.Value) ||
                    Distance(new Vector3(x.Value, 0, z.Value), hq.Position) > searchRadius)
                    throw new NpcFault("target_forbidden", "坐标必须在 NPC 总部搜索半径内。");
            }
            else if (action == "attack")
            {
                enemyGroup = adapter.Container.Resolve<GroupsController>().Groups.FirstOrDefault(g => g != null && !g.IsDestroying && !g.IsEmpty() &&
                    g.Id == (string)args["group_id"] && g.AffiliationProvider.Get(Fraction.Player) == Affiliation.Hostile && g.CanBeAttacked() &&
                    squad.EnemiesProvider.IsEnemy(g) && Distance(g.Position, hq.CenterPosition) <= searchRadius)
                    ?? throw new NpcFault("target_forbidden", "目标必须是搜索半径内可攻击的敌对群体（感染者或土匪）。");
            }
        }
        else throw new NpcFault("invalid_request", "未知 NPC 命令。");
        // ---- execution pass ----
        receipts[key] = (input, null); receiptOrder.Enqueue(key);
        if (receiptOrder.Count > 1024) receipts.Remove(receiptOrder.Dequeue());
        using (adapter.Scope(owner))
        {
            if (action == "strategy")
            {
                focus = (string)args["focus"]; allowExpansion = (bool?)args["allow_expansion"] ?? allowExpansion;
                searchRadius = (float?)args["search_radius"] ?? searchRadius;
                nextEconomy = 0; nextBuild = 0;
            }
            else if (action == "mode")
            {
                manualMode = (bool)args["take_over"];
                jobs.Clear(); manual.Clear(); recipeLocks.Clear(); staffLocks.Clear();
                Note("模式切换：" + (manualMode ? "AI 完全接管，本地调度停止下单" : "恢复本地自动经营"));
            }
            else if (action == "workers")
            {
                work.IsSuspended = false;
                work.MaxWorkers = workerMax;
                var id = work.GetRelatedStructure().GetId();
                staffLocks[id] = Hours + hold;
                Note("派工：" + id + " → " + workerMax + " 人");
            }
            else if (action == "production")
            {
                ((ProductionWork)work).ChangeProduction(recipe);
                recipeLocks[work.GetRelatedStructure().GetId()] = Hours + hold;
                Note("生产：" + work.GetRelatedStructure().GetId() + " → " + recipe.Id);
            }
            else if (action == "build")
            {
                adapter.Claim(buildTarget, owner);
                try
                {
                    adapter.Container.Resolve<AdaptController>().StartFullAdapt(buildTarget, draft);
                    Note("施工：" + draft.Id + " → " + buildTarget.Id);
                    nextBuild = Hours + 2;
                }
                catch
                {
                    Failed("build", "施工结果不确定：" + buildTarget.Id + "，已保留归属，请检查日志");
                    throw;
                }
            }
            else
            {
                Order order = action switch
                {
                    "auto" => null,
                    "move" => new MoveOrder(new Vector3((float)args["x"], squad.Position.y, (float)args["z"])),
                    "attack" => new AttackGroupOrder(enemyGroup),
                    "scavenge" => new MoveToBuildingOrder(scavengeTarget),
                    "return" => new MoveToBuildingOrder(hq),
                    _ => null
                };
                if (action == "auto") { manual.Remove(squad.Id); jobs.Remove(squad.Id); squad.OrdersHandlers.ClearOrders(); }
                else
                {
                    squad.OrdersHandlers.GiveOrder(order, clearOrders: true);
                    if (!ReferenceEquals(squad.OrdersHandlers.OrdersQueue.CurrentOrder, order) && !squad.OrdersHandlers.OrdersQueue.Orders.Contains(order))
                        throw new NpcFault("order_rejected", "游戏未接受订单，请读取状态。");
                    jobs.Remove(squad.Id);
                    if (action == "scavenge") jobs[squad.Id] = new Job { Order = order, Kind = "scavenge", Target = scavengeTarget.Id,
                        Position = squad.Position, Cargo = Cargo(squad), LastCargo = Cargo(squad), LastProgress = Hours };
                    manual[squad.Id] = Hours + hold;
                }
            }
        }
        Note("MCP 指令：" + action + (squad == null ? "" : "，小队 " + squad.Id));
        var result = new { request_id = key, faction_id = FactionId.ToString(), action, accepted = true,
            squad_id = squad?.Id, autonomous_safety_override = true, mode = manualMode ? "ai" : "auto",
            strategy = new { focus, allow_expansion = allowExpansion, search_radius = searchRadius } };
        receipts[key] = (input, result);
        return result;
    }
}
