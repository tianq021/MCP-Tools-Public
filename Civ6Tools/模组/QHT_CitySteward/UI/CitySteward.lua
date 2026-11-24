include("InstanceManager")

local BUILDINGS = {
    { "BUILDING_MONUMENT", 74 },
    { "BUILDING_GRANARY", 61 },
    { "BUILDING_WATER_MILL", 59 },
    { "BUILDING_SEWER", 50 },
    { "BUILDING_WORKSHOP", 72 },
    { "BUILDING_FACTORY", 63 },
    { "BUILDING_MARKET", 69 },
    { "BUILDING_BANK", 61 },
    { "BUILDING_STOCK_EXCHANGE", 54 },
    { "BUILDING_LIGHTHOUSE", 69 },
    { "BUILDING_SHIPYARD", 68 },
    { "BUILDING_SEAPORT", 54 },
    { "BUILDING_LIBRARY", 63 },
    { "BUILDING_UNIVERSITY", 59 },
    { "BUILDING_RESEARCH_LAB", 55 },
    { "BUILDING_AMPHITHEATER", 56 },
    { "BUILDING_ARENA", 52 },
    { "BUILDING_ZOO", 52 },
    { "BUILDING_STADIUM", 50 },
    { "BUILDING_FERRIS_WHEEL", 52 },
    { "BUILDING_AQUARIUM", 52 },
    { "BUILDING_AQUATICS_CENTER", 50 },
}

local PROJECTS = {
    { "PROJECT_ENHANCE_DISTRICT_COMMERCIAL_HUB", 35 },
    { "PROJECT_ENHANCE_DISTRICT_HARBOR", 35 },
    { "PROJECT_ENHANCE_DISTRICT_CAMPUS", 30 },
    { "PROJECT_ENHANCE_DISTRICT_THEATER", 25 },
    { "PROJECT_ENHANCE_DISTRICT_INDUSTRIAL_ZONE", 20 },
    { "PROJECT_ENHANCE_DISTRICT_HOLY_SITE", 15 },
}

local op = CityOperationTypes
local rows = InstanceManager:new("CityRow", "Row", Controls.CityStack)
local initialized, enabled, opened = false, false, false
local launchStack
local manual, results, attempted, pending = {}, {}, {}, {}
local turn, scanDelay, waitingForCapture
local summary = "预览或开启托管以查看安排。"

local function LocalPlayer()
    local id = Game.GetLocalPlayer()
    if id < 0 or GameConfiguration.IsAnyMultiplayer() or GameConfiguration.IsHotseat() then
        return nil
    end
    return Players[id]
end

local function NeedsCaptureDecision(playerID)
    local blocker = EndTurnBlockingTypes.ENDTURN_BLOCKING_CONSIDER_RAZE_CITY
    for _, id in ipairs(NotificationManager.GetList(playerID) or {}) do
        local notice = NotificationManager.Find(playerID, id)
        if notice and not notice:IsDismissed() and notice:GetEndTurnBlocking() == blocker then
            return true
        end
    end
    return false
end

local function BestProduction(city)
    local queue = city:GetBuildQueue()
    local buildings = city:GetBuildings()
    local growth = city:GetGrowth()
    local housingShort = growth:GetHousing() - city:GetPopulation() <= 1
    local amenitiesShort = growth:GetAmenities() < growth:GetAmenitiesNeeded()
    local occupied = city:IsOccupied()
    local best

    local function Consider(info, key, priority, reason, x, y)
        local params = { [key] = info.Hash, [op.PARAM_INSERT_MODE] = op.VALUE_APPEND }
        if x ~= nil then
            params[op.PARAM_X], params[op.PARAM_Y] = x, y
        end
        if not CityManager.CanStartOperation(city, op.BUILD, params, true) then return end
        local turns = queue:GetTurnsLeft(info.Hash)
        if turns < 0 then return end
        local score = priority - math.min(turns, 60) * 2
        if not best or score > best.score or (score == best.score and info.Hash < best.hash) then
            best = {
                hash = info.Hash, params = params, turns = turns, score = score,
                name = Locale.Lookup(info.Name), reason = reason,
            }
        end
    end

    for _, district in city:GetDistricts():Members() do
        if district:IsPillaged() then
            local info = GameInfo.Districts[district:GetType()]
            if info and info.DistrictType ~= "DISTRICT_CITY_CENTER" then
                Consider(info, op.PARAM_DISTRICT_TYPE, 145, "修复区域", district:GetX(), district:GetY())
            end
        end
    end

    for info in GameInfo.Buildings() do
        if not info.IsWonder and buildings:HasBuilding(info.Index) and buildings:IsPillaged(info.Index) then
            local monument = occupied and info.BuildingType == "BUILDING_MONUMENT"
            Consider(info, op.PARAM_BUILDING_TYPE, monument and 200 or 135, "修复建筑")
        end
    end

    for _, entry in ipairs(BUILDINGS) do
        local info = GameInfo.Buildings[entry[1]]
        if info and not info.IsWonder and not info.RequiresPlacement and not buildings:HasBuilding(info.Index)
            and queue:CanProduce(info.Hash, true) then
            local priority, reason = entry[2], "常规建筑"
            if housingShort and (info.Housing or 0) > 0 then
                priority, reason = priority + 42, "补住房"
            elseif amenitiesShort and (info.Entertainment or 0) > 0 then
                priority, reason = priority + 42, "补宜居度"
            elseif occupied and entry[1] == "BUILDING_MONUMENT" then
                priority, reason = priority + 50, "占领城市"
            end
            Consider(info, op.PARAM_BUILDING_TYPE, priority, reason)
        end
    end

    if best then return best end
    for _, entry in ipairs(PROJECTS) do
        local info = GameInfo.Projects[entry[1]]
        if info and queue:CanProduce(info.Hash, true) then
            Consider(info, op.PARAM_PROJECT_TYPE, entry[2], "城市项目")
        end
    end
    return best
end

local function Render()
    if not opened then return end
    local player = LocalPlayer()
    Controls.Status:SetText(enabled and "托管已开启 · 本局" or "托管已停止")
    Controls.ToggleLabel:SetText(enabled and "停止托管" or "开启并补产")
    Controls.Open:SetToolTipString(enabled and "城市生产托管 · 已开启" or "城市生产托管 · 已停止")
    Controls.Summary:SetText(summary)
    rows:ResetInstances()
    if player then
        for _, city in player:GetCities():Members() do
            local id = city:GetID()
            local row = rows:GetInstance()
            row.Name:SetText((manual[id] and "手动  " or "托管  ") .. Locale.Lookup(city:GetName()))
            row.Result:SetText(manual[id] and "由你安排生产" or (results[id] or "尚无安排"))
            row.Row:RegisterCallback(Mouse.eLClick, function()
                manual[id] = not manual[id]
                results[id] = nil
                if enabled and not manual[id] then scanDelay = 0.25 end
                Render()
            end)
        end
    end
    Controls.CityStack:CalculateSize()
    Controls.CityStack:ReprocessAnchoring()
    Controls.CityScroll:CalculateInternalSize()
end

local function Process(preview)
    local player = LocalPlayer()
    if not player or not player:IsTurnActive() then
        summary = "等待你的回合。"
        return
    end
    local captureDecision = NeedsCaptureDecision(player:GetID())
    if not preview then waitingForCapture = captureDecision end
    if captureDecision then
        summary = "请先决定是否保留新占城市。"
        return
    end

    local currentTurn = Game.GetCurrentGameTurn()
    if not preview and turn ~= currentTurn then
        turn, attempted, pending = currentTurn, {}, {}
    end
    local count, unavailable = 0, 0
    for _, city in player:GetCities():Members() do
        local id = city:GetID()
        local queue = city:GetBuildQueue()
        if not manual[id] then
            if preview or not attempted[id] then
                if queue:GetSize() > 0 then
                    results[id] = "已有生产，保留队列"
                else
                    local choice = BestProduction(city)
                    if not choice then
                        results[id] = "没有合适的生产，请手动安排"
                        unavailable = unavailable + 1
                    elseif preview then
                        results[id] = string.format("建议：%s · %s · %d 回合", choice.name, choice.reason, choice.turns)
                        count = count + 1
                    elseif player:IsTurnActive() and Game.GetCurrentGameTurn() == currentTurn and queue:GetSize() == 0 then
                        attempted[id] = true
                        pending[id] = { choice = choice, age = 0 }
                        CityManager.RequestOperation(city, op.BUILD, choice.params)
                        results[id] = "已提交，等待确认：" .. choice.name
                        count = count + 1
                    end
                end
            end
        end
    end
    summary = string.format("第 %d 回合 · %s %d 城 · %d 城需要手动安排",
        currentTurn, preview and "建议" or "本次提交", count, unavailable)
end

local function StopWithError(err)
    enabled, scanDelay, pending = false, nil, {}
    summary = "出现错误，托管已停止。详情见 Lua.log。"
    print("[QHT_CitySteward] " .. tostring(err))
    Render()
end

local function Update(delta)
    if scanDelay then
        scanDelay = scanDelay - delta
        if scanDelay <= 0 then
            scanDelay = nil
            if enabled then Process(false); Render() end
        end
    end
    if not next(pending) then return end
    local player = LocalPlayer()
    local changed = false
    for id, entry in pairs(pending) do
        entry.age = entry.age + delta
        local city = player and CityManager.GetCity(player:GetID(), id)
        local queue = city and city:GetBuildQueue()
        if queue and queue:GetSize() > 0 and queue:GetCurrentProductionTypeHash() == entry.choice.hash then
            results[id] = string.format("已安排：%s · %s · %d 回合",
                entry.choice.name, entry.choice.reason, entry.choice.turns)
            pending[id], changed = nil, true
        elseif entry.age >= 2 then
            results[id] = "未确认：" .. entry.choice.name .. "，请核对队列"
            pending[id], changed = nil, true
        end
    end
    if changed then Render() end
end

local function Close()
    opened = false
    Controls.Root:SetHide(true)
end

local function Schedule()
    if enabled and not scanDelay then scanDelay = 0.25 end
end

local function OnCityEvent(playerID)
    if playerID == Game.GetLocalPlayer() then Schedule() end
end

local function OnNotificationDismissed(playerID)
    if waitingForCapture and playerID == Game.GetLocalPlayer() then Schedule() end
end

local function ResizeLaunchBar()
    launchStack:CalculateSize()
    launchStack:ReprocessAnchoring()
    local width = launchStack:GetSizeX()
    for _, item in ipairs({ {"LaunchBacking", 116}, {"LaunchBackingTile", -20}, {"LaunchBarDropShadow", 0} }) do
        local control = ContextPtr:LookUpControl("/InGame/LaunchBar/" .. item[1])
        if control then control:SetSizeX(width + item[2]) end
    end
    LuaEvents.LaunchBar_Resize(width)
end

local function Initialize()
    if initialized or not LocalPlayer() then return end
    local stack = ContextPtr:LookUpControl("/InGame/LaunchBar/ButtonStack")
    local screens = ContextPtr:LookUpControl("/InGame/Screens")
    if not stack or not screens then return end
    initialized, launchStack = true, stack
    ContextPtr:ChangeParent(screens)
    Controls.Open:ChangeParent(stack)
    Controls.Open:SetHide(false)
    Controls.Open:SetToolTipString("城市生产托管")
    ResizeLaunchBar()

    Controls.Open:RegisterCallback(Mouse.eLClick, function()
        opened = true
        Controls.Root:SetHide(false)
        Render()
    end)
    Controls.Close:RegisterCallback(Mouse.eLClick, Close)
    Controls.Backdrop:RegisterCallback(Mouse.eLClick, Close)
    Controls.Preview:RegisterCallback(Mouse.eLClick, function()
        local ok, err = pcall(Process, true)
        if not ok then StopWithError(err) else Render() end
    end)
    Controls.Toggle:RegisterCallback(Mouse.eLClick, function()
        enabled = not enabled
        if enabled then
            Schedule()
        else
            scanDelay = nil
            summary = "已停止续产，已安排的生产保留在队列中。"
        end
        Render()
    end)
    ContextPtr:SetInputHandler(function(input)
        if opened and input:GetMessageType() == KeyEvents.KeyUp and input:GetKey() == Keys.VK_ESCAPE then
            Close()
            return true
        end
        return false
    end, true)
    ContextPtr:SetUpdate(function(delta)
        if scanDelay or next(pending) then
            local ok, err = pcall(Update, delta)
            if not ok then StopWithError(err) end
        end
    end)
    Events.LocalPlayerTurnBegin.Add(Schedule)
    Events.LocalPlayerTurnEnd.Add(Close)
    Events.CityProductionCompleted.Add(OnCityEvent)
    Events.CityAddedToMap.Add(OnCityEvent)
    Events.NotificationDismissed.Add(OnNotificationDismissed)
    print("[QHT_CitySteward] Ready v1")
end

ContextPtr:SetShutdown(function()
    Events.LoadGameViewStateDone.Remove(Initialize)
    Events.LocalPlayerTurnBegin.Remove(Initialize)
    if initialized then
        Events.LocalPlayerTurnBegin.Remove(Schedule)
        Events.LocalPlayerTurnEnd.Remove(Close)
        Events.CityProductionCompleted.Remove(OnCityEvent)
        Events.CityAddedToMap.Remove(OnCityEvent)
        Events.NotificationDismissed.Remove(OnNotificationDismissed)
        ContextPtr:ClearUpdate()
        Controls.Open:SetHide(true)
        ResizeLaunchBar()
    end
end)

Events.LoadGameViewStateDone.Add(Initialize)
Events.LocalPlayerTurnBegin.Add(Initialize)
