// Compile-only contract. These methods MUST NOT be executed outside the game.
// This is not a plugin and has no entry point, callbacks, patches, or injector.
using UnityEngine;

public static class ApiSurfaceProbe
{
    public static void ReadState(int slot)
    {
        Player player = HeroController.players[slot];
        int controller = player.controllerNum;
        int reverseSlot = HeroController.GetPlayerNumForControllerID(controller);
        bool local = player.IsMine;
        bool alive = player.IsAlive();
        bool pending = player.IsPendingRespawnOrResuingInProgress();
        int lives = player.Lives;
        TestVanDammeAnim actor = player.character;
        float x = actor.X, y = actor.Y;
        int health = actor.health, direction = actor.Direction, ammo = actor.SpecialAmmo;
        bool onGround = actor.IsOnGround();
        bool offline = Connect.IsOffline;
        bool finished = GameModeController.IsLevelFinished();
        bool loading = LevelSelectionController.IsLoadingScene;
        bool blocked = InputReader.IsBlocked;
        bool paused = PauseController.Paused;
        bool cutscene = CutsceneController.InCutscene;
        bool canMove = CutsceneController.PlayersCanMove();
        int level = LevelSelectionController.CurrentLevelNum;
        GameMode mode = GameModeController.GameMode;
        PauseStatus pause = PauseController.pauseStatus;
        int units = Map.units.Count, checkpoints = Map.checkPoints.Count;
        bool ground = Map.IsGround(actor.collumn, actor.row, true);
        bool hostile = GameModeController.DoesPlayerNumDamage(slot, -1);
        KeyCode fire = PlayerOptions.Instance.keyPlayer1Fire;
    }

    public static void ReadInputSignatures(Player player, int controller)
    {
        bool up = false, down = false, left = false, right = false, fire = false;
        bool jump = false, special = false, melee = false, gesture = false, sprint = false;
        InputReader.GetInput(controller, ref up, ref down, ref left, ref right, ref fire,
            ref jump, ref special, ref melee, ref gesture, ref sprint, false, false);
        player.GetInput(ref up, ref down, ref left, ref right, ref fire,
            ref jump, ref special, ref melee, ref gesture, ref sprint);
        bool accept = false, cancel = false;
        InputReader.GetMenuInput(controller, ref up, ref down, ref left, ref right, ref accept, ref cancel);
    }

    public static void ReadNpcInputSignature(PolymorphicAI ai)
    {
        bool left = false, right = false, up = false, down = false, jump = false, fire = false;
        bool special1 = false, special2 = false, special3 = false, special4 = false, climb = false;
        ai.GetInput(ref left, ref right, ref up, ref down, ref jump, ref fire,
            ref special1, ref special2, ref special3, ref special4, ref climb);
    }
}
