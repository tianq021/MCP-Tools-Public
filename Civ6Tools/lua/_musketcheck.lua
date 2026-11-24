local pid = Game.GetLocalPlayer()
local p = Players[pid]
local techs = p:GetTechs()
local gun = GameInfo.Technologies["TECH_GUNPOWDER"]
print("GUNPOWDER_EXISTS=" .. tostring(gun ~= nil))
if gun ~= nil then
  print("GUNPOWDER_RESEARCHED=" .. tostring(techs:HasTech(gun.Index)))
end
local ms = GameInfo.Technologies["TECH_MILITARY_SCIENCE"]
print("MILSCI_RESEARCHED=" .. tostring(techs:HasTech(ms.Index)))
-- check resources
local res = p:GetResources()
for _, r in ipairs({"RESOURCE_NITER","RESOURCE_IRON","RESOURCE_COAL"}) do
  local ok, amt = pcall(function() return p:GetResourceAmount(r) end)
  print(r .. "=" .. tostring(ok) .. ":" .. tostring(amt))
end
-- check musketman prereq resources
local row = GameInfo.Units["UNIT_MUSKETMAN"]
if row ~= nil then
  print("MUSKETMAN cost=" .. tostring(row.Cost) .. " prereqtech=" .. tostring(row.PrereqTech))
  for rc in GameInfo.UnitResourceCosts() do
    if tostring(rc.UnitType) == "UNIT_MUSKETMAN" then
      print("  needs " .. tostring(rc.ResourceType) .. " x" .. tostring(rc.Amount))
    end
  end
end
print("---END---")
