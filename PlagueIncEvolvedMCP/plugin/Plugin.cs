using System;
using BepInEx;
using Newtonsoft.Json;
using UnityEngine;

[BepInPlugin("local.plagueinc.mcp", "Plague Inc MCP Bridge", "0.6.2")]
[BepInProcess("PlagueIncEvolved.exe")]
public sealed class PlagueIncBridgePlugin : BaseUnityPlugin
{
    private readonly MainMenu menu = new MainMenu();
    private MenuBridge bridge;
    private (MenuRequest request, Func<bool> confirm, float deadline) pending;

    private void Awake()
    {
        try
        {
            bridge = new MenuBridge();
            Logger.LogInfo("Main menu bridge ready.");
        }
        catch (Exception error)
        {
            Logger.LogError(error);
        }
    }

    private void Update()
    {
        if (bridge == null) return;
        GameStatus.TickAutoCollect();
        menu.Observe();
        var request = pending.request;
        if (request == null && (!bridge.TryTake(out request) || !request.TryBegin())) return;

        try
        {
            if (pending.request != null)
            {
                var reply = menu.ConfirmTransition(request, pending.confirm, Time.realtimeSinceStartup >= pending.deadline);
                if (reply == null) return;
                pending = default;
                request.Complete(JsonConvert.SerializeObject(reply));
            }
            else
            {
                var reply = menu.Call(request, out var confirm);
                if (confirm != null) pending = (request, confirm, Time.realtimeSinceStartup +
                    (request.View == MenuView.Setup || request.View == MenuView.Country ? 8f : 2f));
                else request.Complete(JsonConvert.SerializeObject(reply));
            }
        }
        catch (Exception error)
        {
            pending = default;
            Logger.LogError(error);
            request.Complete(MenuBridge.Error("MAIN_MENU_ERROR", null));
        }
    }

    private void OnDestroy() => bridge?.Dispose();
}
