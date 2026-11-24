// Offline metadata/IL reader. ReflectionOnly prevents execution of game code.
using System;
using System.IO;
using System.Reflection;
using System.Reflection.Emit;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

class InspectAssembly
{
    static string managed;
    static Dictionary<short, OpCode> opcodes = new Dictionary<short, OpCode>();
    const BindingFlags Flags = BindingFlags.Public | BindingFlags.NonPublic |
        BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;

    static string TypeName(Type t) { return t == null ? "<null>" : (t.FullName ?? t.ToString()); }
    static string Parameters(MethodBase method)
    {
        List<string> values = new List<string>();
        foreach (ParameterInfo p in method.GetParameters())
            values.Add((p.IsOut ? "out " : "") + TypeName(p.ParameterType) + " " + p.Name);
        return String.Join(", ", values.ToArray());
    }
    static string Signature(MethodBase method)
    {
        MethodInfo info = method as MethodInfo;
        return (method.IsPublic ? "public " : method.IsFamily ? "protected " : "nonpublic ") +
            (method.IsStatic ? "static " : "") + (method.IsVirtual ? "virtual " : "") +
            (info == null ? "" : TypeName(info.ReturnType) + " ") +
            TypeName(method.DeclaringType) + "::" + method.Name + "(" + Parameters(method) + ")";
    }
    static IEnumerable<MethodBase> Methods(Type type)
    {
        foreach (ConstructorInfo c in type.GetConstructors(Flags)) yield return c;
        foreach (MethodInfo m in type.GetMethods(Flags)) yield return m;
    }
    static string Resolve(MethodBase method, int token, bool isString)
    {
        try
        {
            if (isString) return "\"" + method.Module.ResolveString(token).Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
            Type[] typeArgs = method.DeclaringType.IsGenericType ? method.DeclaringType.GetGenericArguments() : null;
            Type[] methodArgs = method.IsGenericMethod ? method.GetGenericArguments() : null;
            MemberInfo member = method.Module.ResolveMember(token, typeArgs, methodArgs);
            return TypeName(member.DeclaringType) + "::" + member.ToString();
        }
        catch { return "TOKEN_0x" + token.ToString("X8"); }
    }
    static void DumpIL(MethodBase method)
    {
        Console.WriteLine("\nMETHOD " + Signature(method) + " token=0x" + method.MetadataToken.ToString("X8"));
        MethodBody body = method.GetMethodBody();
        if (body == null) { Console.WriteLine("<no IL body>"); return; }
        foreach (LocalVariableInfo local in body.LocalVariables)
            Console.WriteLine("LOCAL " + local.LocalIndex + " " + TypeName(local.LocalType));
        byte[] bytes = body.GetILAsByteArray();
        for (int i = 0; i < bytes.Length; )
        {
            int start = i;
            short key = bytes[i++];
            if (key == 0xfe) key = unchecked((short)(0xfe00 | bytes[i++]));
            OpCode op = opcodes[key];
            object value = "";
            int size = 0;
            switch (op.OperandType)
            {
                case OperandType.InlineNone: break;
                case OperandType.ShortInlineI: size = 1; value = (sbyte)bytes[i]; break;
                case OperandType.ShortInlineVar: size = 1; value = bytes[i]; break;
                case OperandType.InlineVar: size = 2; value = BitConverter.ToUInt16(bytes, i); break;
                case OperandType.InlineI: size = 4; value = BitConverter.ToInt32(bytes, i); break;
                case OperandType.InlineI8: size = 8; value = BitConverter.ToInt64(bytes, i); break;
                case OperandType.ShortInlineR: size = 4; value = BitConverter.ToSingle(bytes, i); break;
                case OperandType.InlineR: size = 8; value = BitConverter.ToDouble(bytes, i); break;
                case OperandType.ShortInlineBrTarget:
                    size = 1; value = "IL_" + (i + 1 + (sbyte)bytes[i]).ToString("X4"); break;
                case OperandType.InlineBrTarget:
                    size = 4; value = "IL_" + (i + 4 + BitConverter.ToInt32(bytes, i)).ToString("X4"); break;
                case OperandType.InlineSwitch:
                    int count = BitConverter.ToInt32(bytes, i);
                    size = 4 + count * 4;
                    List<string> labels = new List<string>();
                    for (int n = 0; n < count; n++) labels.Add("IL_" + (i + size + BitConverter.ToInt32(bytes, i + 4 + n * 4)).ToString("X4"));
                    value = String.Join(", ", labels.ToArray()); break;
                case OperandType.InlineSig:
                    size = 4; value = "SIGNATURE_0x" + BitConverter.ToInt32(bytes, i).ToString("X8"); break;
                default:
                    size = 4; value = Resolve(method, BitConverter.ToInt32(bytes, i), op.OperandType == OperandType.InlineString); break;
            }
            i += size;
            Console.WriteLine("IL_" + start.ToString("X4") + " " + op.Name + " " + value);
        }
        foreach (ExceptionHandlingClause clause in body.ExceptionHandlingClauses)
            Console.WriteLine("EH " + clause.Flags + " try=" + clause.TryOffset + "+" + clause.TryLength + " handler=" + clause.HandlerOffset + "+" + clause.HandlerLength);
    }
    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length < 4) { Console.Error.WriteLine("Usage: InspectAssembly <Managed directory> <assembly filename> <list|types|il> <regex>"); return 2; }
        managed = Path.GetFullPath(args[0]);
        AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += delegate(object sender, ResolveEventArgs e)
        {
            string path = Path.Combine(managed, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(path) ? Assembly.ReflectionOnlyLoadFrom(path) : Assembly.ReflectionOnlyLoad(e.Name);
        };
        foreach (FieldInfo f in typeof(OpCodes).GetFields())
            if (f.FieldType == typeof(OpCode)) { OpCode op = (OpCode)f.GetValue(null); opcodes[op.Value] = op; }
        try
        {
            Assembly assembly = Assembly.ReflectionOnlyLoadFrom(Path.Combine(managed, args[1]));
            Regex filter = new Regex(args[3], RegexOptions.IgnoreCase);
            Console.WriteLine("ASSEMBLY " + assembly.FullName + " MVID=" + assembly.ManifestModule.ModuleVersionId);
            int matched = 0;
            foreach (Type type in assembly.GetTypes())
            {
                if (args[2] == "il")
                {
                    foreach (MethodBase method in Methods(type))
                        if (filter.IsMatch(type.FullName + "::" + method.Name)) { DumpIL(method); matched++; }
                    continue;
                }
                if (!filter.IsMatch(type.FullName)) continue;
                matched++;
                Console.WriteLine("\nTYPE " + type.FullName + " base=" + TypeName(type.BaseType));
                if (args[2] == "list") continue;
                foreach (FieldInfo f in type.GetFields(Flags))
                {
                    Console.WriteLine("FIELD " + (f.IsPublic ? "public " : "nonpublic ") + (f.IsStatic ? "static " : "") +
                        TypeName(f.FieldType) + " " + f.Name + (f.IsLiteral ? " = " + f.GetRawConstantValue() : ""));
                }
                foreach (PropertyInfo p in type.GetProperties(Flags)) Console.WriteLine("PROPERTY " + p);
                foreach (MethodBase method in Methods(type)) Console.WriteLine("MEMBER " + Signature(method));
            }
            Console.WriteLine("\nMATCHED " + matched);
            return matched == 0 ? 3 : 0;
        }
        catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
