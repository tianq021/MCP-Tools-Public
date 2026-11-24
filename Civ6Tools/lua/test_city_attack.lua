-- 测试：用 Lua 直接对"有敌军驻守的敌城"开火，看掉的是城血还是单位
local me = Game.GetLocalPlayer()
local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index
local TX, TY = 18, 25
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
  print('NO_CITY_AT_TARGET')
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
  print(string.format('TARGET owner=%d hp=%d/%d', owner, hp0, mx))
  local u = UnitManager.GetUnit(me, 1245192)
  print('ARCHER at ' .. tostring(u and u:GetX()) .. ',' .. tostring(u and u:GetY()))
  local params = {}
  params[UnitOperationTypes.PARAM_X] = TX
  params[UnitOperationTypes.PARAM_Y] = TY
  local ok, can = pcall(function()
    return UnitManager.CanStartOperation(u, UnitOperationTypes.RANGE_ATTACK, nil, params)
  end)
  print('CAN=' .. tostring(ok) .. ':' .. tostring(can))
  if ok and can then
    UnitManager.RequestOperation(u, UnitOperationTypes.RANGE_ATTACK, params)
    print('REQUESTED')
  end
  local hp1 = stats(target)
  print(string.format('AFTER hp=%d/%d', hp1, mx))
end
print('---END---')
