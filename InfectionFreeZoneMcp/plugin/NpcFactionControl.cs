using System;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace IfzMcp;

public sealed partial class Plugin
{
    private string npcLeaseFaction;
    private object NpcInvoke(string method, JObject args)
    {
        var assembly = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == "IfzNpcEnhanced");
        var gateway = assembly?.GetType("IfzNpcEnhanced.NpcGateway")?.GetMethod("Invoke", BindingFlags.Public | BindingFlags.Static);
        if (gateway == null) throw new BridgeException("npc_plugin_required", "Install IFZ Enhanced NPC 0.2.0 or later.");
        string response;
        try { response = (string)gateway.Invoke(null, new object[] { method, args.ToString(Newtonsoft.Json.Formatting.None) }); }
        catch (TargetInvocationException ex) { throw new BridgeException("npc_command_failed", ex.GetBaseException().Message); }
        var json = JObject.Parse(response);
        if ((bool?)json["ok"] != true) throw new BridgeException((string)json["code"] ?? "npc_command_failed", (string)json["message"] ?? "NPC command failed.");
        return json;
    }
    private object NpcStatus()
    {
        if (!sessionId.HasValue) return new { ready = false, session_id = (string)null };
        var data = (JObject)NpcInvoke("npc_list_factions", new JObject());
        return new { ready = true, session_id = sessionId.Value.ToString(), campaign = (string)data["campaign"], selected_faction_id = (string)data["selected_faction_id"] };
    }
    private object AcquireNpcControl(JObject args)
    {
        RequireSession(args);
        var faction = Text(args, "faction_id", 32);
        NpcInvoke("npc_get_faction", args); // Verify the configured NPC and a live owned HQ first.
        var requestedOwner = Text(args, "owner", 40);
        if (lease.HasValue && (npcLeaseFaction != faction || requestedOwner != owner || (string)args["lease"] != lease.Value.ToString()))
            throw new BridgeException("control_busy", "Another controller owns the write lease.");
        lease ??= Guid.NewGuid(); owner = requestedOwner; npcLeaseFaction = faction; leaseUntil = Now + 90;
        return new { lease, faction_id = faction, scope = "npc_faction", expires_in_seconds = 90 };
    }
    private object NpcCommand(JObject args)
    {
        if (npcLeaseFaction == null || Text(args, "faction_id", 32) != npcLeaseFaction)
            throw new BridgeException("faction_forbidden", "Acquire an NPC-scoped lease for exactly this faction first.");
        return NpcInvoke("npc_command", args);
    }
}
