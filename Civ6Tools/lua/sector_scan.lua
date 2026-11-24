-- sector_scan.lua : 胶东DSH 防区扫描（ingame）
local me = Game.GetLocalPlayer()
local CX, CY = 14, 23

local function dist(x1, y1, x2, y2)
  return Map.GetPlotDistance(x1, y1, x2, y2)
end

local city = nil
for _, c in Players[me]:GetCities():Members() do
  if c:GetX() == CX and c:GetY() == CY then city = c end
end

local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index
local hp, mx, def = -1, -1, -1
if city then
  for _, d in city:GetDistricts():Members() do
    if d:GetType() == ccIdx then
      mx = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON)
      hp = mx - d:GetDamage(DefenseTypes.DISTRICT_GARRISON)
      pcall(function() def = d:GetDefenseStrength() end)
    end
  end
end
print(string.format('CITY|%s|%s|%s|%s', tostring(hp), tostring(mx), tostring(def), city and 'yes' or 'no'))

for _, u in Players[me]:GetUnits():Members() do
  local d = dist(u:GetX(), u:GetY(), CX, CY)
  if d <= 5 then
    local ui = GameInfo.Units[u:GetType()]
    print(string.format('MINE|%d|%s|%d|%d|%d|%d|%d|%d',
      u:GetID(), ui and ui.UnitType or '?', u:GetX(), u:GetY(),
      u:GetMovesRemaining(), u:GetMaxMoves(),
      u:GetMaxDamage() - u:GetDamage(), u:GetMaxDamage()))
  end
end

for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and pi ~= me then
    for _, u in p:GetUnits():Members() do
      local d = dist(u:GetX(), u:GetY(), CX, CY)
      if d <= 6 then
        local ui = GameInfo.Units[u:GetType()]
        print(string.format('FOREIGN|%d|%s|%d|%d|%d|%d',
          pi, ui and ui.UnitType or '?', u:GetX(), u:GetY(),
          u:GetMaxDamage() - u:GetDamage(), u:GetMaxDamage()))
      end
    end
  end
end
print('---END---')
