"""Bind a worker to one isolated game's port and instance lock."""
from pathlib import Path
import hashlib,json

def resolve_runtime(plan,root):
    root=Path(root).resolve();game=Path(plan['game_dir']).resolve()
    if game.parent!=root or not game.name.startswith('test-game'):
        raise ValueError('only a direct isolated test-game copy is allowed')
    port=plan.get('port',14621);instance=plan.get('instance',0)
    if type(port) is not int or not 1024<=port<=65535 or type(instance) is not int or not 0<=instance<=64:
        raise ValueError('invalid worker port or instance')
    config=json.loads((game/'ra2yrcpp.json').read_text(encoding='utf-8-sig'))
    if config['port']!=port:raise ValueError('worker port differs from isolated bridge config')
    if instance:
        marker=json.loads((game/'ra2mcp-instance.json').read_text(encoding='utf-8-sig'))
        actual=hashlib.sha256((game/'gamemd-spawn-ra2yrcpp.exe').read_bytes()).hexdigest()
        if marker.get('instance')!=instance or not marker.get('patched') or marker['patched_sha256'].lower()!=actual:
            raise ValueError('worker instance/mutex/executable mismatch')
    return game,port,instance
