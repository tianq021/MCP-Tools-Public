-- probe Map size API
local names = {"GetGridWidth","GetGridHeight","GetWidth","GetHeight","GetGridSize","GetMaxX","GetMaxY"}
for _, n in ipairs(names) do
  local f = Map[n]
  print("PROBE|" .. n .. "|" .. type(f))
end
local ok, w, h = pcall(function() return Map.GetGridSize() end)
print("GetGridSize|" .. tostring(ok) .. "|" .. tostring(w) .. "|" .. tostring(h))
-- try iterating plots to find max
local maxx, maxy = 0, 0
for i = 0, 5000 do
  local okp, p = pcall(function() return Map.GetPlotByIndex(i) end)
  if okp and p ~= nil then
    local x, y = p:GetX(), p:GetY()
    if x > maxx then maxx = x end
    if y > maxy then maxy = y end
  else
    break
  end
end
print("MAX|" .. maxx .. "|" .. maxy)
print("---END---")
