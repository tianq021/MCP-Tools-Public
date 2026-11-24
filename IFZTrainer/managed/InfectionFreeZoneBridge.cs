using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

[assembly: AssemblyVersion("3.0.0.0")]

namespace InfectionFreeZoneTrainerV3
{
    /// <summary>
    /// Loaded into Infection Free Zone's Mono runtime by the external trainer.
    /// A one-shot window message creates a persistent MonoBehaviour on Unity's
    /// main thread. Resource requests are exchanged through named shared memory.
    /// </summary>
    public static class Bootstrap
    {
        public const uint InstallMonitorMessage = 0x8455; // WM_APP + 0x455

        private const int GwlWndProc = -4;
        private const uint GwOwner = 4;
        private const uint PageReadWrite = 0x04;
        private const uint FileMapWrite = 0x0002;
        private const int ErrorAlreadyExists = 183;

        private const int SharedMemorySize = 256;
        private const int ResourceSequenceOffset = 0;
        private const int MonitorReadyOffset = 8;
        private const int MonitorHeartbeatOffset = 16;
        private const int ResourceQuantityOffset = 24;
        private const int ResourceIdOffset = 32;
        private const int ResourceIdCharacters = 64;
        private const int ExtraCommandSequenceOffset = 160;
        private const int ExtraCommandCodeOffset = 168;
        private const int ExtraCommandArgumentOffset = 176;
        private const int MonitorHealthProbeMilliseconds = 1000;

        private static readonly int CurrentPid = Process.GetCurrentProcess().Id;
        private static readonly string AppDataDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "InfectionFreeZoneTrainer"
        );
        private static readonly string StatusPath = Path.Combine(
            AppDataDirectory,
            "bridge_status_v3.txt"
        );
        private static readonly HashSet<string> AllowedResourceIds = new HashSet<string>(
            new string[]
            {
                "res_cans",
                "res_fuel",
                "res_ammo",
                "res_bricks",
                "res_metal",
                "res_wood",
                "res_fertiliser",
                "res_drugs",
                "res_explosives",
                "res_grenades",
                "res_ammotank",
                "res_vegetables",
                "res_meat",
                "res_electronics",
                "res_basic_tool",
                "res_adv_tool",
                "res_scientificmaterial",
                "res_planks",
                "res_grain",
                "res_food_rations",
                "res_chicken",
                "res_mines",
                "res_beverages",
                "res_scientists_case",
                "res_scientists_vaccine",
                "res_scientists_infected",
                "res_scientists_sample",
                "eq_basic_melee_weapon",
                "eq_pistol",
                "eq_sniper_rifle",
                "eq_assault_rifle",
                "eq_shotgun",
                "eq_bow",
                "eq_crossbow",
                "eq_flamethrower",
                "eq_molotov",
                "eq_polearm",
                "eq_hcal",
                "eq_mortar",
                "eq_tank_cannon",
                "eq_wear_protectors",
                "eq_wear_riot_gear",
                "eq_wear_plate",
                "eq_wear_camouflage",
                "eq_wear_soldier_gear"
            },
            StringComparer.Ordinal
        );

        private static WndProcDelegate windowProcedure;
        private static IntPtr originalWindowProcedure = IntPtr.Zero;
        private static IntPtr gameWindow = IntPtr.Zero;
        private static IntPtr readyEvent = IntPtr.Zero;
        private static IntPtr sharedMemoryHandle = IntPtr.Zero;
        private static IntPtr sharedMemoryView = IntPtr.Zero;
        private static long lastResourceSequence;
        private static long lastExtraCommandSequence;
        private static int currentMonitorGeneration;
        private static bool windowHookInstalled;
        private static bool monitorInstalled;

        private delegate IntPtr WndProcDelegate(
            IntPtr hWnd,
            uint message,
            IntPtr wParam,
            IntPtr lParam
        );
        private delegate bool EnumWindowsDelegate(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsDelegate callback, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool IsWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetWindow(IntPtr hWnd, uint command);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
        private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int index, IntPtr newValue);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
        private static extern IntPtr SetWindowLong32(IntPtr hWnd, int index, IntPtr newValue);

        [DllImport("user32.dll")]
        private static extern IntPtr CallWindowProc(
            IntPtr previousWindowProcedure,
            IntPtr hWnd,
            uint message,
            IntPtr wParam,
            IntPtr lParam
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateEvent(
            IntPtr eventAttributes,
            bool manualReset,
            bool initialState,
            string name
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetEvent(IntPtr eventHandle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileMapping(
            IntPtr fileHandle,
            IntPtr attributes,
            uint protection,
            uint maximumSizeHigh,
            uint maximumSizeLow,
            string name
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr MapViewOfFile(
            IntPtr mappingHandle,
            uint desiredAccess,
            uint fileOffsetHigh,
            uint fileOffsetLow,
            UIntPtr bytesToMap
        );

        [DllImport("kernel32.dll")]
        private static extern void SetLastError(uint errorCode);

        /// <summary>Entry point called by the external Mono injector.</summary>
        public static void Start()
        {
            try
            {
                InitializeSharedMemory();

                bool sharedMonitorReady = ReadSharedInt64(MonitorReadyOffset) == 1;
                if ((monitorInstalled || sharedMonitorReady) && IsMonitorHeartbeatMoving())
                {
                    monitorInstalled = true;
                    SignalReady();
                    WriteStatus("READY", "MONITOR");
                    return;
                }

                monitorInstalled = false;
                WriteSharedInt64(MonitorReadyOffset, 0);

                if (windowHookInstalled && gameWindow != IntPtr.Zero && IsWindow(gameWindow))
                {
                    SignalReady();
                    WriteStatus("READY", gameWindow.ToInt64().ToString());
                    return;
                }

                windowHookInstalled = false;
                gameWindow = WaitForGameWindow();
                originalWindowProcedure = IntPtr.Zero;
                if (gameWindow == IntPtr.Zero)
                {
                    WriteStatus("ERROR", "找不到 Infection Free Zone 主窗口");
                    return;
                }

                windowProcedure = new WndProcDelegate(WindowProcedure);
                IntPtr functionPointer = Marshal.GetFunctionPointerForDelegate(windowProcedure);
                SetLastError(0);
                originalWindowProcedure = SetWindowLongPtr(gameWindow, GwlWndProc, functionPointer);
                int error = Marshal.GetLastWin32Error();
                if (originalWindowProcedure == IntPtr.Zero && error != 0)
                {
                    WriteStatus("ERROR", "安装一次性主线程初始化钩子失败，Win32=" + error);
                    return;
                }

                windowHookInstalled = true;
                SignalReady();
                WriteStatus("READY", gameWindow.ToInt64().ToString());
            }
            catch (Exception exception)
            {
                WriteStatus("ERROR", exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static void InitializeSharedMemory()
        {
            if (sharedMemoryView != IntPtr.Zero)
            {
                return;
            }

            SetLastError(0);
            sharedMemoryHandle = CreateFileMapping(
                new IntPtr(-1),
                IntPtr.Zero,
                PageReadWrite,
                0,
                SharedMemorySize,
                "Local\\InfectionFreeZoneTrainer_SharedV3_" + CurrentPid
            );
            if (sharedMemoryHandle == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "创建共享状态失败，Win32=" + Marshal.GetLastWin32Error()
                );
            }
            int createError = Marshal.GetLastWin32Error();

            sharedMemoryView = MapViewOfFile(
                sharedMemoryHandle,
                FileMapWrite,
                0,
                0,
                new UIntPtr((uint)SharedMemorySize)
            );
            if (sharedMemoryView == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "映射共享状态失败，Win32=" + Marshal.GetLastWin32Error()
                );
            }

            if (createError != ErrorAlreadyExists)
            {
                int offset;
                for (offset = 0; offset < SharedMemorySize; offset += 8)
                {
                    WriteSharedInt64(offset, 0);
                }
            }
            lastResourceSequence = ReadSharedInt64(ResourceSequenceOffset);
            lastExtraCommandSequence = ReadSharedInt64(ExtraCommandSequenceOffset);
        }

        private static bool IsMonitorHeartbeatMoving()
        {
            long before = ReadSharedInt64(MonitorHeartbeatOffset);
            Thread.Sleep(MonitorHealthProbeMilliseconds);
            return ReadSharedInt64(MonitorHeartbeatOffset) != before;
        }

        private static IntPtr SetWindowLongPtr(IntPtr hWnd, int index, IntPtr newValue)
        {
            return IntPtr.Size == 8
                ? SetWindowLongPtr64(hWnd, index, newValue)
                : SetWindowLong32(hWnd, index, newValue);
        }

        private static IntPtr WaitForGameWindow()
        {
            int attempts;
            for (attempts = 0; attempts < 150; attempts++)
            {
                IntPtr found = FindGameWindow();
                if (found != IntPtr.Zero)
                {
                    return found;
                }
                Thread.Sleep(100);
            }
            return IntPtr.Zero;
        }

        private static IntPtr FindGameWindow()
        {
            IntPtr result = IntPtr.Zero;
            EnumWindows(
                delegate(IntPtr hWnd, IntPtr unused)
                {
                    uint processId;
                    GetWindowThreadProcessId(hWnd, out processId);
                    if (
                        processId == (uint)CurrentPid
                        && IsWindowVisible(hWnd)
                        && GetWindow(hWnd, GwOwner) == IntPtr.Zero
                    )
                    {
                        result = hWnd;
                        return false;
                    }
                    return true;
                },
                IntPtr.Zero
            );
            return result;
        }

        private static IntPtr WindowProcedure(
            IntPtr hWnd,
            uint message,
            IntPtr wParam,
            IntPtr lParam
        )
        {
            if (message != InstallMonitorMessage)
            {
                return CallWindowProc(originalWindowProcedure, hWnd, message, wParam, lParam);
            }

            bool initialized = false;
            try
            {
                EnsureMonitorOnMainThread();
                initialized = true;
            }
            catch (Exception exception)
            {
                WriteStatus("ERROR", exception.GetType().Name + ": " + exception.Message);
            }

            bool restored = RestoreOriginalWindowProcedure(hWnd);
            return initialized && restored ? new IntPtr(1) : IntPtr.Zero;
        }

        private static void EnsureMonitorOnMainThread()
        {
            if (monitorInstalled)
            {
                return;
            }

            UnityEngine.Application.runInBackground = true;
            UnityEngine.GameObject host = new UnityEngine.GameObject(
                "InfectionFreeZoneResourceTrainerMonitor"
            );
            UnityEngine.Object.DontDestroyOnLoad(host);
            ResourceMonitorBehaviour behaviour = (ResourceMonitorBehaviour)host.AddComponent(
                typeof(ResourceMonitorBehaviour)
            );
            currentMonitorGeneration++;
            behaviour.Generation = currentMonitorGeneration;
            lastResourceSequence = ReadSharedInt64(ResourceSequenceOffset);
            lastExtraCommandSequence = ReadSharedInt64(ExtraCommandSequenceOffset);
            monitorInstalled = true;
            WriteSharedInt64(MonitorReadyOffset, 1);
            WriteStatus("MONITOR", "READY");
        }

        private static bool RestoreOriginalWindowProcedure(IntPtr hWnd)
        {
            if (!windowHookInstalled)
            {
                return true;
            }

            SetLastError(0);
            IntPtr replaced = SetWindowLongPtr(hWnd, GwlWndProc, originalWindowProcedure);
            int error = Marshal.GetLastWin32Error();
            if (replaced == IntPtr.Zero && error != 0)
            {
                WriteStatus("ERROR", "移除一次性窗口钩子失败，Win32=" + error);
                return false;
            }

            windowHookInstalled = false;
            gameWindow = IntPtr.Zero;
            return true;
        }

        internal static void ProcessPendingResourceRequest()
        {
            long sequence = ReadSharedInt64(ResourceSequenceOffset);
            if (sequence == lastResourceSequence)
            {
                return;
            }
            lastResourceSequence = sequence;

            try
            {
                string resourceId = ReadSharedString(ResourceIdOffset, ResourceIdCharacters);
                double quantity = BitConverter.Int64BitsToDouble(
                    ReadSharedInt64(ResourceQuantityOffset)
                );
                ExecuteResourceRequest(resourceId, quantity);
            }
            catch (Exception exception)
            {
                WriteStatus("ERROR", exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static void ExecuteResourceRequest(string resourceId, double quantity)
        {
            if (!AllowedResourceIds.Contains(resourceId))
            {
                WriteStatus("DENIED", "不支持的资源 ID：" + resourceId);
                return;
            }
            if (Double.IsNaN(quantity) || Double.IsInfinity(quantity) || quantity <= 0 || quantity > 1000000)
            {
                WriteStatus("DENIED", "资源数量必须大于 0 且不超过 1000000");
                return;
            }

            Commands.GameConsoleCommandHandler handler =
                Commands.GameConsoleCommandHandler.Instance;
            if (handler == null)
            {
                WriteStatus("WAITING", "请先载入存档并进入游戏地图");
                return;
            }

            handler.AddResourcesToHq(resourceId, (float)quantity);
            WriteStatus(
                "OK",
                resourceId,
                quantity.ToString("0.###", CultureInfo.InvariantCulture)
            );
        }

        internal static void ProcessPendingExtraCommand()
        {
            long sequence = ReadSharedInt64(ExtraCommandSequenceOffset);
            if (sequence == lastExtraCommandSequence)
            {
                return;
            }
            lastExtraCommandSequence = sequence;

            try
            {
                long commandCode = ReadSharedInt64(ExtraCommandCodeOffset);
                long commandArgument = ReadSharedInt64(ExtraCommandArgumentOffset);
                ExecuteExtraCommand(commandCode, commandArgument);
            }
            catch (Exception exception)
            {
                WriteStatus("ERROR", exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static void ExecuteExtraCommand(long commandCode, long commandArgument)
        {
            Commands.GameConsoleCommandHandler handler =
                Commands.GameConsoleCommandHandler.Instance;
            if (handler == null)
            {
                WriteStatus("WAITING", "请先载入存档并进入游戏地图");
                return;
            }

            switch (commandCode)
            {
                case 1:
                    handler.RevealLairs();
                    break;
                case 2:
                    handler.RevealHideouts();
                    break;
                case 3:
                    handler.RevealAllHideoutsLairs();
                    break;
                case 4:
                    handler.ShowVehicles();
                    break;
                case 10:
                    handler.FastConstructionState(commandArgument != 0);
                    break;
                case 11:
                    handler.FastResearchState(commandArgument != 0);
                    break;
                case 12:
                    handler.FreeResearchState(commandArgument != 0);
                    break;
                case 13:
                    handler.FastScavengeState(commandArgument != 0);
                    break;
                case 14:
                    handler.InfiniteAmmoState(commandArgument != 0);
                    break;
                case 15:
                    handler.NoHungerState(commandArgument != 0);
                    break;
                case 16:
                    handler.PerfectMoodState(commandArgument != 0);
                    break;
                case 17:
                    handler.PerfectWeatherState(commandArgument != 0);
                    break;
                default:
                    WriteStatus("DENIED", "不支持的额外功能代码：" + commandCode);
                    return;
            }

            WriteStatus(
                "EXTRA_OK",
                commandCode.ToString(CultureInfo.InvariantCulture),
                commandArgument != 0 ? "1" : "0"
            );
        }

        private static string ReadSharedString(int offset, int characterCount)
        {
            IntPtr address = new IntPtr(sharedMemoryView.ToInt64() + offset);
            string value = Marshal.PtrToStringUni(address, characterCount) ?? String.Empty;
            int terminator = value.IndexOf('\0');
            return terminator >= 0 ? value.Substring(0, terminator) : value;
        }

        internal static void UpdateHeartbeat()
        {
            WriteSharedInt64(
                MonitorHeartbeatOffset,
                unchecked(ReadSharedInt64(MonitorHeartbeatOffset) + 1)
            );
        }

        internal static void MonitorDestroyed(int generation)
        {
            if (generation == currentMonitorGeneration)
            {
                monitorInstalled = false;
                WriteSharedInt64(MonitorReadyOffset, 0);
            }
        }

        private static long ReadSharedInt64(int offset)
        {
            return Marshal.ReadInt64(new IntPtr(sharedMemoryView.ToInt64() + offset));
        }

        private static void WriteSharedInt64(int offset, long value)
        {
            Marshal.WriteInt64(new IntPtr(sharedMemoryView.ToInt64() + offset), value);
        }

        private static void SignalReady()
        {
            if (readyEvent == IntPtr.Zero)
            {
                readyEvent = CreateEvent(
                    IntPtr.Zero,
                    true,
                    false,
                    "Local\\InfectionFreeZoneTrainer_ReadyV3_" + CurrentPid
                );
            }
            if (readyEvent != IntPtr.Zero)
            {
                SetEvent(readyEvent);
            }
        }

        private static void WriteStatus(string state, params string[] details)
        {
            try
            {
                Directory.CreateDirectory(AppDataDirectory);
                StringBuilder builder = new StringBuilder();
                builder.Append(state);
                builder.Append('|');
                builder.Append(CurrentPid);
                int index;
                for (index = 0; index < details.Length; index++)
                {
                    builder.Append('|');
                    builder.Append(
                        (details[index] ?? String.Empty)
                            .Replace('|', '/')
                            .Replace('\r', ' ')
                            .Replace('\n', ' ')
                    );
                }
                File.WriteAllText(StatusPath, builder.ToString(), Encoding.UTF8);
            }
            catch
            {
                // Status reporting must never destabilize the game.
            }
        }
    }

    public sealed class ResourceMonitorBehaviour : UnityEngine.MonoBehaviour
    {
        public int Generation;
        private float nextHeartbeatTime;

        private void Awake()
        {
            Bootstrap.UpdateHeartbeat();
            nextHeartbeatTime = UnityEngine.Time.realtimeSinceStartup + 0.25f;
        }

        private void Update()
        {
            Bootstrap.ProcessPendingResourceRequest();
            Bootstrap.ProcessPendingExtraCommand();

            float now = UnityEngine.Time.realtimeSinceStartup;
            if (now >= nextHeartbeatTime)
            {
                nextHeartbeatTime = now + 0.25f;
                Bootstrap.UpdateHeartbeat();
            }
        }

        private void OnDestroy()
        {
            Bootstrap.MonitorDestroyed(Generation);
        }
    }
}
