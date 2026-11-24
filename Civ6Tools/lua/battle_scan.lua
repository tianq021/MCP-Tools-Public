-- battle_scan.lua : 全帝国战斗态势扫描（ingame）
local me = Game.GetLocalPlayer()
print('ME|' .. tostring(me))

local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index
for _, c in Players[me]:GetCities():Members() do
  local hp, mx, def, gar = -1, -1, -1, 0
  for _, d in c:GetDistricts():Members() do
    if d:GetType() == ccIdx then
      mx = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON)
      hp = mx - d:GetDamage(DefenseTypes.DISTRICT_GARRISON)
      pcall(function() def = d:GetDefenseStrength() end)
    end
  end
  pcall(function()
    local pu = Map.GetUnitsAt(c:GetX(), c:GetY())
    if pu then
      for u in pu:Units() do
        if u:GetOwner() == me then gar = 1 end
      end
    end
  end)
  print(string.format('CITY|%d|%d|%d|%d|%d|%d|%d',
    c:GetID(), c:GetX(), c:GetY(), hp, mx, def, gar))
end

for _, u in Players[me]:GetUnits():Members() do
  local ui = GameInfo.Units[u:GetType()]
  print(string.format('MINE|%d|%s|%d|%d|%d|%d|%d|%d',
    u:GetID(), ui and ui.UnitType or '?', u:GetX(), u:GetY(),
    u:GetMovesRemaining(), u:GetMaxMoves(),
    u:GetMaxDamage() - u:GetDamage(), u:GetMaxDamage()))
end

for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and pi ~= me then
    for _, u in p:GetUnits():Members() do
      local ui = GameInfo.Units[u:GetType()]
      print(string.format('FOREIGN|%d|%s|%d|%d|%d|%d',
        pi, ui and ui.UnitType or '?', u:GetX(), u:GetY(),
        u:GetMaxDamage() - u:GetDamage(), u:GetMaxDamage()))
    end
  end
end
print('---END---')
