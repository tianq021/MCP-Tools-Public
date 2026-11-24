local me = Game.GetLocalPlayer()
local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index
local TX, TY = 18, 20
local target
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and pi ~= me then
    for _, c in p:GetCities():Members() do
      if c:GetX() == TX and c:GetY() == TY then target = c end
    end
  end
end
if target then
  for _, d in target:GetDistricts():Members() do
    if d:GetType() == ccIdx then
      print(string.format('GARRISON %d/%d  OUTER %d/%d',
        d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) - d:GetDamage(DefenseTypes.DISTRICT_GARRISON),
        d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON),
        d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) - d:GetDamage(DefenseTypes.DISTRICT_OUTER),
        d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER)))
    end
  end
end
local pu = Map.GetUnitsAt(TX, TY)
if pu then
  for u in pu:Units() do
    local ui = GameInfo.Units[u:GetType()]
    print(string.format('ON_CITY player=%d id=%d type=%s hp=%d/%d',
      u:GetOwner(), u:GetID(), ui and ui.UnitType or '?',
      u:GetMaxDamage() - u:GetDamage(), u:GetMaxDamage()))
  end
else
  print('ON_CITY none')
end
-- 我方可入城的近战单位
for _, u in Players[me]:GetUnits():Members() do
  local ui = GameInfo.Units[u:GetType()]
  local ut = ui and ui.UnitType or '?'
  local d = Map.GetPlotDistance(u:GetX(), u:GetY(), TX, TY)
  if d <= 1 then
    print(string.format('ADJ id=%d type=%s moves=%d', u:GetID(), ut, u:GetMovesRemaining()))
  end
end
print('---END---')
