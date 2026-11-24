using System.Reflection;
using System.Runtime.Loader;
var root = args[0];
AssemblyLoadContext.Default.Resolving += (ctx, name) => {
    foreach (var dir in new[] { "interop", "core" }) {
        var p = Path.Combine(root, dir, name.Name + ".dll");
        if (File.Exists(p)) return ctx.LoadFromAssemblyPath(p);
    }
    return null;
};
var assemblies = new[] { "Assembly-CSharp.dll", "com.ndemiccreation.foundation.dll" }.Select(n => AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(root, "interop", n)));
foreach (var t in assemblies.SelectMany(a => a.GetTypes()).Where(t => args.Skip(1).Any(s => t.Name == s))) {
    Console.WriteLine($"\nTYPE {t.FullName} BASE {t.BaseType}");
    if (t.IsEnum) Console.WriteLine("ENUM " + string.Join(", ", Enum.GetNames(t)));
    foreach (var f in t.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
        Console.WriteLine($"F {f.FieldType} {f.Name}");
    foreach (var p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
        Console.WriteLine($"P {p.PropertyType} {p.Name}");
    foreach (var m in t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly).Where(m => !m.IsSpecialName))
        Console.WriteLine($"M {m.ReturnType} {m.Name}({string.Join(", ", m.GetParameters().Select(p => p.ParameterType + " " + p.Name))})");
}
