"""Civ identity domain — the traits and unique kit of the civ you are playing.

This domain exists because Civ 6's state API tells an agent *who* it is
(``GetCivilizationTypeName``) but never *what that means*. The ability text
lives in the GameInfo database (``Traits`` / ``CivilizationTraits`` /
``LeaderTraits``), and nothing in the rest of this package read those tables —
which is the documented root cause of the repeated "civ identity blindness"
failures in the devlogs (Game 6 Gandhi: 9,599 unspent faith and zero Holy
Sites; Game 10 Chandragupta: 431 turns of generic India).

Everything here is defensive: each optional table/column access is wrapped in
``pcall`` and the query emits ``DIAG|`` lines so that a failure shows up as a
zero counter rather than a silent empty list.
"""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import CivAbilities, CivTrait, UniqueItem


def _safe_int(s: str) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def build_civ_abilities_query() -> str:
    """InGame context: current civ/leader identity, trait texts, unique kit.

    Output format (pipe-delimited, one record per line)::

        CIV|civType|civName|leaderType|leaderName
        TRAIT|scope|traitType|name|description        (scope = CIV | LEADER)
        UNIT|type|name|combat|ranged|prereqTech|prereqCivic|cost
        DISTRICT|type|name|0|0|prereqTech|prereqCivic|cost
        BUILDING|type|name|0|0|prereqTech|prereqCivic|cost
        IMPROVEMENT|type|name|0|0|prereqTech|prereqCivic|cost
        DIAG|<counter>|<value>
        ---END---
    """
    return """
local me = Game.GetLocalPlayer()
local cfg = PlayerConfigurations[me]
if cfg == nil then
    print("ERR:NO_CONFIG")
    print("{SENTINEL}")
    return
end
local civType = cfg:GetCivilizationTypeName() or "UNKNOWN"
local leaderType = cfg:GetLeaderTypeName() or "UNKNOWN"

-- Collapse LOC text: resolve key, kill pipes (field separator), strip [ICON_x]
-- markup, flatten newlines. Verified against live game output: ability text
-- ships with tags like "[ICON_TechBoosted] 尤里卡和 [ICON_CivicBoosted] 鼓舞".
local function clean(s)
    if s == nil then return "" end
    local v = s
    local ok, looked = pcall(function() return Locale.Lookup(v) end)
    if ok and looked ~= nil and looked ~= "" then v = looked end
    v = tostring(v)
    v = v:gsub("|", "/")
    v = v:gsub("[\\r\\n\\t]+", " ")
    v = v:gsub("%[ICON_[%w_]+%]", "")
    v = v:gsub("%[Icon_[%w_]+%]", "")
    v = v:gsub("  +", " ")
    v = v:gsub("^%s+", "")
    return v
end

local civName, leaderName = "", ""
pcall(function() civName = clean(cfg:GetCivilizationShortDescription()) end)
pcall(function() leaderName = clean(cfg:GetLeaderName()) end)
print("CIV|" .. tostring(civType) .. "|" .. civName .. "|" .. tostring(leaderType) .. "|" .. leaderName)

-- ---- Traits -------------------------------------------------------------
local traitSet = {}
local nTraitKeys = 0
local nSkipped = 0

local function emitTrait(scope, traitType)
    if traitType == nil or traitType == "" then return end
    if traitSet[traitType] then return end
    traitSet[traitType] = true
    local nm, desc = "", ""
    local row = nil
    pcall(function() row = GameInfo.Traits[traitType] end)
    if row ~= nil then
        pcall(function() nm = clean(row.Name) end)
        pcall(function() desc = clean(row.Description) end)
    end
    -- Internal pseudo-traits (e.g. TRAIT_GOLD_PURCHASE,
    -- TRAIT_LEADER_SCIENCE_MAJOR_CIV) carry neither name nor text. They are
    -- AI-behaviour plumbing, not player-facing abilities — drop them so the
    -- agent does not reason about a "trait" that does nothing.
    if nm == "" and desc == "" then
        nSkipped = nSkipped + 1
        return
    end
    nTraitKeys = nTraitKeys + 1
    print("TRAIT|" .. scope .. "|" .. tostring(traitType) .. "|" .. nm .. "|" .. desc)
end

local nCivTraits, nLeaderTraits = 0, 0
pcall(function()
    for r in GameInfo.CivilizationTraits() do
        if r.CivilizationType == civType then
            nCivTraits = nCivTraits + 1
            emitTrait("CIV", r.TraitType)
        end
    end
end)
pcall(function()
    for r in GameInfo.LeaderTraits() do
        if r.LeaderType == leaderType then
            nLeaderTraits = nLeaderTraits + 1
            emitTrait("LEADER", r.TraitType)
        end
    end
end)

-- ---- Unique kit ---------------------------------------------------------
local function isOurs(t)
    if t == nil or t == "" then return false end
    return traitSet[t] == true
end

local function techName(t)
    if t == nil or t == "" then return "" end
    local n = ""
    local row = nil
    pcall(function() row = GameInfo.Technologies[t] end)
    if row ~= nil then pcall(function() n = clean(row.Name) end) end
    if n == "" then n = tostring(t) end
    return n
end

local function civicName(t)
    if t == nil or t == "" then return "" end
    local n = ""
    local row = nil
    pcall(function() row = GameInfo.Civics[t] end)
    if row ~= nil then pcall(function() n = clean(row.Name) end) end
    if n == "" then n = tostring(t) end
    return n
end

local nWithTrait = 0
local nOurUnits, nOurDistricts, nOurBuildings, nOurImps = 0, 0, 0, 0

pcall(function()
    for row in GameInfo.Units() do
        if row.TraitType ~= nil and row.TraitType ~= "" then
            nWithTrait = nWithTrait + 1
            if isOurs(row.TraitType) then
                nOurUnits = nOurUnits + 1
                print("UNIT|" .. tostring(row.UnitType) .. "|" .. clean(row.Name)
                    .. "|" .. tostring(row.Combat or 0) .. "|" .. tostring(row.RangedCombat or 0)
                    .. "|" .. techName(row.PrereqTech) .. "|" .. civicName(row.PrereqCivic)
                    .. "|" .. tostring(row.Cost or 0))
            end
        end
    end
end)

pcall(function()
    for row in GameInfo.Districts() do
        if isOurs(row.TraitType) then
            nOurDistricts = nOurDistricts + 1
            print("DISTRICT|" .. tostring(row.DistrictType) .. "|" .. clean(row.Name)
                .. "|0|0|" .. techName(row.PrereqTech) .. "|" .. civicName(row.PrereqCivic)
                .. "|" .. tostring(row.Cost or 0))
        end
    end
end)

pcall(function()
    for row in GameInfo.Buildings() do
        if isOurs(row.TraitType) then
            nOurBuildings = nOurBuildings + 1
            print("BUILDING|" .. tostring(row.BuildingType) .. "|" .. clean(row.Name)
                .. "|0|0|" .. techName(row.PrereqTech) .. "|" .. civicName(row.PrereqCivic)
                .. "|" .. tostring(row.Cost or 0))
        end
    end
end)

pcall(function()
    for row in GameInfo.Improvements() do
        if isOurs(row.TraitType) then
            nOurImps = nOurImps + 1
            print("IMPROVEMENT|" .. tostring(row.ImprovementType) .. "|" .. clean(row.Name)
                .. "|0|0|" .. techName(row.PrereqTech) .. "|" .. civicName(row.PrereqCivic)
                .. "|" .. tostring(row.Cost or 0))
        end
    end
end)

print("DIAG|CIVTRAITS|" .. nCivTraits)
print("DIAG|LEADERTRAITS|" .. nLeaderTraits)
print("DIAG|TRAITS|" .. nTraitKeys)
print("DIAG|TRAITS_INTERNAL_SKIPPED|" .. nSkipped)
print("DIAG|UNITS_WITH_TRAITTYPE|" .. nWithTrait)
print("DIAG|OUR_UNITS|" .. nOurUnits)
print("DIAG|OUR_DISTRICTS|" .. nOurDistricts)
print("DIAG|OUR_BUILDINGS|" .. nOurBuildings)
print("DIAG|OUR_IMPROVEMENTS|" .. nOurImps)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_civ_abilities_response(lines: list[str]) -> CivAbilities:
    """Parse the pipe-delimited output of :func:`build_civ_abilities_query`."""
    ab = CivAbilities(civ_type="", civ_name="", leader_type="", leader_name="")
    for line in lines:
        if not line or "|" not in line:
            continue
        p = line.split("|")
        tag = p[0]
        if tag == "CIV" and len(p) >= 5:
            ab.civ_type = p[1]
            ab.civ_name = p[2]
            ab.leader_type = p[3]
            ab.leader_name = p[4]
        elif tag == "TRAIT" and len(p) >= 5:
            ab.traits.append(
                CivTrait(scope=p[1], trait_type=p[2], name=p[3], description=p[4])
            )
        elif tag in ("UNIT", "DISTRICT", "BUILDING", "IMPROVEMENT") and len(p) >= 8:
            ab.uniques.append(
                UniqueItem(
                    kind=tag,
                    item_type=p[1],
                    name=p[2],
                    combat=_safe_int(p[3]),
                    ranged_combat=_safe_int(p[4]),
                    prereq_tech=p[5],
                    prereq_civic=p[6],
                    cost=_safe_int(p[7]),
                )
            )
        elif tag == "DIAG" and len(p) >= 3:
            ab.diagnostics[p[1]] = _safe_int(p[2])
    return ab
