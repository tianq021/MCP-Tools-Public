local me = Game.GetLocalPlayer()
local TX, TY = 18, 20
local function desc(x, y)
  local p = Map.GetPlot(x, y)
  if not p then return 'void' end
  local t = 'flat'
  if p:IsMountain() then t = 'mountain' elseif p:IsHills() then t = 'hills' elseif p:IsWater() then t = 'water' end
  local feat, imp, terr = 'none', 'none', '?'
  pcall(function() local f = GameInfo.Features[p:GetFeatureType()]; if f then feat = f.FeatureType end end)
  pcall(function() local i = GameInfo.Improvements[p:GetImprovementType()]; if i then imp = i.ImprovementType end end)
  pcall(function() local ti = GameInfo.Terrains[p:GetTerrainType()]; if ti then terr = ti.TerrainType end end)
  local units = {}
  pcall(function()
    local pu = Map.GetUnitsAt(x, y)
    if pu then for u in pu:Units() do units[#units + 1] = 'p' .. u:GetOwner() .. '#' .. u:GetID() end end
  end)
  return string.format('%s %s feat=%s imp=%s owner=%s units={%s}', t, terr, feat, imp, tostring(p:GetOwner()), table.concat(units, ','))
end
for dy = -2, 2 do
  for dx = -2, 2 do
    local x, y = TX + dx, TY + dy
    local d = Map.GetPlotDistance(TX, TY, x, y)
    if d == 1 or d == 2 then
      print(string.format('d=%d (%d,%d) %s', d, x, y, desc(x, y)))
    end
  end
end
print('---END---')
