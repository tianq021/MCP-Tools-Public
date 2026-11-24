-- 找出待决城市（可能不在 get_cities 列表里）
local me = Game.GetLocalPlayer()
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() then
    for _, c in p:GetCities():Members() do
      local x, y = c:GetX(), c:GetY()
      print(string.format('CITY player=%d id=%d at=%d,%d pop=%d name=%s',
        pi, c:GetID(), x, y, c:GetPopulation(), Locale.Lookup(c:GetName())))
    end
  end
end
print('---END---')
