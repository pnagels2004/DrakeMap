"""
drake_probe.py - feed live port values of a running Drake simulation to drake_diagram_viewer.py

Usage (replaces `sim.AdvanceTo(np.inf)` while you are debugging):

    from drake_probe import run_debug

    sim = Simulator(diagram, context)
    sim.Initialize()
    sim.set_target_realtime_rate(1)
    run_debug(sim, diagram, "system_diagram")      # name = the stem of your SVG file

It advances the simulation in chunks of `period` simulated seconds (default 1 s). After every
chunk it evaluates the input and output ports of every system (recursively through all
sub-diagrams) and writes a summary to

    $XDG_CACHE_HOME/drake_diagram_viewer/<name>.values.json      (default: ~/.cache/drake_diagram_viewer/)

The viewer watches that file and lists the values of the selected block(s) in its inspector.
The file is replaced atomically, so the viewer never reads a half-written snapshot.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

try:
    from pydrake.systems.framework import Diagram
except ImportError:  # pragma: no cover
    Diagram = ()

MAX_VALUES = 16  # arrays longer than this are reduced to min / max / norm
MAX_ITEMS = 6  # list-valued ports: how many items are summarised


# =============================================================================
# cache location (Fedora / any XDG system) - keep identical to the viewer
# =============================================================================
def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "drake_diagram_viewer"


def values_path(name) -> Path:
    """`name` may be "system_diagram", "system_diagram.svg" or a full path to the SVG."""
    return cache_dir() / f"{Path(str(name)).stem}.values.json"


# =============================================================================
# value summaries (small, JSON-friendly, never huge)
# =============================================================================
def _floats(x):
    return [float(v) for v in np.asarray(x, dtype=float).ravel()]


def _array(a) -> dict:
    a = np.asarray(a)
    if a.dtype.kind not in "biuf":
        return {"kind": "object", "type": f"ndarray[{a.dtype}]"}
    flat = a.astype(float).ravel()
    fin = flat[np.isfinite(flat)]
    d = {
        "kind": "array",
        "shape": list(a.shape),
        "values": [float(v) for v in flat[:MAX_VALUES]],
        "truncated": bool(flat.size > MAX_VALUES),
        "nonfinite": int(flat.size - fin.size),
    }
    nan = float("nan")
    d["min"] = float(fin.min()) if fin.size else nan
    d["max"] = float(fin.max()) if fin.size else nan
    d["norm"] = float(np.linalg.norm(fin)) if fin.size else nan
    return d


def _summarize(v, depth: int = 0) -> dict:
    if isinstance(v, (bool, int, float, np.generic)) and not isinstance(
        v, (str, bytes)
    ):
        return {"kind": "scalar", "value": float(v)}
    if isinstance(v, np.ndarray):
        return _array(v)
    tname = type(v).__name__
    try:
        from pydrake.math import RigidTransform, RollPitchYaw, RotationMatrix

        if isinstance(v, RigidTransform):
            return {
                "kind": "pose",
                "p": _floats(v.translation()),
                "rpy": _floats(RollPitchYaw(v.rotation()).vector()),
            }
        if isinstance(v, RotationMatrix):
            return {"kind": "rotation", "rpy": _floats(RollPitchYaw(v).vector())}
    except Exception:
        pass
    try:
        if hasattr(v, "rotational") and hasattr(
            v, "translational"
        ):  # SpatialVelocity/Force/...
            return {
                "kind": "spatial",
                "type": tname,
                "rot": _floats(v.rotational()),
                "trans": _floats(v.translational()),
            }
        if (
            hasattr(v, "width")
            and hasattr(v, "height")
            and isinstance(getattr(v, "data", None), np.ndarray)
        ):
            data = v.data.astype(float)
            fin = data[np.isfinite(data)]
            return {
                "kind": "image",
                "type": tname,
                "width": int(v.width()),
                "height": int(v.height()),
                "channels": int(data.shape[2]) if data.ndim == 3 else 1,
                "min": float(fin.min()) if fin.size else float("nan"),
                "max": float(fin.max()) if fin.size else float("nan"),
            }
        if hasattr(v, "xyzs") and hasattr(v, "size"):
            return {"kind": "pointcloud", "n": int(v.size())}
        if hasattr(v, "num_point_pair_contacts"):  # ContactResults
            info = f"{v.num_point_pair_contacts()} point-pair"
            if hasattr(v, "num_hydroelastic_contacts"):
                info += f", {v.num_hydroelastic_contacts()} hydroelastic"
            return {"kind": "object", "type": tname, "info": info + " contacts"}
    except Exception:
        pass
    if isinstance(v, (list, tuple)):
        items = [] if depth >= 2 else [_summarize(x, depth + 1) for x in v[:MAX_ITEMS]]
        return {"kind": "list", "n": len(v), "items": items}
    if isinstance(v, dict):
        return {"kind": "object", "type": "dict", "info": f"{len(v)} keys"}
    return {"kind": "object", "type": tname}


# =============================================================================
# snapshot of the whole diagram
# =============================================================================
def _port_values(system, ctx, which: str, skipped: bool) -> dict:
    out = {}
    n = system.num_input_ports() if which == "in" else system.num_output_ports()
    for i in range(n):
        port = system.get_input_port(i) if which == "in" else system.get_output_port(i)
        name = port.get_name()
        if skipped:
            out[name] = {"kind": "skipped"}
            continue
        try:
            if which == "in" and hasattr(port, "HasValue") and not port.HasValue(ctx):
                out[name] = {"kind": "unconnected"}
            else:
                out[name] = _summarize(port.Eval(ctx))
        except Exception as exc:  # one broken port must never kill the simulation
            out[name] = {
                "kind": "error",
                "msg": (str(exc).strip().splitlines() or [type(exc).__name__])[0][:160],
            }
    return out


def snapshot(diagram, context, skip=()) -> dict:
    """Evaluate every port of every system below `diagram` (the root is not listed itself).

    Systems are keyed by the "/"-joined names from the first level below the root down to the system.
    `skip` holds system names (or such paths) whose ports must NOT be evaluated (e.g. a camera).
    """
    skip = set(skip)
    systems: dict = {}

    def walk(diag, ctx, path):
        for s in diag.GetSystems():
            p = path + [s.get_name()]
            key = "/".join(p)
            try:
                sub = diag.GetSubsystemContext(s, ctx)
            except Exception:
                continue
            skipped = s.get_name() in skip or key in skip
            systems[key] = {
                "type": type(s).__name__,
                "inputs": _port_values(s, sub, "in", skipped),
                "outputs": _port_values(s, sub, "out", skipped),
            }
            if isinstance(s, Diagram):
                walk(s, sub, p)

    walk(diagram, context, [])
    return {
        "version": 1,
        "diagram": diagram.get_name(),
        "sim_time": float(context.get_time()),
        "wall_time": time.time(),
        "systems": systems,
    }


def _write(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, default=str, separators=(",", ":")))
    os.replace(tmp, path)  # atomic: the viewer never sees a partial file


# =============================================================================
# the debug loop
# =============================================================================
def run_debug(
    simulator,
    diagram,
    name="system_diagram",
    period: float = 1.0,
    skip=(),
    end_time: float = float("inf"),
    verbose: bool = True,
):
    """Drop-in replacement for `simulator.AdvanceTo(np.inf)` that publishes port values.

    simulator : the (initialised) Simulator
    diagram   : the root Diagram the simulator runs
    name      : stem of the SVG the viewer shows (a path to the .svg also works)
    period    : simulated seconds between two snapshots
    skip      : system names (or "sub_diagram/system" paths) whose ports must not be evaluated
    end_time  : stop at this simulation time (default: run until the sim terminates / Ctrl+C)
    """
    path = values_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = simulator.get_context()
    extra = {"period": float(period)}

    def publish():
        data = snapshot(diagram, ctx, skip)
        data.update(extra)
        _write(path, data)

    if verbose:
        print(
            f"[drake_probe] writing a snapshot every {period:g} s of sim time to {path}"
        )
    publish()
    t = ctx.get_time()
    try:
        while t < end_time:
            target = min(t + period, end_time)
            simulator.AdvanceTo(target)
            publish()
            if (
                ctx.get_time() < target - 1e-9
            ):  # a termination condition / monitor stopped the sim
                if verbose:
                    print(
                        f"[drake_probe] simulation terminated at t = {ctx.get_time():.3f} s"
                    )
                break
            t = target
    except KeyboardInterrupt:
        try:
            publish()
        except Exception:
            pass
        if verbose:
            print(f"\n[drake_probe] stopped at t = {ctx.get_time():.3f} s")
