// Win32Ime —— 直接走 Windows IMM32 读输入法组合串/提交串
//
// 为什么不走 Unity：
//   · 游戏把输入处理切到 Input System 后，旧 Input 类被禁用（legacy_input_broken）
//   · IMGUI 的 GUIUtility.imeCompositionMode 在本机需要 IMGUI 有键盘控件的焦点，实测拿不到组合串
//   · 所以直接在进程内 P/Invoke imm32.dll，读取当前窗口输入法上下文（HIMC）里的组合串；
//     组合结束时的提交串（GCS_RESULTSTR）追加到聊天草稿。
//
// 用法（由 ChatOverlay 驱动）：
//   Open()  聊天打开时调用：拿窗口句柄 + 输入法上下文（拿不到就创建一个并关联）
//   Poll()  OnGUI 每帧调用：返回 (组合串, 新提交串)
//   Close() 聊天关闭时调用：释放上下文
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace SlimeRancher2Bridge
{
    internal static class Win32Ime
    {
        const int GCS_COMPSTR = 0x0008;
        const int GCS_RESULTSTR = 0x0800;

        [DllImport("user32.dll")]
        static extern IntPtr GetActiveWindow();

        [DllImport("imm32.dll")]
        static extern IntPtr ImmGetContext(IntPtr hWnd);

        [DllImport("imm32.dll")]
        static extern bool ImmReleaseContext(IntPtr hWnd, IntPtr hIMC);

        [DllImport("imm32.dll", CharSet = CharSet.Unicode)]
        static extern int ImmGetCompositionStringW(IntPtr hIMC, int dwIndex, byte[] lpBuf, int dwBufLen);

        [DllImport("imm32.dll")]
        static extern IntPtr ImmCreateContext();

        [DllImport("imm32.dll")]
        static extern IntPtr ImmAssociateContext(IntPtr hWnd, IntPtr hIMC);

        [DllImport("imm32.dll")]
        static extern bool ImmGetOpenStatus(IntPtr hIMC);

        static IntPtr window;
        static IntPtr context;
        static IntPtr createdContext;
        static bool ownContext;
        static string lastResult = "";
        static string lastReadError = "";

        public static bool Available => context != IntPtr.Zero || everWorked;
        public static bool EverWorked => everWorked;
        public static int CommitCount => commitCount;
        static bool everWorked;
        static int commitCount;
        public static bool ImeOpen { get; private set; }
        public static string LastReadError => lastReadError;
        public static string LastResult => lastResult;

        public static void Open()
        {
            try
            {
                window = GetActiveWindow();
                if (window == IntPtr.Zero)
                {
                    lastReadError = "GetActiveWindow returned 0";
                    ChatDiag.Log("Win32Ime: no active window");
                    return;
                }
                context = ImmGetContext(window);
                ownContext = false;
                if (context == IntPtr.Zero)
                {
                    // 游戏窗口没有输入法上下文：建一个并关联
                    createdContext = ImmCreateContext();
                    if (createdContext != IntPtr.Zero)
                    {
                        ImmAssociateContext(window, createdContext);
                        context = ImmGetContext(window);
                        ownContext = context != IntPtr.Zero;
                    }
                }
                lastReadError = context == IntPtr.Zero ? "ImmGetContext/associate failed" : "";
                if (context != IntPtr.Zero) everWorked = true;
                try { ImeOpen = context != IntPtr.Zero && ImmGetOpenStatus(context); } catch { }
                ChatDiag.Log("Win32Ime open: hwnd=" + window + " ctx=" + context + " own=" + ownContext
                    + " imeOpen=" + ImeOpen + (lastReadError.Length > 0 ? " err=" + lastReadError : ""));
            }
            catch (Exception e)
            {
                lastReadError = e.GetBaseException().Message;
                ChatDiag.Log("Win32Ime open failed: " + e.GetBaseException().Message);
            }
        }

        public static void Close()
        {
            try
            {
                if (context != IntPtr.Zero && window != IntPtr.Zero)
                {
                    ImmReleaseContext(window, context);
                }
                if (ownContext && createdContext != IntPtr.Zero && window != IntPtr.Zero)
                {
                    ImmAssociateContext(window, IntPtr.Zero);
                }
            }
            catch (Exception e)
            {
                ChatDiag.Log("Win32Ime close failed: " + e.GetBaseException().Message);
            }
            context = IntPtr.Zero;
            createdContext = IntPtr.Zero;
            window = IntPtr.Zero;
            ownContext = false;
            lastResult = "";
        }

        /// <summary>读组合串与提交串；组合结束返回新提交串（没有则空）。</summary>
        public static string Poll(out string composition)
        {
            composition = "";
            if (context == IntPtr.Zero) return "";
            try
            {
                try { ImeOpen = ImmGetOpenStatus(context); } catch { }
                composition = ReadString(GCS_COMPSTR);
                string result = ReadString(GCS_RESULTSTR);
                if (result.Length > 0 && result != lastResult)
                {
                    lastResult = result;
                    commitCount++;
                    ChatDiag.Log("Win32Ime commit: '" + result + "'  composing='" + composition + "' imeOpen=" + ImeOpen);
                    return result;
                }
            }
            catch (Exception e)
            {
                lastReadError = e.GetBaseException().Message;
                ChatDiag.Log("Win32Ime poll failed: " + e.GetBaseException().Message);
            }
            return "";
        }

        static string ReadString(int index)
        {
            int bytes = ImmGetCompositionStringW(context, index, null, 0);
            if (bytes <= 0) return "";
            var buffer = new byte[bytes];
            int read = ImmGetCompositionStringW(context, index, buffer, bytes);
            if (read <= 0) return "";
            return Encoding.Unicode.GetString(buffer, 0, read);
        }
    }
}

