-- ============================================================
-- force_endturn.lua : 绕过 MCP 直接请求结束回合（ingame 上下文）
-- 用法: python 脚本\civlua.py lua/force_endturn.lua ingame
--       然后隔 15-20 秒用 lua/turn.lua (gamecore) 确认回合号是否 +1
-- 场景: end_turn 反复 WAITING / 服务端标志残留 / 游戏内回合不动
-- ============================================================
local okCan, can = pcall(function() return UI.CanEndTurn() end)
print('CAN_END_TURN=' .. tostring(okCan) .. ':' .. tostring(can))
if okCan and can then
  local ok = pcall(function() UI.RequestAction(ActionTypes.ACTION_ENDTURN) end)
  print('FORCED=' .. tostring(ok))
else
  print('BLOCKED_BY_GAME — 先解决 UI 上的必答项（生产/科技/市政/单位/外交）')
end
print('---END---')
