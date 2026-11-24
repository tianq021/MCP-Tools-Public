local me = Game.GetLocalPlayer()
local removed = {}
for _, nid in ipairs(NotificationManager.GetList(me) or {}) do
  local e = NotificationManager.Find(me, nid)
  if e and not e:IsDismissed() then
    local bt = e:GetEndTurnBlocking()
    if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE
      or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_CIVIC
      or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_RESEARCH then
      pcall(function() NotificationManager.SendActivated(me, nid) end)
      pcall(function() NotificationManager.Dismiss(me, nid) end)
      removed[#removed + 1] = tostring(nid) .. ':' .. tostring(bt)
    end
  end
end
print('CLEARED=' .. #removed .. ' [' .. table.concat(removed, ',') .. ']')

local p = Players[me]
local pts = -1
pcall(function() pts = p:GetGovernorPointsAvailable() end)
print('GOV_POINTS=' .. tostring(pts))
print('---END---')
