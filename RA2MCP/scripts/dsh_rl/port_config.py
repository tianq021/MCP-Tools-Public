"""P0-6: Windows TCP 端口预检与训练端口配置（独立模块，不启动游戏/训练）。

检查项：
1. Windows IPv4 TCP 排除端口范围（netsh excludedportrange）；
2. Windows IPv6 TCP 排除端口范围；
3. 每个 Worker 端口的实际 Socket Bind（127.0.0.1，IPv4 必须成功）；
4. 训练所需全部 Worker 端口（base_port + 0..workers-1，唯一且范围合法）；
5. 监听冲突（netstat LISTENING，包含 PID）；
6. IPv6 bind 可用性（信息/警告，不作为游戏启动硬失败；游戏桥接使用 IPv4）。

安全策略：任何 IPv4 bind 失败 / IPv4 排除 / 端口重复 → `ok=False`，调用方必须禁止启动游戏。
"""
import argparse
import json
import pathlib
import re
import socket
import subprocess
import time

HOST4 = "127.0.0.1"
HOST6 = "::1"
DEFAULT_BASE_PORT = 14621


class PortPreflightError(RuntimeError):
    def __init__(self, code, message, report=None, details=None):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.report = report or {}
        self.details = details or {}


def _decode(raw):
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8", "gbk", "cp936", "oem"):
        try:
            return raw.decode(encoding)
        except BaseException:  # noqa: BLE001
            continue
    return raw.decode("utf-8", errors="replace")


def parse_excluded_output(text):
    """解析 netsh excludedportrange 输出中的 Start/End 两列。"""
    ranges = []
    for line in _decode(text).splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s*\*?\s*$", line)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        if 1 <= start <= end <= 65535:
            ranges.append((start, end))
    return sorted(set(ranges))


def windows_excluded_ranges(family="ipv4", protocol="tcp"):
    """读取 Windows 当前 TCP 排除端口范围；失败返回空并标记 UNKNOWN（不伪造可用）。"""
    command = ["netsh", "interface", family, "show", "excludedportrange",
               "protocol=%s" % protocol]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30)
    except BaseException:  # noqa: BLE001
        return []
    if result.returncode != 0:
        return []
    return parse_excluded_output(result.stdout)


def _in_ranges(port, ranges):
    for start, end in ranges or []:
        if start <= port <= end:
            return (start, end)
    return None


def socket_bind_test(host, port, family=socket.AF_INET):
    """对真实地址/端口做 bind 测试；不设置 SO_REUSEADDR，避免掩盖冲突。"""
    family = family or socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((host, int(port)))
        return {"ok": True, "host": host, "port": int(port), "errno": 0,
                "winerror": 0, "message": ""}
    except OSError as error:
        return {
            "ok": False, "host": host, "port": int(port),
            "errno": getattr(error, "errno", None),
            "winerror": getattr(error, "winerror", None),
            "message": str(error),
        }
    finally:
        try:
            sock.close()
        except BaseException:  # noqa: BLE001
            pass


def listener_conflicts(ports):
    """从 netstat 读取 LISTENING 占用，返回 {port: [{pid, local_address}]}。"""
    wanted = set(int(port) for port in ports or [])
    conflicts = {port: [] for port in wanted}
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                capture_output=True, timeout=30)
        text = _decode(result.stdout)
    except BaseException:  # noqa: BLE001
        return conflicts
    for line in text.splitlines():
        match = re.match(r"^\s*TCP\s+(\S+)\s+\S+\s+LISTENING\s+(\d+)\s*$", line)
        if not match:
            continue
        local, pid = match.group(1), int(match.group(2))
        try:
            port = int(local.rsplit(":", 1)[1])
        except BaseException:  # noqa: BLE001
            continue
        if port in conflicts:
            conflicts[port].append({"pid": pid, "local_address": local})
    return conflicts


def worker_ports(base_port, workers):
    base_port = int(base_port)
    workers = int(workers)
    if workers <= 0:
        raise ValueError("workers must be > 0")
    if base_port < 1 or base_port + workers - 1 > 65535:
        raise ValueError("base_port out of legal range: %s + %s" % (base_port, workers))
    ports = [base_port + index for index in range(workers)]
    if len(set(ports)) != len(ports):
        raise ValueError("duplicate worker ports")
    return ports


def preflight_ports(base_port, workers, host4=HOST4, check_ipv6=True,
                    excluded_ipv4=None, excluded_ipv6=None, check_listeners=True):
    """完整端口预检；任何 IPv4 不可用/IPv4 排除/重复 → ok=False。"""
    base_port = int(base_port)
    workers = int(workers)
    ports = worker_ports(base_port, workers)
    if excluded_ipv4 is None:
        excluded_ipv4 = windows_excluded_ranges("ipv4")
    if excluded_ipv6 is None:
        excluded_ipv6 = windows_excluded_ranges("ipv6")
    conflicts = listener_conflicts(ports) if check_listeners else {port: [] for port in ports}
    entries = []
    failures = []
    warnings = []
    seen = set()
    for index, port in enumerate(ports):
        if port in seen:
            failures.append({"index": index, "port": port, "code": "DUPLICATE_PORT",
                             "message": "worker port duplicated"})
        seen.add(port)
        in_ipv4 = _in_ranges(port, excluded_ipv4)
        in_ipv6 = _in_ranges(port, excluded_ipv6)
        bind4 = socket_bind_test(host4, port, socket.AF_INET)
        bind6 = {"ok": None, "host": HOST6, "port": port, "errno": None,
                 "winerror": None, "message": "IPv6 check disabled"}
        if check_ipv6 and socket.has_ipv6:
            bind6 = socket_bind_test(HOST6, port, socket.AF_INET6)
        entry = {
            "index": index,
            "port": port,
            "ipv4_excluded_range": list(in_ipv4) if in_ipv4 else None,
            "ipv6_excluded_range": list(in_ipv6) if in_ipv6 else None,
            "ipv4_bind": bind4,
            "ipv6_bind": bind6,
            "listener_conflicts": conflicts.get(port, []),
            "ok": True,
            "failure_codes": [],
        }
        if in_ipv4:
            entry["ok"] = False
            entry["failure_codes"].append("IPV4_EXCLUDED_PORT")
            failures.append({"index": index, "port": port, "code": "IPV4_EXCLUDED_PORT",
                             "message": "port in Windows IPv4 excluded range %s"
                                        % (in_ipv4,)})
        if not bind4.get("ok"):
            entry["ok"] = False
            entry["failure_codes"].append("IPV4_BIND_FAILED")
            failures.append({"index": index, "port": port, "code": "IPV4_BIND_FAILED",
                             "errno": bind4.get("errno"),
                             "winerror": bind4.get("winerror"),
                             "message": bind4.get("message")})
        if conflicts.get(port):
            entry["ok"] = False
            entry["failure_codes"].append("PORT_IN_USE")
            failures.append({"index": index, "port": port, "code": "PORT_IN_USE",
                             "listeners": conflicts[port]})
        if check_ipv6:
            if in_ipv6:
                entry["failure_codes"].append("IPV6_EXCLUDED_PORT")
                warnings.append({"index": index, "port": port,
                                 "code": "IPV6_EXCLUDED_PORT",
                                 "message": "port in Windows IPv6 excluded range; "
                                            "IPv4 game bridge may still work"})
            if bind6.get("ok") is False:
                entry["failure_codes"].append("IPV6_BIND_UNAVAILABLE")
                warnings.append({"index": index, "port": port,
                                 "code": "IPV6_BIND_UNAVAILABLE",
                                 "errno": bind6.get("errno"),
                                 "message": bind6.get("message")})
        entries.append(entry)
    report = {
        "ok": not failures,
        "base_port": base_port,
        "workers": workers,
        "ports": ports,
        "worker_ports": [{"index": index, "port": port}
                         for index, port in enumerate(ports)],
        "host_ipv4": host4,
        "host_ipv6": HOST6,
        "ipv4_excluded_ranges": [list(item) for item in excluded_ipv4],
        "ipv6_excluded_ranges": [list(item) for item in excluded_ipv6],
        "entries": entries,
        "failures": failures,
        "warnings": warnings,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "suggested_base_ports": [],
    }
    if failures:
        try:
            report["suggested_base_ports"] = suggest_base_ports(
                workers, count=5, excluded_ipv4=excluded_ipv4)
        except BaseException:  # noqa: BLE001
            report["suggested_base_ports"] = []
    return report


def suggest_base_ports(workers, count=5, start=14550, scan_limit=2000,
                       excluded_ipv4=None):
    """自动寻找可用端口基址（全部 worker 端口 IPv4 bind 成功且不在排除范围）。"""
    if excluded_ipv4 is None:
        excluded_ipv4 = windows_excluded_ranges("ipv4")
    suggestions = []
    for base in range(int(start), min(65535, int(start) + int(scan_limit))):
        try:
            ports = worker_ports(base, workers)
        except ValueError:
            break
        if any(_in_ranges(port, excluded_ipv4) for port in ports):
            continue
        if all(socket_bind_test(HOST4, port).get("ok") for port in ports):
            suggestions.append(base)
            if len(suggestions) >= int(count):
                break
    return suggestions


def format_failure(report):
    lines = ["PORT_PREFLIGHT_FAILED base_port=%s workers=%s"
             % (report.get("base_port"), report.get("workers"))]
    for failure in report.get("failures") or []:
        detail = {key: failure.get(key) for key in
                  ("index", "port", "code", "errno", "winerror", "message")
                  if failure.get(key) is not None}
        lines.append("  FAILED_PORT %s" % json.dumps(detail, ensure_ascii=False))
    suggestions = report.get("suggested_base_ports") or []
    if suggestions:
        lines.append("  SUGGESTED_BASE_PORTS_VERIFIED=%s" % suggestions)
    else:
        lines.append("  SUGGESTED_BASE_PORTS_VERIFIED=[] (specify a base port manually)")
    return "\n".join(lines)


def configure_game_port(game_dir, port):
    """把实际 bridge port 写入该游戏副本的 ra2yrcpp.json（游戏启动时读取）。"""
    path = pathlib.Path(game_dir) / "ra2yrcpp.json"
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    config["port"] = int(port)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def validate_approval_port_config(config, base_port, workers):
    """审批配置中的 port_configuration 必须与实际运行端口一致。"""
    expected = (config or {}).get("port_configuration") or {}
    if not expected:
        return {"declared": False}
    ports = worker_ports(base_port, workers)
    expected_base = expected.get("base_port")
    if expected_base is not None and int(expected_base) != int(base_port):
        raise PortPreflightError(
            "PORT_CONFIG_MISMATCH",
            "approval config base_port=%s but runtime base_port=%s"
            % (expected_base, base_port),
            details={"expected_base_port": expected_base, "runtime_base_port": base_port})
    expected_workers = expected.get("workers")
    if expected_workers is not None and int(expected_workers) != int(workers):
        raise PortPreflightError(
            "PORT_CONFIG_MISMATCH",
            "approval config workers=%s but runtime workers=%s"
            % (expected_workers, workers),
            details={"expected_workers": expected_workers, "runtime_workers": workers})
    expected_ports = expected.get("worker_ports")
    if expected_ports is not None and [int(p) for p in expected_ports] != ports:
        raise PortPreflightError(
            "PORT_CONFIG_MISMATCH",
            "approval config worker_ports=%s but runtime worker ports=%s"
            % (expected_ports, ports),
            details={"expected_worker_ports": expected_ports,
                     "runtime_worker_ports": ports})
    return {"declared": True, "base_port": int(base_port), "workers": int(workers),
            "worker_ports": ports}


def run_report(base_port, workers, preflight):
    """run.json 中记录的端口字段（不伪造可用性，保留真实预检结果）。"""
    return {
        "base_port": int(base_port),
        "workers": int(workers),
        "worker_ports": worker_ports(base_port, workers),
        "port_preflight_ok": bool((preflight or {}).get("ok")),
        "port_preflight": preflight,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="P0-6 Windows worker port preflight")
    parser.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = preflight_ports(args.base_port, args.workers)
    except PortPreflightError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)},
                         ensure_ascii=False, indent=2))
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["ok"]:
        print("PORT_PREFLIGHT_OK base=%d workers=%d ports=%s"
              % (report["base_port"], report["workers"], report["ports"]))
        for entry in report["entries"]:
            print("  port=%d ipv4_bind=%s ipv6_bind=%s ipv4_excluded=%s"
                  % (entry["port"], entry["ipv4_bind"]["ok"],
                     entry["ipv6_bind"].get("ok"),
                     bool(entry["ipv4_excluded_range"])))
    else:
        print(format_failure(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
