r"""Tiny CLI for the Civ6 MCP HTTP bridge (127.0.0.1:8766).

Usage:
    python 脚本\civc.py "get_units" "get_cities"                     # print tool text
    python 脚本\civc.py 'unit_action={"unit_id":131073,"action":"move","target_x":30,"target_y":7}'
    python 脚本\civc.py -f out.txt "get_units"                        # also write UTF-8 file

Names are the MCP tool names; optional =<json object> supplies arguments.
"""
import json
import sys
import urllib.request

URL = 'http://127.0.0.1:8766/call'


def call(name, arguments=None):
    payload = json.dumps({'name': name, 'arguments': arguments or {}}).encode('utf-8')
    req = urllib.request.Request(URL, data=payload,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)  # bytes -> utf-8 auto-detect
    text = '\n'.join(c.get('text', '') for c in data.get('content', []))
    return text, bool(data.get('isError'))


def main(argv):
    out_file = None
    specs = []
    argv = list(argv)
    args = argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '-f':
            out_file = args[i + 1]
            i += 2
        elif args[i] == '-j':
            with open(args[i + 1], encoding='utf-8') as fh:
                plan = json.load(fh)
            for step in plan:
                if isinstance(step, str):
                    name, _, raw = step.partition('=')
                    specs.append((name.strip(), json.loads(raw) if raw.strip() else {}))
                else:
                    specs.append((step['name'], step.get('arguments') or {}))
            i += 2
        else:
            name, _, raw = args[i].partition('=')
            specs.append((name.strip(), json.loads(raw) if raw.strip() else {}))
            i += 1

    chunks = []
    failed = False
    for name, call_args in specs:
        raw = json.dumps(call_args, ensure_ascii=False) if call_args else ''
        try:
            text, is_error = call(name, call_args)
        except Exception as exc:  # noqa: BLE001
            text, is_error = f'TRANSPORT_ERROR: {exc}', True
        tag = 'ERROR' if is_error else 'OK'
        header = f'### {name} ={raw}  [{tag}]'
        chunks.append(header)
        chunks.append(text.strip())
        chunks.append('')
        if is_error:
            failed = True
        sys.stderr.write(header + '\n')

    body = '\n'.join(chunks)
    if out_file:
        with open(out_file, 'w', encoding='utf-8') as fh:
            fh.write(body)
    print(body)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
