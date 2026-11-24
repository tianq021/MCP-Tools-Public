-- siege_melee.lua : 让贴城的我方近战单位对"无驻军敌城"发起攻击（MOVE_TO 到城市格）
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
  print(string.format('BEFORE owner=%d hp=%d/%d', owner, stats(target)))
  for _, u in Players[me]:GetUnits():Members() do
    local ui = GameInfo.Units[u:GetType()]
    local ut = ui and ui.UnitType or '?'
    local isMelee = (ut:find('WARRIOR') or ut:find('HORSEMAN') or ut:find('SPEARMAN')
      or ut:find('SWORDSMAN') or ut:find('BLOOD_GUARD') or ut:find('MAN_AT_ARMS')) ~= nil
    if isMelee and u:GetMovesRemaining() > 0 then
      local d = Map.GetPlotDistance(u:GetX(), u:GetY(), TX, TY)
      if d <= 1 then
        local params = {}
        params[UnitOperationTypes.PARAM_X] = TX
        params[UnitOperationTypes.PARAM_Y] = TY
        local ok, can = pcall(function()
          return UnitManager.CanStartOperation(u, UnitOperationTypes.MOVE_TO, nil, params)
        end)
        if ok and can then
          UnitManager.RequestOperation(u, UnitOperationTypes.MOVE_TO, params)
          print(string.format('ATTACK id=%d type=%s from=%d,%d', u:GetID(), ut, u:GetX(), u:GetY()))
        else
          print(string.format('SKIP id=%d type=%s can=%s', u:GetID(), ut, tostring(can)))
        end
      else
        print(string.format('FAR id=%d type=%s d=%d', u:GetID(), ut, d))
      end
    end
  end
  print(string.format('AFTER hp=%d/%d (异步)', stats(target)))
end
print('---END---')
