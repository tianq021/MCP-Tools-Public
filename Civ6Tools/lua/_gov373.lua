-- list governments available to us, unlocked policies, and city-distance global params
local pid = Game.GetLocalPlayer()
local p = Players[pid]
print("PLAYER=" .. tostring(pid))

print("=== GLOBAL PARAMS (DIST/CITY) ===")
for row in GameInfo.GlobalParameters() do
  local n = tostring(row.Name)
  if n:find("DIST") or n:find("CITY") then
    print("  " .. n .. " = " .. tostring(row.Value))
  end
end

print("=== GOVERNMENTS ===")
for row in GameInfo.Governments() do
  local gt = tostring(row.GovernmentType)
  local civic = tostring(row.RequiredCivic or "")
  local unlocked = "yes"
  if civic ~= "" and civic ~= "nil" then
    local ok, r = pcall(function() return p:HasCivic(civic) end)
    if ok then unlocked = tostring(r) else unlocked = "?" end
  end
  print(string.format("  %s | civic=%s | unlocked=%s", gt, civic, unlocked))
end

print("=== UNLOCKED POLICIES ===")
local count = 0
for row in GameInfo.Policies() do
  local civic = tostring(row.RequiredCivic or "")
  local unlocked = false
  if civic == "" or civic == "nil" then unlocked = true
  else
    local ok, r = pcall(function() return p:HasCivic(civic) end)
    if ok then unlocked = r end
  end
  if unlocked then
    count = count + 1
    print(string.format("  %s | slot=%s | civic=%s", tostring(row.PolicyType), tostring(row.GovernmentSlotType), civic))
  end
end
print("POLICY_COUNT=" .. count)
print("---END---")
