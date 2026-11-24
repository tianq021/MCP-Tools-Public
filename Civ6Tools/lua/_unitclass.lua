-- check unit classes for siege tower compatibility + available melee units
local pid = Game.GetLocalPlayer()
local p = Players[pid]
print("=== KEY UNITS ===")
local check = {"UNIT_PIKE_AND_SHOT","UNIT_QHT_BLOOD_GUARD","UNIT_MUSKETMAN","UNIT_LINE_INFANTRY","UNIT_SWORDSMAN","UNIT_MAN_AT_ARMS","UNIT_WARRIOR","UNIT_HEAVY_CHARIOT","UNIT_CROSSBOWMAN","UNIT_CHINESE_CROUCHING_TIGER","UNIT_TREBUCHET","UNIT_CATAPULT","UNIT_BOMBARD","UNIT_FIELD_CANNON"}
for _, ut in ipairs(check) do
  local row = GameInfo.Units[ut]
  if row ~= nil then
    print(string.format("%s | promo=%s | class=%s | CS=%s RS=%s | cost=%s | tech=%s | obsolete=%s",
      ut, tostring(row.PromotionClass), tostring(row.FormationClass), tostring(row.Combat), tostring(row.RangedCombat), tostring(row.Cost), tostring(row.PrereqTech), tostring(row.ObsoleteTech)))
  else
    print(ut .. " | DOES_NOT_EXIST")
  end
end
print("=== AVAILABLE MELEE-CLASS UNITS (tech unlocked) ===")
local techs = p:GetTechs()
for row in GameInfo.Units() do
  local pc = tostring(row.PromotionClass or "")
  if pc == "PROMOTION_CLASS_MELEE" then
    local ok = true
    local tech = row.PrereqTech
    if tech ~= nil then
      local ok2, has = pcall(function() return techs:HasTech(tech) end)
      if ok2 then ok = has end
    end
    print(string.format("  %s | class=%s | CS=%s RS=%s | cost=%s | tech=%s | unlocked=%s",
      tostring(row.UnitType), pc, tostring(row.Combat), tostring(row.RangedCombat), tostring(row.Cost), tostring(tech), tostring(ok)))
  end
end
print("---END---")
