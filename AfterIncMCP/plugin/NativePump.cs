using System.Runtime.InteropServices;
using BepInEx.Unity.IL2CPP.Hook;

namespace AfterIncBridge;

// Hook an existing game's callback. No class injection or Unity component is created.
internal static class NativePump
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void UpdateCallback(IntPtr instance, IntPtr method);
    private static UpdateCallback? original;
    private static readonly UpdateCallback callback = Tick;
    private static INativeDetour? detour, menuDetour;
    private static UpdateCallback? originalMenu;
    private static readonly UpdateCallback menuCallback = MenuTick;
    private static readonly GameBridge bridge = new();
    private static long ticks;
    internal static void Install()
    {
        try {
            detour = Hook("Assembly-CSharp", "com.ndemiccreations.survivors", "GameController", callback, out original);
            menuDetour = Hook("com.ndemiccreation.foundation", "com.ndemiccreations.foundation", "OoGUIScreenAnimator", menuCallback, out originalMenu);
        } catch { Stop(); throw; }
    }

    private static INativeDetour Hook(string module, string ns, string type, UpdateCallback replacement, out UpdateCallback? trampoline)
    {
        var domain = il2cpp_domain_get();
        var assembly = il2cpp_domain_assembly_open(domain, module);
        if (assembly == IntPtr.Zero) throw new InvalidOperationException("Game assembly unavailable");
        var klass = il2cpp_class_from_name(il2cpp_assembly_get_image(assembly), ns, type);
        if (klass == IntPtr.Zero) throw new InvalidOperationException(type + " unavailable");
        var method = il2cpp_class_get_method_from_name(klass, "Update", 0);
        if (method == IntPtr.Zero) throw new InvalidOperationException(type + ".Update unavailable");
        // IL2CPP MethodInfo starts with the native method pointer on this x64 build.
        var address = Marshal.ReadIntPtr(method);
        if (address == IntPtr.Zero) throw new InvalidOperationException("Update has no native body");
        return INativeDetour.CreateAndApply(address, replacement, out trampoline);
    }
    private static void MenuTick(IntPtr instance, IntPtr method)
    {
        originalMenu!(instance, method);
        try { bridge.MenuTick(instance); }
        catch (Exception e) { Plugin.LogSource?.LogError("Menu callback: " + e.Message); }
    }
    private static void Tick(IntPtr instance, IntPtr method)
    {
        original!(instance, method);
        try {
            if (Interlocked.Increment(ref ticks) == 1) Plugin.LogSource?.LogInfo("NativePump first main-thread tick");
            bridge.Tick(instance);
        } catch (Exception e) { Plugin.LogSource?.LogError("NativePump: " + e.Message); }
    }
    internal static void Stop() { menuDetour?.Dispose(); menuDetour = null; detour?.Dispose(); detour = null; }
    [DllImport("GameAssembly", CallingConvention = CallingConvention.Cdecl)] private static extern IntPtr il2cpp_domain_get();
    [DllImport("GameAssembly", CallingConvention = CallingConvention.Cdecl)] private static extern IntPtr il2cpp_domain_assembly_open(IntPtr domain, [MarshalAs(UnmanagedType.LPStr)] string name);
    [DllImport("GameAssembly", CallingConvention = CallingConvention.Cdecl)] private static extern IntPtr il2cpp_assembly_get_image(IntPtr assembly);
    [DllImport("GameAssembly", CallingConvention = CallingConvention.Cdecl)] private static extern IntPtr il2cpp_class_from_name(IntPtr image, [MarshalAs(UnmanagedType.LPStr)] string namesp, [MarshalAs(UnmanagedType.LPStr)] string name);
    [DllImport("GameAssembly", CallingConvention = CallingConvention.Cdecl)] private static extern IntPtr il2cpp_class_get_method_from_name(IntPtr klass, [MarshalAs(UnmanagedType.LPStr)] string name, int count);
}
