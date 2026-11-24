local me = Game.GetLocalPlayer()
local removed = {}
for _, nid in ipairs(NotificationManager.GetList(me) or {}) do
  local e = NotificationManager.Find(me, nid)
  if e and not e:IsDismissed() then
    local bt = -1
    pcall(function() bt = e:GetEndTurnBlocking() end)
    if bt and bt ~= 0 and bt ~= EndTurnBlockingTypes.ENDTURN_BLOCKING_NONE then
      pcall(function() NotificationManager.SendActivated(me, nid) end)
      pcall(function() NotificationManager.Dismiss(me, nid) end)
      removed[#removed + 1] = tostring(nid) .. ':' .. tostring(bt)
    end
  end
end
print('DISMISSED=' .. #removed .. ' [' .. table.concat(removed, ',') .. ']')
-- 列出总督指派状况
local pts = -1
pcall(function() pts = Players[me]:GetGovernorPointsAvailable() end)
print('GOV_POINTS=' .. tostring(pts))
print('---END---')
