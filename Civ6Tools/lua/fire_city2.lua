-- fire_city.lua v2 : 让我方所有远程单位（弓箭手/弩手/散兵/追猎者等，只要有射程）对目标城市格齐射
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
  local RANGED = {
    UNIT_ARCHER = true, UNIT_CROSSBOWMAN = true, UNIT_SKIRMISHER = true, UNIT_RANGER = true,
    UNIT_SLINGER = true, UNIT_CATAPULT = true, UNIT_FIELD_CANNON = true, UNIT_BOMBARD = true,
    UNIT_ARTILLERY = true, UNIT_MACHINE_GUN = true, UNIT_ARCHER_BABYLON = true,
    UNIT_TREBUCHET = true, UNIT_CHINESE_CROUCHING_TIGER = true,
    UNIT_QHT_BLOOD_GUARD = false,
  }
  for _, u in Players[me]:GetUnits():Members() do
    local ui = GameInfo.Units[u:GetType()]
    local ut = ui and ui.UnitType or '?'
    if RANGED[ut] and u:GetMovesRemaining() > 0 then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = TX
      params[UnitOperationTypes.PARAM_Y] = TY
      local ok, can = pcall(function()
        return UnitManager.CanStartOperation(u, UnitOperationTypes.RANGE_ATTACK, nil, params)
      end)
      if ok and can then
        UnitManager.RequestOperation(u, UnitOperationTypes.RANGE_ATTACK, params)
        print(string.format('FIRE id=%d %s at=%d,%d ', u:GetID(), ut, u:GetX(), u:GetY()))
      else
        print(string.format('SKIP id=%d %s at=%d,%d', u:GetID(), ut, u:GetX(), u:GetY()))
      end
    end
  end
  print(string.format('AFTER hp=%d/%d (异步)', stats(target), mx))
end
print('---END---')
