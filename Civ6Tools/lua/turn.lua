-- ============================================================
-- turn.lua : 读取真实游戏回合号（gamecore 上下文）
-- 用法: python 脚本\civlua.py lua/turn.lua gamecore
-- 用途: end_turn 报 "Turn N -> N+1 但没推进" / WAITING 时，以此为准
-- ============================================================
print('TURN=' .. tostring(Game.GetCurrentGameTurn()))
local ok, era = pcall(function() return Players[Game.GetLocalPlayer()]:GetEra() end)
print('ERA=' .. tostring(ok) .. ':' .. tostring(era))
print('---END---')
