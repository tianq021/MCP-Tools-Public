-- ============================================================
-- volley_city.lua : 对目标城市格齐射（ingame 上下文）
-- 用法: python 脚本\civlua.py lua/volley_city.lua ingame
-- 为什么需要它: 城格上有敌方平民(建造者)时，MCP 的 attack 会打平民、
--              城血一点不掉；此脚本直发 RANGE_ATTACK，绕过目标选择。
-- 注意: 城血读数是异步的 —— 本脚本打印的 AFTER 可能仍是旧值，
--       请在**另一次调用**（隔几秒）再读一次确认实际战果。
-- ============================================================
local TX, TY = 30, 18      -- 目标敌城坐标
local ENEMY_PLAYER = 1     -- 敌方玩家编号
local ME = Game.GetLocalPlayer()

local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index

local function findCity(px, x, y)
  for _, c in Players[px]:GetCities():Members() do
    if c:GetX() == x and c:GetY() == y then return c end
  end
  return nil
end

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

-- 自动收集我方所有弓箭手（UNIT_ARCHER）
local archers = {}
local archerIdx = GameInfo.Units['UNIT_ARCHER'].Index
for _, u in Players[ME]:GetUnits():Members() do
  if u:GetType() == archerIdx then archers[#archers + 1] = u:GetID() end
end

local c = findCity(ENEMY_PLAYER, TX, TY)
if not c then
  print('CITY_GONE (可能已被占领)')
else
  local hp0, mx = stats(c)
  print('BEFORE=' .. tostring(hp0) .. '/' .. tostring(mx))
  for _, id in ipairs(archers) do
    local u = UnitManager.GetUnit(ME, id)
    if u then
      local params = {}
      params[UnitOperationTypes.PARAM_X] = TX
      params[UnitOperationTypes.PARAM_Y] = TY
      local ok, can = pcall(function()
        return UnitManager.CanStartOperation(u, UnitOperationTypes.RANGE_ATTACK, nil, params)
      end)
      local dist = Map.GetPlotDistance(u:GetX(), u:GetY(), TX, TY)
      if ok and can then
        UnitManager.RequestOperation(u, UnitOperationTypes.RANGE_ATTACK, params)
        print(string.format('FIRE id=%d from=%d,%d dist=%d', id, u:GetX(), u:GetY(), dist))
      else
        print(string.format('SKIP id=%d from=%d,%d dist=%d can=%s (已开火/超射程/无视线)',
          id, u:GetX(), u:GetY(), dist, tostring(can)))
      end
    end
  end
  print('AFTER=' .. tostring(stats(c)) .. '/' .. tostring(mx) .. '  <- 异步，需另调一次确认')
end
print('---END---')
