local pid = Game.GetLocalPlayer()
print("TURN=" .. tostring(Game.GetCurrentGameTurn()))
print("ACTIVE=" .. tostring(Players[pid]:IsTurnActive()))
print("---END---")
