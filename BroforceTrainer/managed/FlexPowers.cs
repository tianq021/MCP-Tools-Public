using System;

namespace BroforceLifeTrainer
{
    /// <summary>
    /// V15 摆动作特殊技：只负责“给能力”，触发仍由游戏的摆动作键（Gesture 4 = Flex）执行。
    /// 游戏同一时间只保留一个 Flex Power，所以这里每次只维持一个目标类型。
    /// </summary>
    internal static class FlexPowers
    {
        internal const int Off = 0;
        internal const int AirJump = 10;
        internal const int Invulnerable = 11;
        internal const int GoldenLight = 12;
        internal const int Teleport = 13;
        internal const int Alluring = 14;

        // 0 off, 1 active, 2 waiting for hero, 3 online denied, 4 error, 5 unsupported.
        internal static bool IsSupported(int request)
        {
            return request == Off ||
                (request >= AirJump && request <= Alluring);
        }

        internal static int Maintain(int request, bool offline, bool ready, Player player)
        {
            if (request == Off) return 0;
            if (!IsSupported(request)) return 5;
            if (!offline) return 3;
            if (!ready || player == null || !player.IsAlive()) return 2;
            PickupType target = (PickupType)request;
            if (!player.HasFlexPower(target))
            {
                // Forced write: replaces the single Flex Power slot instead of
                // being ignored because the player already holds another one.
                player.AddFlexPower(target, true);
            }
            return player.HasFlexPower(target) ? 1 : 4;
        }

        // Only remove what this trainer granted; a crate pickup is left alone.
        internal static bool Clear(Player player)
        {
            if (player == null || !player.IsAlive()) return false;
            player.AddFlexPower(PickupType.None, true);
            return !player.HasFlexPower();
        }
    }
}
