-- read Mali city defense values + siege unit abilities
local pid1 = 1
for _, c in Players[pid1]:GetCities():Members() do
  print("=== MALI CITY " .. tostring(c:GetName()) .. " at " .. c:GetX() .. "," .. c:GetY() .. " pop=" .. c:GetPopulation() .. " ===")
  for _, d in c:GetDistricts():Members() do
    local dInfo = GameInfo.Districts[d:GetType()]
    if dInfo and dInfo.DistrictType == "DISTRICT_CITY_CENTER" then
      local ok1, def = pcall(function() return d:GetDefenseStrength() end)
      print("  DEF_STRENGTH=" .. tostring(ok1) .. ":" .. tostring(def))
      local okO, maxO = pcall(function() return d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) end)
      local okO2, curO = pcall(function() return d:GetDamage(DefenseTypes.DISTRICT_OUTER) end)
      print("  WALLS=" .. tostring(maxO) .. " cur_damage=" .. tostring(curO))
      local okG, maxG = pcall(function() return d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) end)
      local okG2, curG = pcall(function() return d:GetDamage(DefenseTypes.DISTRICT_GARRISON) end)
      print("  GARRISON=" .. tostring(maxG) .. " cur_damage=" .. tostring(curG))
      local okGs, gs = pcall(function() return d:GetGarrisonStrength() end)
      print("  GARRISON_STRENGTH=" .. tostring(okGs) .. ":" .. tostring(gs))
    end
  end
  local okU, u = pcall(function() return c:GetGarrisonedUnit() end)
  if okU and u ~= nil then
    print("  GARRISON_UNIT=" .. tostring(u:GetType()) .. " CS=" .. tostring(u:GetCombatStrength()))
  end
end
print("=== SIEGE UNIT ABILITIES ===")
for _, ut in ipairs({"UNIT_TREBUCHET", "UNIT_SIEGE_TOWER", "UNIT_CATAPULT", "UNIT_PIKE_AND_SHOT", "UNIT_CROSSBOWMAN"}) do
  local row = GameInfo.Units[ut]
  if row ~= nil then
    print(ut .. "|CS=" .. tostring(row.Combat) .. "|RS=" .. tostring(row.RangedCombat) .. "|class=" .. tostring(row.FormationClass) .. "|domain=" .. tostring(row.Domain) .. "|cost=" .. tostring(row.Cost))
  end
end
print("---END---")
