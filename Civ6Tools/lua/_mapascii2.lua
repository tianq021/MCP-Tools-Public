-- compact ASCII map of explored world (InGame context)
local pid = Game.GetLocalPlayer()
local vis = PlayersVisibility[pid]
local W, H = Map.GetGridSize()
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
      local idx = p:GetIndex()
      local revealed = vis:IsRevealed(idx)
      local visible = vis:IsVisible(idx)
      local ch = "?"
      if revealed then
        if p:IsWater() then
          ch = "-"
        else
          ch = "."
        end
        local c = Cities.GetCityInPlot(x, y)
        if c ~= nil then
          ch = (c:GetOwner() == pid) and "C" or "X"
        else
          local imp = p:GetImprovementType()
          if imp ~= nil and imp >= 0 then ch = "," end
        end
        if visible then
          if ch == "." then ch = "v" elseif ch == "~" then ch = "w" elseif ch == "-" then ch = "s" end
        end
      end
      row = row .. ch
    end
  end
  print(row)
end
print("--- CITIES ---")
for _, c in Players[pid]:GetCities():Members() do
  print("CITY " .. c:GetID() .. " " .. c:GetX() .. "," .. c:GetY() .. " pop=" .. c:GetPopulation())
end
print("---END---")
