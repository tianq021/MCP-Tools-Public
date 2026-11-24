local me = Game.GetLocalPlayer()
local u
for _, unit in Players[me]:GetUnits():Members() do
  local ui = GameInfo.Units[unit:GetType()]
  if ui and ui.UnitType == 'UNIT_WARRIOR' then u = unit break end
end
if not u then print('NO_WARRIOR') else
  print('UNIT id=' .. tostring(u:GetID()))
  print('GetName=' .. tostring(type(u.GetName)))
  print('SetName=' .. tostring(type(u.SetName)))
  print('SetUnitName=' .. tostring(type(u.SetUnitName)))
  print('GetUnitName=' .. tostring(type(u.GetUnitName)))
  pcall(function() print('NAME=' .. tostring(u:GetName())) end)
end
local p = Players[me]
print('Player.SetUnitName=' .. tostring(type(p.SetUnitName)))
print('Player.GetUnitName=' .. tostring(type(p.GetUnitName)))
print('UnitManager.SetName=' .. tostring(type(UnitManager.SetName)))
print('---END---')
