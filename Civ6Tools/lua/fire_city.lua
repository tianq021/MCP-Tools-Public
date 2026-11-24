-- fire_city.lua : 用我方所有弓箭手对目标城市格齐射（打城血，绕过驻军单位）
local me = Game.GetLocalPlayer()
local TX, TY = 18, 25
local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index

local target, owner = nil, -1
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and pi ~= me then
    for _, c in p:GetCities():Members() do
      if c:GetX() == TX and c:GetY() == TY then target = c; owner = pi end
    end
  end
end
if not target then
  print('NO_CITY')
else
  local function stats(c)
    local hp, mx = -1, -1
    for _, d in c:GetDistricts():Members() do
      if d:GetType() == ccIdx then
        mx = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON)
        hp = mx - d:GetDamage(DefenseTypes.DISTRICT_GARRISON)
      end
    end
    return hp, mx
  end
  local hp0, mx = stats(target)
  print(string.format('BEFORE owner=%d hp=%d/%d', owner, hp0, mx))
  local archerIdx = GameInfo.Units['UNIT_ARCHER'].Index
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetType() == archerIdx then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = TX
      params[UnitOperationTypes.PARAM_Y] = TY
      local ok, can = pcall(function()
        return UnitManager.CanStartOperation(u, UnitOperationTypes.RANGE_ATTACK, nil, params)
      end)
      if ok and can then
        UnitManager.RequestOperation(u, UnitOperationTypes.RANGE_ATTACK, params)
        print(string.format('FIRE id=%d at=%d,%d', u:GetID(), u:GetX(), u:GetY()))
      else
        print(string.format('SKIP id=%d at=%d,%d can=%s', u:GetID(), u:GetX(), u:GetY(), tostring(can)))
      end
    end
  end
  print(string.format('AFTER hp=%d/%d (异步,需另调确认)', stats(target), mx))
end
print('---END---')
