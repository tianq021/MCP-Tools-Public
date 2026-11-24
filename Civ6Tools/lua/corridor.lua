local function desc(x, y)
  local p = Map.GetPlot(x, y)
  if not p then return 'void' end
  local t = 'flat'
  if p:IsMountain() then t = 'MTN' elseif p:IsHills() then t = 'hills' elseif p:IsWater() then t = 'water' end
  local feat = ''
  pcall(function() local f = GameInfo.Features[p:GetFeatureType()]; if f then feat = 'f:'..f.FeatureType:gsub('FEATURE_','') end end)
  local units = ''
  pcall(function()
    local pu = Map.GetUnitsAt(x, y)
    if pu then local n = 0; for u in pu:Units() do n = n + 1 end; if n > 0 then units = ' ['..n..'u]' end end
  end)
  return string.format('(%d,%d) %s %s owner=%s%s', x, y, t, feat, tostring(p:GetOwner()), units)
end
for y = 10, 22 do
  local line = {}
  for x = 36, 43 do
    local p = Map.GetPlot(x, y)
    local mark = '.'
    if p then
      if p:IsMountain() then mark = 'M' elseif p:IsHills() then mark = 'h' elseif p:IsWater() then mark = 'w'
      elseif p:IsImpassable() then mark = 'X' else mark = ',' end
    end
    line[#line + 1] = mark
  end
  print(string.format('y=%2d  %s', y, table.concat(line)))
end
for _, xy in ipairs({ {36,20},{37,19},{38,19},{39,19},{40,18},{40,17},{41,16},{41,15},{41,14},{41,13},{41,12} }) do
  print(desc(xy[1], xy[2]))
end
print('---END---')
