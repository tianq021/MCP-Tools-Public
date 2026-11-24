using Mono.Cecil;
var asmPath = args[0];
var mode = args[1];
var f1 = args.Length > 2 ? args[2] : "";
var f2 = args.Length > 3 ? args[3] : "";
var asm = AssemblyDefinition.ReadAssembly(asmPath, new ReaderParameters { ReadSymbols = false });
IEnumerable<TypeDefinition> AllTypes(TypeDefinition t) { yield return t; foreach (var n in t.NestedTypes.SelectMany(AllTypes)) yield return n; }
var types = asm.MainModule.Types.SelectMany(AllTypes).ToList();
if (mode == "type") {
    foreach (var t in types.Where(t => t.FullName.Equals(f1, StringComparison.OrdinalIgnoreCase) || t.FullName.Contains(f1, StringComparison.OrdinalIgnoreCase))) {
        Console.WriteLine($"TYPE {t.FullName} : {t.BaseType?.FullName}");
        foreach (var f in t.Fields) if (!f.Name.StartsWith("NativeF")) Console.WriteLine($"   field {f.FieldType.Name} {f.Name}");
        foreach (var p in t.Properties) Console.WriteLine($"   prop  {p.PropertyType.Name} {p.Name}");
        foreach (var m in t.Methods) {
            var ps = string.Join(", ", m.Parameters.Select(x => x.ParameterType.Name + " " + x.Name));
            Console.WriteLine($"   method {m.ReturnType.Name} {m.Name}({ps})");
        }
    }
} else if (mode == "actionmembers") {
    // find members whose type name is Action / Action`1 within types whose namespace contains f1
    foreach (var t in types.Where(t => t.FullName.Contains(f1, StringComparison.OrdinalIgnoreCase))) {
        foreach (var f in t.Fields) if (!f.Name.StartsWith("NativeF") && (f.FieldType.Name == "Action" || f.FieldType.Name == "Action`1")) Console.WriteLine($"FIELD {t.FullName}::{f.Name} : {f.FieldType.Name}");
        foreach (var p in t.Properties) if (p.PropertyType.Name == "Action" || p.PropertyType.Name == "Action`1") Console.WriteLine($"PROP  {t.FullName}::{p.Name} : {p.PropertyType.Name}");
        foreach (var m in t.Methods) if (m.ReturnType.Name == "Action" || m.ReturnType.Name == "Action`1") Console.WriteLine($"RET   {t.FullName}::{m.Name} : {m.ReturnType.Name}");
    }
}
