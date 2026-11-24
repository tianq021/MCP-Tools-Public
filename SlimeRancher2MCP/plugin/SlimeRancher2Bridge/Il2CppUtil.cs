// Il2CppUtil —— IL2CPP / Il2CppInterop 对象的反射小工具（各 Tools*.cs 共用）
//
// 难点：Il2CppInterop 会把游戏里的集合包装成自己的类型
//   · Il2CppReferenceArray<T> : Il2CppArrayBase<T>（实现 System.Collections.IEnumerable）
//   · Il2CppSystem.Collections.Generic.List<T> / IReadOnlyList<T> 等包装器
//     往往不实现 System.Collections.IEnumerable，但都有 GetEnumerator() / MoveNext() / Current
// 所以 Enumerate 依次尝试三种策略，并回传实际走通的策略名（诊断用）：
//   ① is IEnumerable → foreach
//   ② Length/Count + Item 索引器
//   ③ 反射 GetEnumerator() → MoveNext()/Current 循环
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace SlimeRancher2Bridge
{
    internal static class Il2CppUtil
    {
        // Interop exposes Current on IEnumerator<T> and MoveNext on IEnumerator.
        internal static IEnumerable<T> Items<T>(Il2CppSystem.Collections.Generic.IEnumerable<T> sequence)
        {
            var typed = sequence.GetEnumerator();
            var iterator = typed.Cast<Il2CppSystem.Collections.IEnumerator>();
            try
            {
                while (iterator.MoveNext()) yield return typed.Current;
            }
            finally { typed.TryCast<Il2CppSystem.IDisposable>()?.Dispose(); }
        }

        // 商店这类集合在游戏加载过程中会被自己改动，直接用枚举器会抛
        // InvalidOperationException: Collection was modified。这里优先按
        // Count + 索引器逐项拷贝（不走枚举器的版本检查），失败再退回枚举器。
        internal static List<T> Snapshot<T>(Il2CppSystem.Collections.Generic.IEnumerable<T> sequence)
        {
            var result = new List<T>();
            if (sequence == null) return result;
            var type = sequence.GetType();
            var countProperty = type.GetProperty("Count");
            var itemProperty = type.GetProperty("Item");
            var countMethod = countProperty == null ? type.GetMethod("get_Count", Type.EmptyTypes) : null;
            var itemMethod = itemProperty == null ? type.GetMethod("get_Item", new[] { typeof(int) }) : null;
            if ((countProperty != null || countMethod != null) && (itemProperty != null || itemMethod != null))
            {
                try
                {
                    int count = Convert.ToInt32(countProperty != null
                        ? countProperty.GetValue(sequence)
                        : countMethod.Invoke(sequence, null));
                    for (int i = 0; i < count; ++i)
                    {
                        object value = itemProperty != null
                            ? itemProperty.GetValue(sequence, new object[] { i })
                            : itemMethod.Invoke(sequence, new object[] { i });
                        if (value is T typed) result.Add(typed);
                    }
                    return result;
                }
                catch { result.Clear(); }
            }
            try
            {
                foreach (var item in Items(sequence)) if (item != null) result.Add(item);
            }
            catch { }
            return result;
        }

        internal static string Localized(UnityEngine.Localization.LocalizedString text)
        {
            if (text == null) return null;
            try { return text.GetLocalizedString(); }
            catch (Exception) { return null; }
        }

        // ---------------- 成员读写 ----------------

        internal static object Member(object instance, string name)
        {
            if (instance == null) return null;
            Type type = instance.GetType();
            var property = type.GetProperty(name);
            if (property != null) return property.GetValue(instance);
            var field = type.GetField(name);
            if (field != null) return field.GetValue(instance);
            throw new MissingMemberException(type.FullName, name);
        }

        internal static object Try(object instance, string name)
        {
            try { return Member(instance, name); }
            catch { return null; }
        }

        // ---------------- 类型查找 ----------------

        internal static Type FindType(string name)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                var type = assembly.GetType(name)
                    ?? assembly.GetType("Il2Cpp." + name)
                    ?? assembly.GetType("Il2CppMonomiPark.SlimeRancher." + name);
                if (type != null) return type;
            }
            return null;
        }

        // ---------------- 集合枚举 ----------------

        internal static List<object> Enumerate(object sequence)
        {
            return Enumerate(sequence, out _);
        }

        internal static List<object> Enumerate(object sequence, out string strategy)
        {
            strategy = "null";
            var result = new List<object>();
            if (sequence == null) return result;

            // ① 实现 System.Collections.IEnumerable
            if (sequence is IEnumerable enumerable)
            {
                try
                {
                    foreach (var item in enumerable) if (item != null) result.Add(item);
                    strategy = "ienumerable";
                    return result;
                }
                catch
                {
                    result.Clear();
                }
            }

            var type = sequence.GetType();

            // ② Length/Count + Item 索引器（属性或 get_ 方法）
            var lengthProperty = type.GetProperty("Length") ?? type.GetProperty("Count");
            var itemProperty = type.GetProperty("Item");
            var lengthMethod = lengthProperty == null
                ? (type.GetMethod("get_Length", Type.EmptyTypes) ?? type.GetMethod("get_Count", Type.EmptyTypes))
                : null;
            var itemMethod = itemProperty == null
                ? (type.GetMethod("get_Item", new[] { typeof(int) }) ?? type.GetMethod("get_Item"))
                : null;
            if (lengthMethod != null && itemMethod != null)
            {
                try
                {
                    int length = Convert.ToInt32(lengthMethod.Invoke(sequence, null));
                    for (int i = 0; i < length; i++)
                    {
                        var item = itemMethod.Invoke(sequence, new object[] { i });
                        if (item != null) result.Add(item);
                    }
                    strategy = "length_item_method";
                    return result;
                }
                catch
                {
                    result.Clear();
                }
            }
            if (lengthProperty != null && itemProperty != null)
            {
                try
                {
                    int length = Convert.ToInt32(lengthProperty.GetValue(sequence));
                    for (int i = 0; i < length; i++)
                    {
                        var item = itemProperty.GetValue(sequence, new object[] { i });
                        if (item != null) result.Add(item);
                    }
                    strategy = "length_item";
                    return result;
                }
                catch
                {
                    result.Clear();
                }
            }

            // ③ 反射调用任意 *GetEnumerator()（含显式接口实现名）→ *MoveNext / Current / get_Current
            try
            {
                MethodInfo getEnumerator = null;
                foreach (var method in type.GetMethods())
                {
                    if (method.GetParameters().Length == 0
                        && method.Name.Contains("GetEnumerator"))
                    {
                        getEnumerator = method;
                        break;
                    }
                }
                if (getEnumerator != null)
                {
                    object enumerator = getEnumerator.Invoke(sequence, null);
                    if (enumerator != null)
                    {
                        // 能直接当 System.Collections.IEnumerator 用就直接用
                        if (enumerator is IEnumerator standard)
                        {
                            while (standard.MoveNext())
                            {
                                var item = standard.Current;
                                if (item != null) result.Add(item);
                            }
                            strategy = "standard_enumerator";
                            return result;
                        }
                        var enumeratorType = enumerator.GetType();
                        MethodInfo moveNext = null;
                        foreach (var method in enumeratorType.GetMethods())
                        {
                            if (method.GetParameters().Length == 0
                                && method.Name.Contains("MoveNext"))
                            {
                                moveNext = method;
                                break;
                            }
                        }
                        object current = null;
                        foreach (var member in enumeratorType.GetMembers())
                        {
                            if (member.Name.Contains("Current"))
                            {
                                current = member;
                                break;
                            }
                        }
                        if (moveNext != null && current != null)
                        {
                            for (int guard = 0; guard < 100000; guard++)
                            {
                                object more = moveNext.Invoke(enumerator, null);
                                if (!(more is bool flag) || !flag) break;
                                object item = current is PropertyInfo property ? property.GetValue(enumerator)
                                    : current is MethodInfo methodInfo ? methodInfo.Invoke(enumerator, null) : null;
                                if (item != null) result.Add(item);
                            }
                            strategy = "reflection_enumerator";
                            return result;
                        }
                    }
                }
            }
            catch
            {
                result.Clear();
                strategy = "reflection_enumerator_failed";
                return result;
            }

            strategy = "none";
            return result;
        }

        /// <summary>诊断：描述一个对象在枚举层面的形态，写进工具返回里方便排查。</summary>
        internal static Dictionary<string, object> Describe(object sequence)
        {
            if (sequence == null) return new() { ["type"] = null };
            var type = sequence.GetType();
            return new()
            {
                ["type"] = type.FullName,
                ["is_ienumerable"] = sequence is IEnumerable,
                ["has_length_or_count"] = type.GetProperty("Length") != null || type.GetProperty("Count") != null,
                ["has_item"] = type.GetProperty("Item") != null,
                ["has_getenumerator"] = type.GetMethod("GetEnumerator", Type.EmptyTypes) != null,
            };
        }

        // ---------------- 场景对象查找 ----------------

        /// <summary>找场景里所有某类型的对象；优先用泛型 FindObjectsByType&lt;T&gt;（IL2CPP 下 Type 参数会被包装）。</summary>
        internal static List<object> FindSceneObjects(string typeName, out string diagnostic)
        {
            diagnostic = "not_run";
            var result = new List<object>();
            var type = FindType(typeName);
            if (type == null)
            {
                diagnostic = "type_not_found:" + typeName;
                return result;
            }
            var objectType = Type.GetType("UnityEngine.Object, UnityEngine.CoreModule");
            var sortModeType = Type.GetType("UnityEngine.FindObjectsSortMode, UnityEngine.CoreModule");
            if (objectType == null || sortModeType == null)
            {
                diagnostic = "unity_types_missing object=" + (objectType != null) + " sortmode=" + (sortModeType != null);
                return result;
            }
            try
            {
                MethodInfo generic = null;
                foreach (var method in objectType.GetMethods())
                {
                    if (method.Name == "FindObjectsByType" && method.IsGenericMethodDefinition
                        && method.GetParameters().Length == 1)
                    {
                        generic = method;
                        break;
                    }
                }
                if (generic == null)
                {
                    diagnostic = "generic_overload_missing";
                    return result;
                }
                var closed = generic.MakeGenericMethod(type);
                object sortMode = Enum.Parse(sortModeType, "None");
                object raw = closed.Invoke(null, new[] { sortMode });
                result = Enumerate(raw, out string strategy);
                diagnostic = "ok generic T=" + type.Name + " raw=" + Describe(raw)["type"]
                    + " strategy=" + strategy + " count=" + result.Count;
            }
            catch (Exception e)
            {
                diagnostic = "invoke_failed:" + e.GetBaseException().Message;
            }
            return result;
        }
        // ---------------- 标量 / 向量 / 调用 ----------------

        internal static int IntOf(object value, int fallback = 0)
        {
            if (value == null) return fallback;
            try { return Convert.ToInt32(value); } catch { return fallback; }
        }

        internal static bool BoolOf(object value, bool fallback = false)
        {
            if (value == null) return fallback;
            try { return value is bool flag ? flag : Convert.ToBoolean(value); } catch { return fallback; }
        }

        internal static float Float(object vector, string axis)
        {
            if (vector == null) return 0f;
            var type = vector.GetType();
            var field = type.GetField(axis);
            if (field != null) return Convert.ToSingle(field.GetValue(vector));
            var property = type.GetProperty(axis);
            return property != null ? Convert.ToSingle(property.GetValue(vector)) : 0f;
        }

        internal static float[] PositionOf(object component)
        {
            object transform = Try(component, "transform");
            if (transform == null) return null;
            object position = Try(transform, "position");
            if (position == null) return null;
            return new[] { Float(position, "x"), Float(position, "y"), Float(position, "z") };
        }

        internal static float[] ForwardOf(object component)
        {
            object transform = Try(component, "transform");
            if (transform == null) return null;
            object forward = Try(transform, "forward");
            if (forward == null) return null;
            return new[] { Float(forward, "x"), Float(forward, "y"), Float(forward, "z") };
        }

        /// <summary>把实现类参数转成接口包装类型（Il2CppInterop 接口参数需要同指针的接口对象）。</summary>
        internal static object ConvertArg(object value, Type targetType)
        {
            if (value == null || targetType == null) return value;
            var valueType = value.GetType();
            if (targetType.IsAssignableFrom(valueType)) return value;
            try
            {
                // Il2CppInterop 的「接口」在生成程序集里是继承 Il2CppObjectBase 的类，带 (IntPtr) 构造
                if (value is Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase objBase && objBase.Pointer != IntPtr.Zero)
                {
                    var ctor = targetType.GetConstructor(new[] { typeof(IntPtr) });
                    if (ctor != null) return ctor.Invoke(new object[] { objBase.Pointer });
                }
            }
            catch
            {
            }
            return value;
        }

        internal static string LastInvokeError;

        internal static object InvokeMethod(MethodInfo method, object target, object[] args)
        {
            if (method == null) return null;
            try
            {
                var parameters = method.GetParameters();
                var converted = new object[args.Length];
                for (int i = 0; i < args.Length; i++)
                    converted[i] = i < parameters.Length ? ConvertArg(args[i], parameters[i].ParameterType) : args[i];
                return method.Invoke(target, converted);
            }
            catch (Exception e)
            {
                LastInvokeError = method.DeclaringType?.Name + "." + method.Name + ": "
                    + (e is TargetInvocationException tie ? tie.GetBaseException().Message : e.Message);
                return null;
            }
        }

        internal static object Invoke(object target, string method, object[] args = null)
        {
            if (target == null) return null;
            try
            {
                System.Reflection.MethodInfo info = null;
                foreach (var candidate in target.GetType().GetMethods())
                {
                    if (candidate.Name != method) continue;
                    var parameters = candidate.GetParameters();
                    if (args == null)
                    {
                        if (parameters.Length == 0) { info = candidate; break; }
                        continue;
                    }
                    if (parameters.Length == args.Length) { info = candidate; break; }
                }
                if (info == null) return null;
                return InvokeMethod(info, target, args);
            }
            catch
            {
                return null;
            }
        }
    }
}






