// MelonMain —— Slime Rancher 2 桥接插件入口（MelonLoader 0.7.x / .NET 6 / IL2CPP）
//
// 与 1 代 SlimeRancherBridge 同构：
//   ① 读共享配置（MCP 与游戏共用同一份 bridge.json）
//   ② 起本地 HTTP 通道（127.0.0.1 + token）
//   ③ OnUpdate 里泵主线程任务（所有 Unity IL2CPP API 只能在主线程调用）
// 加工具：① Tools*.cs 里 handler + Register；② mcp\tools.json 加定义。
using System;
using MelonLoader;

[assembly: MelonInfo(typeof(SlimeRancher2Bridge.SR2BridgeMod), "SlimeRancher2Bridge", SlimeRancher2Bridge.SR2BridgeMod.ModVersion, "dsh")]
[assembly: MelonGame("MonomiPark", "SlimeRancher2")]

namespace SlimeRancher2Bridge
{
    public class SR2BridgeMod : MelonMod
    {
        public const string ModVersion = "0.5.34";
        bool clockFailed;
        bool chatFailed;
        internal static BridgeServer Server;
        internal static BridgeConfig Config;
        static DateTime nextSnapshot = DateTime.MinValue;

        public override void OnInitializeMelon()
        {
            try
            {
                Config = BridgeConfig.LoadOrCreate();
                Server = new BridgeServer(Config);
                ToolsCore.Register(Server);
                ToolsSession.Register(Server);
                ToolsWorld.Register(Server);
                ToolsRanch.Register(Server);
                ToolsExploration.Register(Server);
                ToolsFabrication.Register(Server);
                ToolsActions.Register(Server);
                ToolsProgress.Register(Server);
                ToolsNavigation.Register(Server);
                ChatTools.Register(Server);
                Server.Register("sr2_market", ToolsMarket.Read, mainThread: true);
                Server.Register("sr2_vacpack", ToolsInventory.Read, mainThread: true);
                Server.Register("sr2_vacpack_clear", ToolsInventory.Clear, mainThread: true, write: true);
                Server.Register("sr2_storage_list", ToolsStorage.List, mainThread: true);
                Server.Register("sr2_store", ToolsStorage.Store, mainThread: true, write: true);
                Server.Register("sr2_move_amount", ToolsStorage.MoveAmount, mainThread: true, write: true);
                Server.Register("sr2_consolidate_storage", ToolsStorage.Consolidate, mainThread: true, write: true);
                Server.Register("sr2_transfer", ToolsStorage.TransferBetween, mainThread: true, write: true);
                Server.Register("sr2_sell_plorts", ToolsSell.Sell, mainThread: true, write: true);
                Server.Register("sr2_shop_list", ToolsShop.List, mainThread: true);
                Server.Register("sr2_shop_buy", ToolsShop.Buy, mainThread: true, write: true);
                Server.Register("sr2_landing_check", ToolsLanding.Check, mainThread: true);
                Server.Register("sr2_refinery", ToolsRefinery.Read, mainThread: true);
                Server.Register("sr2_refine", ToolsRefinery.Deposit, mainThread: true, write: true);
                Server.Register("sr2_currency", ToolsCurrency.Read, mainThread: true);
                Server.Register("sr2_currency_add", ToolsCurrency.Add, mainThread: true, write: true);
                Server.Register("sr2_collect", ToolsCollect.Run, mainThread: true, write: true);
                ToolsScan.Register(Server);
                ToolsSlimes.Register(Server);
                ToolsPurchase.Register(Server);
                ToolsSafety.Register(Server);
                ToolsPlortDoors.Register(Server);
                ToolsGadgetCatalog.Register(Server);
                ToolsPlot.Register(Server);
                ToolsAutomation.Register(Server);
                ToolsTidy.Register(Server);
                ToolsWithdraw.Register(Server);
                bool ok = Server.Start();
                MelonLogger.Msg("[SR2Bridge] " + (ok ? "listening on " : "START FAILED on ")
                    + Config.host + ":" + Config.port
                    + (ok ? "" : " — " + Server.LastError)
                    + " | config=" + BridgeConfig.ConfigPath
                    + " | game=" + ToolsCore.GameDirectory);
                Server.Log("plugin loaded, version " + ModVersion);
            }
            catch (Exception e)
            {
                MelonLogger.Error("[SR2Bridge] init failed: " + e);
            }
        }

        public override void OnUpdate()
        {
            try
            {
                if (Server == null) return;
                Server.Pump(8);
                ChatOverlay.Update();
                HudMaterials.Update();
                if (DateTime.UtcNow >= nextSnapshot)
                {
                    nextSnapshot = DateTime.UtcNow.AddSeconds(1);
                    ToolsCore.RefreshSnapshot(Server);
                }
            }
            catch (Exception e)
            {
                MelonLogger.Error("[SR2Bridge] pump failed: " + e);
                if (Server != null) Server.LastError = "pump: " + e.GetType().Name + ": " + e.Message;
            }
        }

        public override void OnGUI()
        {
            if (!clockFailed)
            {
                try { WallClock.Draw(); }
                catch (Exception e)
                {
                    clockFailed = true;
                    MelonLogger.Warning("[SR2Bridge] clock overlay unavailable: " + e.GetBaseException().Message);
                }
            }
            try { QuickTidy.HandleKey(); ChatOverlay.Draw(); }
            catch (Exception e)
            {
                if (!chatFailed)
                {
                    chatFailed = true;
                    MelonLogger.Warning("[SR2Bridge] chat overlay unavailable: " + e.GetBaseException().Message);
                }
            }
            try { HudMaterials.Draw(); }
            catch (Exception e)
            {
                MelonLogger.Warning("[SR2Bridge] HUD materials unavailable: " + e.GetBaseException().Message);
            }
        }

        public override void OnApplicationQuit()
        {
            try
            {
                if (Server != null) Server.Stop();
            }
            catch (Exception e)
            {
                MelonLogger.Warning("[SR2Bridge] stop: " + e.Message);
            }
        }
    }
}












