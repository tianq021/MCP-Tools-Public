extern alias GameAssembly;
using UnityEngine.SceneManagement;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private object Objectives()
    {
        if (!Ready()) return new { ready = false, scene = SceneManager.GetActiveScene().name };
        var goals = game!.global.newObjectives;
        var score = game.optionalScoreCard;
        var outcome = score.HasValue() ? score.value : null;
        var current = goals?.current;
        var step = goals?.progress;
        var progress = step != null && step.HasValue() ? step.value.Item2 : null;
        return new {
            ready = true, session_id = session, turn = game.simulator.gameTurn,
            level_id = game.launchData?.builder?.ident, level_title = Text(game.launchData?.builder?.GetConceptTitle()),
            outcome = outcome == null ? "in_progress" : outcome.won ? "won" : "lost",
            result = outcome == null ? null : new { reason = Text(outcome.message), score = outcome.score, stars = outcome.stars, turn = outcome.gameturn },
            completed = goals?.GetNumCompletedGoals(), total = goals?.GetNumGoals(), objectives_complete = goals?.isComplete,
            current = current == null ? null : new { id = current.outline?.ident, title = Text(current.GetTitle()),
                description = Text(current.outline?.GetDescription()), complete = current.isComplete,
                progress = progress == null ? null : new { current = progress.current, target = progress.desired, fraction = progress.percentage } }
        };
    }
}
