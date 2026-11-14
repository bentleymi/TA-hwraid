"""Shared collection logic for TA-hwraid (MegaCLI/StorCLI, SSAcli)."""
import json
import os
import re
import subprocess
import sys


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _find_cli():
    candidates = ["storcli3", "storcli", "perccli", "ssacli", "hpssacli", "MegaCli", "megacli64"]
    for c in candidates:
        for d in ("/usr/sbin", "/usr/bin", "/opt/MegaRAID/storcli", "/opt/lsi/MegaRAID/storcli"):
            p = os.path.join(d, c)
            if os.path.exists(p) and os.access(p, os.X_OK):
                return p
    return None


def _storcli_collect(cli):
    listing = run([cli, "/call", "show"])
    out = {}
    ctrls = re.findall(r"^\s*(\d+)\s*\((\w+)\)", listing, re.M)
    for num, state in ctrls:
        show = run([cli, "/c%s" % num, "show"])
        rec = {"raw_state": state, "product": "", "drive_count": 0, "failed_drives": 0, "state_ok": None}
        pm = re.search(r"Product Name\s*:\s*(\S+)", show)
        if pm:
            rec["product"] = pm.group(1)
        am = re.search(r"^Array\s+Info.*?Array Size\s*=\s*(\S+)", show, re.S)
        sm = re.search(r"State\s*=\s*(\S+)", show)
        sstate = sm.group(1) if sm else state
        rec["state"] = sstate
        rec["state_ok"] = 1 if re.search(r"Optimal|Ready|Online|Good", sstate) else (0 if re.search(r"Failed|Degraded|Critical", sstate) else None)
        drives = run([cli, "/c%s/vall" % num, "show"])
        dlines = [l for l in drives.splitlines() if re.match(r"^\s*\d+\s+", l)]
        rec["drive_count"] = len(dlines)
        rec["failed_drives"] = sum(1 for l in dlines if re.search(r"Failed|Missing", l))
        rec["rebuilding"] = sum(1 for l in dlines if re.search(r"Rebuild|Reconstruct", l))
        out["c%s" % num] = rec
    return out


def collect():
    cli = _find_cli()
    if not cli:
        return {"status": "absent", "controllers": {}, "cli": None, "controller_count": 0}
    ctrls = _storcli_collect(cli)
    if not ctrls:
        # CLI present but no controller (soft-raid box)
        return {"status": "absent", "controllers": {}, "cli": os.path.basename(cli), "controller_count": 0}
    status = "ok"
    for c in ctrls.values():
        if c.get("state_ok") == 0 or c.get("failed_drives", 0) > 0:
            status = "alarm"
    return {
        "status": status,
        "controllers": ctrls,
        "cli": os.path.basename(cli),
        "controller_count": len(ctrls),
    }


def metrics_payload(data):
    payload = {"controller_count": data.get("controller_count", 0)}
    for name, c in data.get("controllers", {}).items():
        for key in ("drive_count", "failed_drives", "rebuilding", "state_ok"):
            if c.get(key) is not None:
                payload["%s_%s" % (name, key)] = c[key]
    return payload


def emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def fail(message):
    sys.stderr.write("TA-hwraid collection error: %s\n" % message)
    return 1
