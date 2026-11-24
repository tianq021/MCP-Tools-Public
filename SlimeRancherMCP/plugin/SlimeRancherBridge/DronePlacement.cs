using System;
using System.Reflection;
using HarmonyLib;

namespace SlimeRancherBridge
{
    static class DronePlacement
    {
        const string LimitMessage = "w.limit_reached_drone";

        public static void TryInstall(Action<string> log)
        {
            try
            {
                var target = AccessTools.Method(typeof(GadgetDirector), "GetPlacementError",
                    new[] { typeof(GadgetSite), typeof(Gadget.Id) });
                if (target == null)
                    throw new MissingMethodException("GadgetDirector.GetPlacementError");

                var postfix = typeof(DronePlacement).GetMethod("RemoveDroneCountLimit",
                    BindingFlags.Static | BindingFlags.NonPublic);
                var harmony = new Harmony("SlimeRancherBridge.dronePlacement");
                harmony.Patch(target, null, new HarmonyMethod(postfix));
                log("drone placement: per-network count limit disabled");
            }
            catch (Exception e)
            {
                log("drone placement: install FAILED " + e);
            }
        }

        static void RemoveDroneCountLimit(Gadget.Id __1, ref GadgetDirector.PlacementError __result)
        {
            if ((__1 == Gadget.Id.DRONE || __1 == Gadget.Id.DRONE_ADVANCED)
                && __result != null && __result.message == LimitMessage)
                __result = null;
        }
    }
}
