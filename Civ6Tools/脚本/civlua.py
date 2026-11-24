r"""Run a Lua file through the Civ6 MCP bridge.

Usage: python 脚本\civlua.py <file.lua> [gamecore|ingame] [-f out.txt]
"""
import sys

from civc import call

argv = sys.argv[1:]
out_file = None
if '-f' in argv:
    i = argv.index('-f')
    out_file = argv[i + 1]
    del argv[i:i + 2]

path = argv[0]
context = argv[1] if len(argv) > 1 else 'gamecore'
with open(path, encoding='utf-8') as fh:
    code = fh.read()

text, is_error = call('run_lua', {'code': code, 'context': context})
header = f'### run_lua[{context}] {path} [{"ERROR" if is_error else "OK"}]'
body = header + '\n' + text.strip() + '\n'
if out_file:
    with open(out_file, 'w', encoding='utf-8') as fh:
        fh.write(body)
print(body)
