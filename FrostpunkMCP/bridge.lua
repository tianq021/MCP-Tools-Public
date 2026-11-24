-- Frostpunk 1 bridge, loaded from GameMain.lua by the game's Lua 5.1 VM.
if not LuaGameDelegate then return end

local lastId, ticks = nil, 0
local loadingRequest = false
local warned = false
local session = tostring({}):gsub("[^%w]", "")
local hook = "startup"

local function hex(value)
    return (value:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end

local function state()
    return table.concat({
        "session=" .. session,
        "frame=" .. tostring(gGame:GetBigFrameIndex()),
        "active=" .. tostring(gGame:IsActive()),
        "loading=" .. tostring(gGame:IsLoadingScreenActive()),
        "hook=" .. hook,
        "input_mode=" .. tostring(gConfigHelper:GetInputMode()),
    }, "\n")
end

local function probe()
    -- Reversible native setter/getter round trip. Restore even when readback fails.
    local original = gConfigHelper:GetInputMode()
    local other = (original + 1) % INPUTMODE_MAX
    assert(type(other) == "number" and other ~= original, "alternate input mode unavailable")
    local ok, observed = pcall(function()
        gConfigHelper:SetInputMode(other)
        return gConfigHelper:GetInputMode()
    end)
    gConfigHelper:SetInputMode(original)
    local restored = gConfigHelper:GetInputMode()
    assert(restored == original, "original input mode could not be restored")
    assert(ok and observed == other, "native setter did not read back the requested value")
    return state() .. "\nprevious=" .. original .. "\napplied=" .. observed .. "\nrestored=" .. restored
end

local actions = {state = state, probe = probe}
local previous = FP1Bridge
FP1Bridge = {wrappers = {}, originals = {}}

function FP1Bridge.receive(id, action, expectedSession)
    if not loadingRequest or type(id) ~= "string" or #id ~= 32 or id:find("[^0-9a-f]") then return end
    if id == lastId then return end
    lastId = id
    local ok, reply = pcall(function()
        assert(actions[action], "unknown action")
        assert(action == "state" or expectedSession == session, "bridge session changed")
        return actions[action]()
    end)
    print("FP1BRIDGE/1:" .. id .. ":" .. (ok and "ok" or "error") .. ":" .. hex(tostring(reply)) .. ":")
end

local function poll(name)
    hook = name
    ticks = ticks + 1
    if ticks < 4 then return end
    ticks = 0
    loadingRequest = true
    local ok, err = pcall(function() gLua:ExecuteFile("fp1bridge_request", "project") end)
    loadingRequest = false
    if not ok and not warned then
        warned = true
        print("FP1BRIDGE/1:request-error " .. tostring(err))
    end
end

local function wrap(name)
    local original = LuaGameDelegate[name]
    if previous and original == previous.wrappers[name] then original = previous.originals[name] end
    local wrapper = function(self, ...)
        poll(name)
        if original then return original(self, ...) end
    end
    FP1Bridge.wrappers[name] = wrapper
    FP1Bridge.originals[name] = original
    LuaGameDelegate[name] = wrapper
end

wrap("OnTick")
wrap("OnPauseTick")
wrap("OnAfterTick")
print("FP1BRIDGE/1:ready")
