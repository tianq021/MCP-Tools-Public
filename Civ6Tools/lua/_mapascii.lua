-- compact ASCII map of explored world (44x26)
local pid = Game.GetLocalPlayer()
local W, H = Map.GetGridSize()
local team = Players[pid]:GetTeam()
print("MAP|W=" .. W .. "|H=" .. H)
local hdr = "    "
for x = 0, W - 1 do hdr = hdr .. tostring(x % 10) end
print(hdr)
for y = 0, H - 1 do
  local row = string.format("%3d ", y)
  for x = 0, W - 1 do
    local p = Map.GetPlot(x, y)
    if p == nil then row = row .. " "
    else
      local rev = false
      pcall(function() rev = p:IsRevealed(team, false) end)
      if not rev then row = row .. "?"
      else
        local ch = "."
        local w = false
        pcall(function() w = p:IsWater() end)
        if w then
          local ocean = false
          pcall(function() ocean = p:IsOcean() end)
          ch = ocean and "~" or "-"
        end
        local c = nil
        pcall(function() c = p:GetCity() end)
        if c ~= nil then
          if c:GetOwner() == pid then ch = "C" else ch = "X" end
        else
          local imp = nil
          pcall(function() imp = p:GetImprovementType() end)
          if imp ~= nil and imp >= 0 then ch = "," end
        end
        row = row .. ch
      end
    end
  end
  print(row)
end
print("--- CITIES ---")
for _, c in Players[pid]:GetCities():Members() do
  print("CITY " .. c:GetID() .. " " .. c:GetX() .. "," .. c:GetY() .. " pop=" .. c:GetPopulation())
end
print("---END---")

