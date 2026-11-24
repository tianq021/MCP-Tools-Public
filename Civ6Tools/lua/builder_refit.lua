-- ============================================================
-- builder_refit.lua : 模组「整备建造者」——0 金回满建造次数 + 恢复移动力
-- 用法: python 脚本\civlua.py lua/builder_refit.lua ingame
-- 来源: 模组 QHT_ResourceMakers 提供的 ExposedMembers.QHT_BuilderRefresh
-- 注意: 必要时会销毁旧单位并重建 → **建造者 ID 会变化**，整备后必须重读 get_units
-- 说明: 上限算法读基础次数 + 金字塔 +1 + 农奴制 +2 + 公共工程 +2（不含全部模组加成）
-- ============================================================
local me = Game.GetLocalPlayer()
local builderIdx = GameInfo.Units['UNIT_BUILDER'].Index

local function report(tag)
  print('--- ' .. tag .. ' ---')
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetType() == builderIdx then
      local charges = 'n/a'
      pcall(function() charges = u:GetBuildCharges() end)
      local plot = Map.GetPlot(u:GetX(), u:GetY())
      print(string.format('builder id=%d at=%d,%d charges=%s moves=%s/%s owner=%s',
        u:GetID(), u:GetX(), u:GetY(), tostring(charges),
        tostring(u:GetMovesRemaining()), tostring(u:GetMaxMoves()),
        tostring(plot and plot:GetOwner() or -99)))
    end
  end
end

report('BEFORE')

local exposed = ExposedMembers and ExposedMembers.QHT_BuilderRefresh
if exposed and exposed.RefreshBuilder then
  local ids = {}
  for _, u in Players[me]:GetUnits():Members() do
    if u:GetType() == builderIdx then ids[#ids + 1] = u:GetID() end
  end
  for _, id in ipairs(ids) do
    local ok, res = pcall(function() return exposed.RefreshBuilder(me, id) end)
    print(string.format('REFRESH id=%d ok=%s res=%s', id, tostring(ok), tostring(res)))
  end
else
  print('NO_REFRESH_API — 模组未启用或未加载')
end

report('AFTER')
print('---END---')
