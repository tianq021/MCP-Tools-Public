# -*- coding: utf-8 -*-
"""检查 SRSingleton<T> 的公开成员（判断 mod 里能否用 SceneContext.Instance）。"""
import sys
import dnfile

DLL = r"F:\SteamLibrary\steamapps\common\Slime Rancher\SlimeRancher_Data\Managed\Assembly-CSharp.dll"

pe = dnfile.dnPE(DLL)
for t in pe.net.mdtables.TypeDef:
    n = str(t.TypeName)
    if n.startswith("SRSingleton"):
        flags = t.Flags
        print("type:", n, "| ns:", str(t.TypeNamespace), "| flags:", str(flags))
        for idx in t.FieldList:
            r = getattr(idx, "row", None)
            if r is not None:
                print("   field:", r.Name)
        for idx in t.MethodList:
            r = getattr(idx, "row", None)
            if r is not None:
                print("   method:", r.Name)

