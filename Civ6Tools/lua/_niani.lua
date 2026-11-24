local pid1 = 1
for _, c in Players[pid1]:GetCities():Members() do
  if tostring(c:GetName()):find("NIANI") or tostring(c:GetName()):find("尼亚尼") then
    for _, d in c:GetDistricts():Members() do
      local dInfo = GameInfo.Districts[d:GetType()]
      if dInfo and dInfo.DistrictType == "DISTRICT_CITY_CENTER" then
        local maxO = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER)
        local curO = d:GetDamage(DefenseTypes.DISTRICT_OUTER)
        local maxG = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON)
        local curG = d:GetDamage(DefenseTypes.DISTRICT_GARRISON)
        print("NIANI|walls=" .. (maxO - curO) .. "/" .. maxO .. "|cityHP=" .. (maxG - curG) .. "/" .. maxG .. "|def=" .. d:GetDefenseStrength())
      end
    end
  end
end
print("---END---")
