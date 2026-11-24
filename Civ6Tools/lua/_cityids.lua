-- list city ids
local pid = Game.GetLocalPlayer()
for _, c in Players[pid]:GetCities():Members() do
  print("CITY|id=" .. tostring(c:GetID()) .. "|name=" .. tostring(c:GetName()) .. "|at=" .. c:GetX() .. "," .. c:GetY())
end
print("---END---")
