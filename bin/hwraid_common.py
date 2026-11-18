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


_SSACLI_HEADER_RE = re.compile(
    r"(?:smart\s+array|smart\s+hba|dynamic\s+smart|smart\s+stack|smart\s+cloud|smart\s+e|embedded|in\s+slot|controller)",
    re.I,
)


def _ssacli_last_state(body):
    parts = [p.strip() for p in (body or "").split(",") if p.strip()]
    return parts[-1] if parts else ""


def _ssacli_blocks(text):
    blocks = []
    cur = None
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        if not line[0].isspace() and _SSACLI_HEADER_RE.search(line):
            cur = {"header": line.strip(), "lines": [line]}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    return blocks


def _ssacli_ctrl_fields(header):
    model = ""
    mm = re.search(r"smart\s+array\s+([A-Za-z0-9+]+)", header, re.I)
    if mm:
        model = mm.group(1)
    else:
        model = re.split(r"\s+in\s+slot|\s*\(|\s+RAID\s+Mode|\s+Embedded", header, flags=re.I)[0].strip()
    if not model:
        model = header.strip()
    sn = re.search(r"(?:SN|Serial\s*No\.?|Serial)\s*:?\s*([A-Za-z0-9\-]+)", header, re.I)
    fw = re.search(r"(?:FW|Firmware|Rev\.?)\s*:?\s*([A-Za-z0-9.]+)", header, re.I)
    return model, (sn.group(1) if sn else ""), (fw.group(1) if fw else "")


def _ssacli_infer_state(failed, predictive, rebuilding, logical_count, drive_count):
    if failed:
        return "Failed"
    if predictive:
        return "Predictive Failure"
    if rebuilding:
        return "Rebuilding"
    if logical_count or drive_count:
        return "OK"
    return ""


def _parse_ssacli(status, config):
    statuses = [s.strip() for s in re.findall(r"^\s*Controller\s+Status:\s*(.+?)\s*$", status or "", re.M | re.I)]
    out = {}
    for idx, block in enumerate(_ssacli_blocks(config)):
        text = "\n".join(block["lines"])
        model, serial, firmware = _ssacli_ctrl_fields(block["header"])
        drive_states = [
            _ssacli_last_state(m.group(2))
            for m in re.finditer(r"^\s*physicaldrive\s+(\S+)\s*\(([^)]*)\)", text, re.M | re.I)
        ]
        failed = sum(1 for s in drive_states if re.search(r"Failed|Missing", s, re.I))
        predictive = sum(1 for s in drive_states if re.search(r"Predictive", s, re.I))
        rebuilding = sum(1 for s in drive_states if re.search(r"Rebuild|Reconstruct|Recovering", s, re.I))
        logical_count = len(re.findall(r"^\s*logicaldrive\s+\S+", text, re.M | re.I))
        cstat = statuses[idx] if idx < len(statuses) else ""
        if failed or predictive:
            state_ok = 0
        elif rebuilding:
            state_ok = 0
        elif cstat and re.search(r"Failed|Degraded|Critical|Non[ -]?Redundant", cstat, re.I):
            state_ok = 0
        elif cstat and re.search(r"Optimal|Ready|Online|Good|OK", cstat, re.I):
            state_ok = 1
        elif drive_states or logical_count:
            state_ok = 1
        else:
            state_ok = None
        rec = {
            "raw_state": cstat,
            "product": model,
            "serial": serial,
            "firmware": firmware,
            "drive_count": len(drive_states),
            "failed_drives": failed,
            "predictive_drives": predictive,
            "rebuilding": rebuilding,
            "logical_count": logical_count,
            "state": cstat or _ssacli_infer_state(failed, predictive, rebuilding, logical_count, len(drive_states)),
            "state_ok": state_ok,
        }
        out["c%d" % idx] = rec
    return out


def _ssacli_collect(cli):
    status = run([cli, "ctrl", "all", "show", "status"])
    config = run([cli, "ctrl", "all", "show", "config"])
    return _parse_ssacli(status, config)


def _is_ssacli(basename):
    return "ssacli" in (basename or "").lower()


def collect():
    cli = _find_cli()
    if not cli:
        return {"status": "absent", "controllers": {}, "cli": None, "controller_count": 0}
    basename = os.path.basename(cli)
    collect_fn = _ssacli_collect if _is_ssacli(basename) else _storcli_collect
    ctrls = collect_fn(cli)
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
