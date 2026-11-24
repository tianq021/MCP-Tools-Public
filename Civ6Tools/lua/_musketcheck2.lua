local pid = Game.GetLocalPlayer()
local p = Players[pid]
local techs = p:GetTechs()
local gun = GameInfo.Technologies["TECH_GUNPOWDER"]
local ms = GameInfo.Technologies["TECH_MILITARY_SCIENCE"]
print("GUNPOWDER=" .. tostring(gun ~= nil and techs:HasTech(gun.Index) or false))
print("MILSCI=" .. tostring(ms ~= nil and techs:HasTech(ms.Index) or false))
local row = GameInfo.Units["UNIT_MUSKETMAN"]
print("MUSKETMAN_ROW=" .. tostring(row ~= nil))
if row ~= nil then
  print("MUSKETMAN prereq=" .. tostring(row.PrereqTech) .. " cost=" .. tostring(row.Cost))
end
print("---END---")
