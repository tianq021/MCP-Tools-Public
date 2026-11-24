"""Keep interruption and cleanup failure separate from normal match completion."""
import subprocess
def finish(result, environment_kind):
    if result.get('stop_reason'):
        result.update(status='STOPPED',end_kind='interrupted',environment_kind_at_stop=environment_kind)
    else:
        result.update(status='FINISHED',end_kind=environment_kind)


def exit_code(result):
    return 1 if result.get('status')=='FAILED' or result.get('cleanup_error') else 0


def close_checked(env):
    """Check the owned runner after its best-effort close drops env.runner."""
    runner = getattr(env, 'runner', None)
    proc = getattr(runner, 'proc', None)
    errors = []
    try:
        env.close()
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    if getattr(runner, 'cleanup_failure', None):
        errors.append(f'runner cleanup failure: {runner.cleanup_failure}')
    if proc is not None and proc.poll() is None:
        # Windows taskkill can return before the process handle becomes signaled.
        # Bound the grace period; an actually orphaned process still fails closed.
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                errors.append(f'owned game still running after close: pid={proc.pid}')
    if errors:
        raise RuntimeError('; '.join(errors))
