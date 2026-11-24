local me = Game.GetLocalPlayer()
for pi = 0, 63 do
  local p = Players[pi]
  if p and p:IsAlive() and not p:IsBarbarian() then
    for _, c in p:GetCities():Members() do
      local isCap = 'no'
      pcall(function() if c:IsCapital() then isCap = 'CAPITAL' end end)
      print(string.format('p=%d id=%d at=%d,%d pop=%d %s name=%s',
        pi, c:GetID(), c:GetX(), c:GetY(), c:GetPopulation(), isCap, tostring(c:GetName())))
    end
  end
end
print('---END---')
