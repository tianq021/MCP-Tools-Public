-- 测试：近战单位用 Lua MOVE_TO 打到"无驻军的敌城"，是否结算为攻城
local me = Game.GetLocalPlayer()
local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index
local TX, TY = 18, 20
local target, owner = nil, -1
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and pi ~= me then
    for _, c in p:GetCities():Members() do
      if c:GetX() == TX and c:GetY() == TY then target = c; owner = pi end
    end
  end
end
if not target then print('NO_CITY') else
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
  local u = UnitManager.GetUnit(me, 1179657)
  print('WARRIOR at ' .. tostring(u and u:GetX()) .. ',' .. tostring(u and u:GetY()))
  local params = {}
  params[UnitOperationTypes.PARAM_X] = TX
  params[UnitOperationTypes.PARAM_Y] = TY
  local okM, canM = pcall(function()
    return UnitManager.CanStartOperation(u, UnitOperationTypes.MOVE_TO, nil, params)
  end)
  print('MOVE_TO_CAN=' .. tostring(okM) .. ':' .. tostring(canM))
  if okM and canM then
    UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params)
    print('MOVE_TO_REQUESTED')
  end
  print(string.format('AFTER hp=%d/%d (异步)', stats(target), mx))
end
print('---END---')
