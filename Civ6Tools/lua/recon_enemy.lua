-- ============================================================
-- recon_enemy.lua : 敌城防御侦察 + 射击位/领土侦察（ingame 上下文）
-- 用法: python 脚本\civlua.py lua/recon_enemy.lua ingame
-- 改这里 ↓ 目标城市坐标 / 敌方玩家编号 / 要看的我方城市
-- ============================================================
local TARGET_X, TARGET_Y = 30, 18   -- 目标敌城坐标
local ENEMY_PLAYER = 1              -- 敌方玩家编号（见 get_diplomacy）
local ME = Game.GetLocalPlayer()
local OWN_CITIES = {}               -- 留空 = 看全部我方城市周边

local ccIdx = GameInfo.Districts['DISTRICT_CITY_CENTER'].Index

local function cityStats(c)
  local info = { def = 0, wallHP = 0, wallMax = 0, garHP = 0, garMax = 0, dist = {} }
  for _, d in c:GetDistricts():Members() do
    if d:GetType() == ccIdx then
      pcall(function()
        info.def = d:GetDefenseStrength() or 0
        info.wallMax = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) or 0
        info.wallHP = info.wallMax - (d:GetDamage(DefenseTypes.DISTRICT_OUTER) or 0)
        info.garMax = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) or 0
        info.garHP = info.garMax - (d:GetDamage(DefenseTypes.DISTRICT_GARRISON) or 0)
      end)
    else
      local di = GameInfo.Districts[d:GetType()]
      info.dist[#info.dist + 1] = di and di.DistrictType or '?'
    end
  end
  return info
end

local function tileDesc(x, y)
  local p = Map.GetPlot(x, y)
  if not p then return 'void' end
  local t = 'flat'
  if p:IsMountain() then t = 'mountain' elseif p:IsHills() then t = 'hills' elseif p:IsWater() then t = 'water' end
  local imp, feat, terr = 'none', 'none', '?'
  pcall(function() local i = GameInfo.Improvements[p:GetImprovementType()]; if i then imp = i.ImprovementType end end)
  pcall(function() local f = GameInfo.Features[p:GetFeatureType()]; if f then feat = f.FeatureType end end)
  pcall(function() local ti = GameInfo.Terrains[p:GetTerrainType()]; if ti then terr = ti.TerrainType end end)
  local units = {}
  pcall(function()
    local pu = Map.GetUnitsAt(x, y)
    if pu then for u in pu:Units() do units[#units + 1] = 'p' .. u:GetOwner() .. '#' .. u:GetID() end end
  end)
  return string.format('%s %s feat=%s imp=%s owner=%s units={%s}',
    t, terr, feat, imp, tostring(p:GetOwner()), table.concat(units, ','))
end

print('=== ENEMY CITIES (player ' .. tostring(ENEMY_PLAYER) .. ') ===')
for _, c in Players[ENEMY_PLAYER]:GetCities():Members() do
  local i = cityStats(c)
  print(string.format('%s id=%s at=%d,%d pop=%d def=%d walls=%d/%d garrison=%d/%d districts=%s',
    Locale.Lookup(c:GetName()), tostring(c:GetID()), c:GetX(), c:GetY(), c:GetPopulation(),
    i.def, i.wallHP, i.wallMax, i.garHP, i.garMax, table.concat(i.dist, ',')))
end

print(string.format('=== RING d=1..2 AROUND (%d,%d) — 弓手射击位/近战格 ===', TARGET_X, TARGET_Y))
for dy = -2, 2 do
  for dx = -2, 2 do
    local x, y = TARGET_X + dx, TARGET_Y + dy
    local d = Map.GetPlotDistance(TARGET_X, TARGET_Y, x, y)
    if d == 1 or d == 2 then
      print(string.format('  d=%d (%d,%d) %s', d, x, y, tileDesc(x, y)))
    end
  end
end

print('=== OUR CITIES ===')
for _, c in Players[ME]:GetCities():Members() do
  local i = cityStats(c)
  print(string.format('%s id=%s at=%d,%d pop=%d hp=%d/%d def=%d loyalty=%s',
    Locale.Lookup(c:GetName()), tostring(c:GetID()), c:GetX(), c:GetY(), c:GetPopulation(),
    i.garHP, i.garMax, i.def, 'n/a'))
end
print('---END---')
