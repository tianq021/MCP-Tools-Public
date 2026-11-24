-- ============================================================
-- dismiss_blockers.lua : 清除残留的"选择科技/选择市政"阻塞通知（ingame）
-- 用法: python 脚本\civlua.py lua/dismiss_blockers.lua ingame
-- 场景: 开了「随机科技和市政」后，即使已选好，游戏仍每回合生成
--       ENDTURN_BLOCKING_CIVIC 通知，导致 end_turn 永久 BLOCKED。
-- ============================================================
local me = Game.GetLocalPlayer()
local removed = 0
local list = NotificationManager.GetList(me)
for _, nid in ipairs(list or {}) do
  local e = NotificationManager.Find(me, nid)
  if e and not e:IsDismissed() then
    local bt = e:GetEndTurnBlocking()
    if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_CIVIC
      or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_RESEARCH then
      pcall(function() NotificationManager.SendActivated(me, nid) end)
      pcall(function() NotificationManager.Dismiss(me, nid) end)
      removed = removed + 1
      print('DISMISSED|' .. tostring(nid) .. '|blocking=' .. tostring(bt))
    end
  end
end
print('REMOVED=' .. removed)

-- 顺便报告还有哪些阻塞项（不处理，只告知）
local p = Players[me]
local c = p:GetCulture()
local t = p:GetTechs()
print('CIVIC_PROGRESSING=' .. tostring(c:GetProgressingCivic()))
print('TECH_RESEARCHING=' .. tostring(t:GetResearchingTech()))
print('---END---')
