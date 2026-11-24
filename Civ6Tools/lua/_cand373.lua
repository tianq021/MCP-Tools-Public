-- candidate city sites: distance from (13,12) + terrain info + found-city legality guesses
local pid = Game.GetLocalPlayer()
local cx, cy = 13, 12
local cands = {
  {12,10},{12,11},{11,11},{10,10},{10,11},{11,14},{10,14},{9,14},{9,13},
  {15,14},{16,13},{16,15},{17,14},{11,16},{12,16},{13,16},{14,16},{15,16},
  {8,12},{7,12},{8,15},{9,16},{17,10},{18,12},{19,12},{12,7},{14,6},{16,6},
}
print("CAND|dist|terrain|feature|resource|water")
for _, c in ipairs(cands) do
  local x, y = c[1], c[2]
  local p = Map.GetPlot(x, y)
  if p == nil then
    print(string.format("%d,%d|OUT_OF_MAP", x, y))
  else
    local d = Map.GetPlotDistance(cx, cy, x, y)
    local terr = "?"
    local ok1, v1 = pcall(function() return p:GetTerrainType() end)
    if ok1 and v1 ~= nil then terr = tostring(v1) end
    local feat = "-"
    local ok2, v2 = pcall(function() return p:GetFeatureType() end)
    if ok2 and v2 ~= nil and v2 >= 0 then feat = tostring(v2) end
    local res = "-"
    local ok3, v3 = pcall(function() return p:GetResourceType() end)
    if ok3 and v3 ~= nil and v3 >= 0 then res = tostring(v3) end
    -- resource type names
    local rname = "-"
    if res ~= "-" then
      local okr, rv = pcall(function() return GameInfo.Resources[res].ResourceType end)
      if okr then rname = tostring(rv) end
    end
    local tname = terr
    local okt, tv = pcall(function() return GameInfo.Terrains[terr].TerrainType end)
    if okt then tname = tostring(tv) end
    local fname = "-"
    if feat ~= "-" then
      local okf, fv = pcall(function() return GameInfo.Features[feat].FeatureType end)
      if okf then fname = tostring(fv) end
    end
    local water = "land"
    local okw, w = pcall(function() return p:IsWater() end)
    if okw and w then water = "WATER" end
    local canFound = "?"
    local okc, c1 = pcall(function() return Players[pid]:CanFoundCity(x, y) end)
    if okc then canFound = tostring(c1) end
    local okc2, c2 = pcall(function() return p:CanFoundCity(pid) end)
    local can2 = "?"
    if okc2 then can2 = tostring(c2) end
    print(string.format("%d,%d|d=%d|%s|%s|%s|%s|found=%s/%s", x, y, d, tname, fname, rname, water, canFound, can2))
  end
end
print("---END---")
