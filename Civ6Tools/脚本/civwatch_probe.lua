-- ============================================================
-- civwatch_probe.lua : 事件哨兵探针（InGame 上下文，只读）
--
-- 由 脚本\watch_core.py 通过 MCP run_lua 执行，单次往返（~50-150ms）输出
-- 压缩状态。所有行格式 KEY|field|...，以 ---END--- 结束；每块 pcall 包裹，
-- 任何一块读不到都不影响其余输出（宁可少报，不可整段失败）。
--
-- 输出行：
--   STATE|in_game|no_player
--   ME|<player_id>
--   TURN|<turn>
--   DIPLO|<sid>|<player>|<civ>|<leader>|war<0/1>|<deal_count>|<dialogue>
--   WAR|<player>|0/1                     （对所有已见面的主要文明）
--   BLOCK|<EndTurnBlocking 类型>|<通知类型>|<加入回合>|<消息>
--   NOTIF|<notification_id>|<通知类型>|<加入回合>
--   NOTIFMETA|<通知总数>|<最大 id>
--   POPUP|<UI 控件名>
--   PROBE_ERROR|<错误>
--   ---END---
-- ============================================================

local SENT = "---END---"

local function esc(v)
  if v == nil then return "" end
  local s = tostring(v)
  s = s:gsub("|", "/")
  s = s:gsub("[\r\n]+", " ")
  return s
end

local okAll, errAll = pcall(function()
  local me = Game.GetLocalPlayer()
  if me == nil or me < 0 or not Players[me] then
    print("STATE|no_player")
    print(SENT)
    return
  end

  local turn = -1
  pcall(function() turn = Game.GetCurrentGameTurn() end)

  print("STATE|in_game")
  print("ME|" .. tostring(me))
  print("TURN|" .. tostring(turn))

  -- ---------- 1) 外交会话：对方正在跟我们说话 ----------
  for i = 0, 62 do
    if i ~= me and Players[i] and Players[i]:IsAlive() then
      local okS, sid = pcall(function()
        return DiplomacyManager.FindOpenSessionID(me, i)
      end)
      if okS and sid and sid >= 0 then
        local civ, leader = "?", "?"
        pcall(function()
          local cfg = PlayerConfigurations[i]
          if cfg then
            civ = Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?"
            leader = Locale.Lookup(cfg:GetLeaderName()) or "?"
          end
        end)

        local atWar = "0"
        pcall(function()
          if Players[me]:GetDiplomacy():IsAtWarWith(i) then atWar = "1" end
        end)

        -- 对话正文：DiplomacyActionView 的文本在会话打开后 1-2s 才填上，
        -- 所以可能为空——上层有 grace 重试。
        local dialogue = ""
        pcall(function()
          local c = ContextPtr:LookUpControl("/InGame/DiplomacyActionView/LeaderResponseText")
          if c then
            local ok, t = pcall(c.GetText, c)
            if ok and t and t ~= "" then dialogue = t end
          end
          local d = ContextPtr:LookUpControl("/InGame/DiplomacyDealView/LeaderResponseText")
          if d and not d:IsHidden() then
            local ok2, t2 = pcall(d.GetText, d)
            if ok2 and t2 and t2 ~= "" then dialogue = t2 end
          end
        end)

        -- 交易工作簿条目数：>0 表示这是「AI 在向你报价」
        local dealCount = 0
        pcall(function()
          local deal = DealManager.GetWorkingDeal(DealDirection.INCOMING, me, i)
          if deal then dealCount = deal:GetItemCount() or 0 end
        end)

        print("DIPLO|" .. tostring(sid) .. "|" .. tostring(i) .. "|" ..
              esc(civ) .. "|" .. esc(leader) .. "|" .. atWar .. "|" ..
              tostring(dealCount) .. "|" .. esc(dialogue))
      end
    end
  end

  -- ---------- 2) 对所有已见面主要文明的战争状态（会话可能被自动关掉，战争状态不会） ----------
  pcall(function()
    local pDiplo = Players[me]:GetDiplomacy()
    for i = 0, 62 do
      if i ~= me and Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() then
        local okMet, met = pcall(function() return pDiplo:HasMet(i) end)
        if okMet and met then
          local atWar = 0
          pcall(function()
            if pDiplo:IsAtWarWith(i) then atWar = 1 end
          end)
          print("WAR|" .. tostring(i) .. "|" .. tostring(atWar))
        end
      end
    end
  end)

  -- ---------- 3) EndTurn 阻塞通知 + 活跃通知 ----------
  local notifCount, maxId = 0, 0
  pcall(function()
    local list = NotificationManager.GetList(me)
    if not list then return end
    notifCount = #list
    for _, nid in ipairs(list) do
      local entry = NotificationManager.Find(me, nid)
      if entry and not entry:IsDismissed() then
        local num = tonumber(nid) or 0
        if num > maxId then maxId = num end

        local notifType = "UNKNOWN"
        pcall(function() notifType = entry:GetTypeName() or "UNKNOWN" end)
        local addedTurn = -1
        pcall(function() addedTurn = entry:GetAddedTurn() or -1 end)

        local bt = 0
        pcall(function() bt = entry:GetEndTurnBlocking() or 0 end)
        if bt and bt ~= 0 then
          local blockingName = "UNKNOWN"
          for k, v in pairs(EndTurnBlockingTypes) do
            if v == bt then blockingName = k; break end
          end
          local msg = ""
          pcall(function() msg = entry:GetMessage() or "" end)
          print("BLOCK|" .. blockingName .. "|" .. esc(notifType) .. "|" ..
                tostring(addedTurn) .. "|" .. esc(msg))
        end

        print("NOTIF|" .. tostring(num) .. "|" .. esc(notifType) .. "|" .. tostring(addedTurn))
      end
    end
  end)
  print("NOTIFMETA|" .. tostring(notifCount) .. "|" .. tostring(maxId))

  -- ---------- 4) UI 覆盖层 / 弹窗 ----------
  local popups = {
    "DiplomacyActionView", "DiplomacyDealView",
    "InGamePopup", "GenericPopup", "PopupDialog",
    "WorldCongressPopup", "WorldCongressIntro",
    "TechCivicCompletedPopup", "BoostUnlockedPopup",
    "NaturalDisasterPopup", "WonderBuiltPopup", "EraCompletePopup",
    "GreatWorkShowcase", "HistoricMoments", "LeaderScene",
  }
  for _, name in ipairs(popups) do
    local okP, visible = pcall(function()
      local c = ContextPtr:LookUpControl("/InGame/" .. name)
      return c ~= nil and (not c:IsHidden())
    end)
    if okP and visible then
      print("POPUP|" .. name)
    end
  end
end)

if not okAll then
  print("PROBE_ERROR|" .. esc(errAll))
end
print(SENT)
