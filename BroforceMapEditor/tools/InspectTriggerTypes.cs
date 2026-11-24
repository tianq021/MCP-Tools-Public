using System;
using System.IO;
using System.Reflection;

// 查 TriggerActionInfo / TriggerAction 的实现类型，供桥接构造触发器使用。
class InspectTriggerTypes {
    static void Main() {
        string dir = @"F:\SteamLibrary\steamapps\common\Broforce\Broforce_beta_Data\Managed";
        AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += delegate(object s, ResolveEventArgs e) {
            return Assembly.ReflectionOnlyLoadFrom(Path.Combine(dir, new AssemblyName(e.Name).Name + ".dll"));
        };
        Assembly a = Assembly.ReflectionOnlyLoadFrom(Path.Combine(dir, "Assembly-CSharp.dll"));
        Type[] types;
        try { types = a.GetTypes(); } catch (ReflectionTypeLoadException ex) { types = ex.Types; }

        Type info = a.GetType("TriggerActionInfo");
        Type action = a.GetType("TriggerAction");
        Console.WriteLine("TriggerActionInfo isInterface=" + (info != null && info.IsInterface));
        Console.WriteLine("TriggerActionInfo isAbstract=" + (info != null && info.IsAbstract));

        Console.WriteLine("=== implements TriggerActionInfo ===");
        foreach (Type t in types) {
            if (t == null || t.IsInterface) continue;
            try {
                if (info != null && info.IsAssignableFrom(t))
                    Console.WriteLine("  " + t.FullName + " : " + t.BaseType);
            } catch (Exception) { }
        }
        Console.WriteLine("=== derives TriggerAction ===");
        foreach (Type t in types) {
            if (t == null || t.IsInterface || t.IsAbstract || t == action) continue;
            if (action != null && action.IsAssignableFrom(t))
                Console.WriteLine("  " + t.FullName);
        }
    }
}
