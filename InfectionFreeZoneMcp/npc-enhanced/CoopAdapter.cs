using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Gameplay.Buildings;
using Gameplay.GameResources;
using Gameplay.Rebuilding;
using Gameplay.Units.Characters;
using HarmonyLib;
using Zenject;

namespace IfzNpcEnhanced;

// All private RC7-P7 APIs are contained here. Never ship or alter the third-party DLL.
internal sealed class CoopAdapter
{
    static readonly Dictionary<(Type, string), MemberInfo> Members = new();
    internal readonly Assembly Assembly;
    internal readonly Type Brain, Lifecycle, Settings, Persistence, Economy, Registry, Services, Founder, OrderContext;
    readonly MethodInfo GetOwnerCharacter;
    internal CoopAdapter(Assembly assembly)
    {
        Assembly = assembly;
        Brain = Type("NpcBrainService"); Lifecycle = Type("NpcLifecycleService");
        Settings = Type("NpcSessionSettings"); Persistence = Type("CampaignPersistence");
        Economy = Type("Patches.EconomyPartition"); Registry = Type("OwnershipRegistry");
        Services = Type("GameServices"); Founder = Type("SettlementFounder"); OrderContext = Type("Patches.NpcOrderContext");
        foreach (var entry in new[] { (Brain,"Update"), (Brain,"TickFaction"), (Lifecycle,"BootstrapIfNeeded"),
            (Economy,"TryGetExplicitBuildingOwner"), (Economy,"GetContainer"), (Founder,"RegisterBuildingOwnership"),
            (Registry,"TryGetExplicitOwner"), (OrderContext,"Enter"), (Economy,"PushOwnerContext"), (Economy,"PopOwnerContext") })
            if (AccessTools.Method(entry.Item1, entry.Item2) == null) throw new MissingMethodException(entry.Item1.FullName, entry.Item2);
        GetOwnerCharacter = AccessTools.Method(Economy, "GetOwner", new[] { typeof(Character) })
            ?? throw new MissingMethodException(Economy.FullName, "GetOwner(Character)");
    }
    Type Type(string name) => Assembly.GetType("IFZ.Multiplayer.GameIntegration." + name, true);
    internal static object Get(object obj, string name)
    {
        if (obj == null) return null;
        var type = obj as Type ?? obj.GetType(); var target = obj is Type ? null : obj;
        if (!Members.TryGetValue((type, name), out var member))
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
            for (var current = type; current != null && member == null; current = current.BaseType)
                member = (MemberInfo)current.GetProperty(name, flags) ?? current.GetField(name, flags);
            if (member == null) throw new MissingMemberException(type.FullName, name);
            Members[(type, name)] = member;
        }
        return member is PropertyInfo property ? property.GetValue(target) : ((FieldInfo)member).GetValue(target);
    }
    internal static object Call(Type type, string method, params object[] args) =>
        (AccessTools.Method(type, method) ?? throw new MissingMethodException(type.FullName, method)).Invoke(null, args);
    internal static ulong Id(object owner) => owner == null ? 0 : Convert.ToUInt64(Get(owner, "Value"));
    internal DiContainer Container => (DiContainer)Get(Services, "Container");
    internal bool Ready => (bool)Get(Services, "GameplayReady") && Container != null && (bool)Get(Economy, "Enabled");
    internal string Campaign => (string)Get(Persistence, "CampaignId") ?? "";
    internal object[] Records => ((IEnumerable)Get(Lifecycle, "All")).Cast<object>().ToArray();
    internal object Owner(object record) => Get(record, "Id");
    internal ulong OwnerOf(Structure structure)
    {
        if (structure == null) return 0;
        var args = new object[] { structure.GetId(), null };
        return (bool)Call(Economy, "TryGetExplicitBuildingOwner", args) ? Id(args[1]) : 0;
    }
    internal ulong OwnerOfEntity(string id)
    {
        var args = new object[] { id, null };
        return !string.IsNullOrEmpty(id) && (bool)Call(Registry, "TryGetExplicitOwner", args) ? Id(args[1]) : 0;
    }
    // Mirrors the mod's own EconomyPartition.GetOwner(Character): group -> entity id -> house building.
    // The old group/id-only guess could report 0 for workers the mod itself counts, which silently
    // disabled production, construction and staffing for the enhanced faction.
    internal ulong OwnerOf(Character worker) => worker == null ? 0 : Id(GetOwnerCharacter.Invoke(null, new object[] { worker }));
    internal ResourcesContainer Stock(object owner) => (ResourcesContainer)Call(Economy, "GetContainer", owner, null);
    internal void Claim(Building building, object owner) => Call(Founder, "RegisterBuildingOwnership", building, building.Id, owner);
    internal IDisposable Scope(object owner) => new OwnerScope(this, owner);
    sealed class OwnerScope : IDisposable
    {
        readonly CoopAdapter adapter; readonly IDisposable npc;
        internal OwnerScope(CoopAdapter adapter, object owner)
        {
            this.adapter = adapter;
            npc = (IDisposable)Call(adapter.OrderContext, "Enter");
            try { Call(adapter.Economy, "PushOwnerContext", owner); }
            catch { npc.Dispose(); throw; }
        }
        public void Dispose() { try { Call(adapter.Economy, "PopOwnerContext"); } finally { npc.Dispose(); } }
    }
}
