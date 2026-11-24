local me = Game.GetLocalPlayer()
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() then
    for _, c in p:GetCities():Members() do
      print(string.format('CITY p=%d id=%d x=%d y=%d pop=%d', pi, c:GetID(), c:GetX(), c:GetY(), c:GetPopulation()))
    end
  end
end
print('---END---')
