using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using HarmonyLib;
using MonomiPark.SlimeRancher.DataModel;

namespace SlimeRancherBridge
{
    static class DroneBatteryDuration
    {
        const string HarmonyId = "SlimeRancherBridge.droneBatteryDuration";
        const float OriginalHours = 28f;
        const float RechargeHours = 84f;
        const double SecondsPerHour = 3600d;

        public static void TryInstall(Action<string> log)
        {
            var harmony = new Harmony(HarmonyId);
            try
            {
                Patch(harmony, "Reset", new[] { typeof(DroneModel) }, "ChangeRechargeDuration");
                Patch(harmony, "Update", Type.EmptyTypes, "ChangeMeterDuration");
                log("drone battery: recharge duration " + RechargeHours + " game hours");
            }
            catch (Exception e)
            {
                log("drone battery: install FAILED " + e);
                try
                {
                    harmony.UnpatchAll(HarmonyId);
                }
                catch (Exception rollbackError)
                {
                    log("drone battery: rollback FAILED " + rollbackError);
                }
            }
        }

        static void Patch(Harmony harmony, string method, Type[] parameters, string transpiler)
        {
            var target = AccessTools.Method(typeof(DroneStationBattery), method, parameters);
            if (target == null)
                throw new MissingMethodException("DroneStationBattery." + method);

            var patch = typeof(DroneBatteryDuration).GetMethod(transpiler, BindingFlags.Static | BindingFlags.NonPublic);
            harmony.Patch(target, null, null, new HarmonyMethod(patch));
        }

        static IEnumerable<CodeInstruction> ChangeRechargeDuration(IEnumerable<CodeInstruction> instructions)
        {
            return ReplaceConstant(instructions, OpCodes.Ldc_R4, OriginalHours, RechargeHours);
        }

        static IEnumerable<CodeInstruction> ChangeMeterDuration(IEnumerable<CodeInstruction> instructions)
        {
            return ReplaceConstant(instructions, OpCodes.Ldc_R8, OriginalHours * SecondsPerHour,
                RechargeHours * SecondsPerHour);
        }

        static IEnumerable<CodeInstruction> ReplaceConstant(IEnumerable<CodeInstruction> instructions, OpCode opcode,
            object original, object replacement)
        {
            var body = instructions.ToList();
            var constant = body.SingleOrDefault(i => i.opcode == opcode && Equals(i.operand, original));
            if (constant == null)
                throw new InvalidOperationException("Drone battery duration constant not found: " + original);

            constant.operand = replacement;
            return body;
        }
    }
}
