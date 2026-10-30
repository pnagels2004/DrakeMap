#!/usr/bin/env python3
"""
example_diagram.py - build a small nested Drake diagram and export it for drake_diagram_viewer.py

    python example_diagram.py                 # writes system_diagram.svg next to this script
    python drake_diagram_viewer.py system_diagram.svg

    python example_diagram.py --simulate      # also run a short simulation with drake_probe

The example is a position-controlled double integrator with a feedback loop:

    reference ──► controller (sub-diagram) ──► plant (sub-diagram) ──► logger
                      ▲                              │
                      └──────────── state ───────────┘

It contains two sub-diagrams (try folding them with the [-] button), a feedback
loop, a fan-out and a few differently coloured port types, which makes it a good
playground for highlighting, folding and grouping in the viewer.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from pydrake.systems.analysis import Simulator
from pydrake.systems.framework import DiagramBuilder
from pydrake.systems.controllers import PidController
from pydrake.systems.primitives import (
    Adder,
    Demultiplexer,
    Gain,
    Integrator,
    Multiplexer,
    Saturation,
    Sine,
    VectorLogSink,
)


# =============================================================================
# sub-diagrams
# =============================================================================
def make_plant() -> "Diagram":
    """Double integrator with input saturation and a little viscous damping.

    input  : force            (1)
    output : state [q, qd]    (2)
    """
    b = DiagramBuilder()
    sat = b.AddNamedSystem("force_limit", Saturation([-5.0], [5.0]))
    damping = b.AddNamedSystem("damping", Gain(-0.2, 1))
    sum_f = b.AddNamedSystem("force_sum", Adder(2, 1))
    accel = b.AddNamedSystem("inv_mass", Gain(1.0 / 2.0, 1))  # m = 2 kg
    vel = b.AddNamedSystem("velocity", Integrator(1))
    pos = b.AddNamedSystem("position", Integrator(1))
    state = b.AddNamedSystem("state_mux", Multiplexer([1, 1]))

    b.Connect(sat.get_output_port(), sum_f.get_input_port(0))
    b.Connect(damping.get_output_port(), sum_f.get_input_port(1))
    b.Connect(sum_f.get_output_port(), accel.get_input_port())
    b.Connect(accel.get_output_port(), vel.get_input_port())
    b.Connect(vel.get_output_port(), pos.get_input_port())
    b.Connect(vel.get_output_port(), damping.get_input_port())
    b.Connect(pos.get_output_port(), state.get_input_port(0))
    b.Connect(vel.get_output_port(), state.get_input_port(1))

    b.ExportInput(sat.get_input_port(), "force")
    b.ExportOutput(state.get_output_port(), "state")
    return b.Build()


def make_controller() -> "Diagram":
    """PID on position plus a feed-forward term.

    inputs  : desired_state [q_d, qd_d] (2), estimated_state [q, qd] (2)
    output  : force (1)
    """
    b = DiagramBuilder()
    pid = b.AddNamedSystem("pid", PidController(kp=[20.0], ki=[1.0], kd=[8.0]))
    ff = b.AddNamedSystem("feed_forward", Gain(0.5, 1))
    total = b.AddNamedSystem("force_sum", Adder(2, 1))
    des_split = b.AddNamedSystem("desired_split", Demultiplexer(2, 1))

    b.Connect(pid.get_output_port_control(), total.get_input_port(0))
    b.Connect(des_split.get_output_port(1), ff.get_input_port())
    b.Connect(ff.get_output_port(), total.get_input_port(1))

    b.ExportInput(pid.get_input_port_desired_state(), "desired_state")
    b.ExportInput(pid.get_input_port_estimated_state(), "estimated_state")
    # the same desired-state input also feeds the feed-forward branch
    b.ConnectInput("desired_state", des_split.get_input_port())
    b.ExportOutput(total.get_output_port(), "force")
    return b.Build()


# =============================================================================
# top level
# =============================================================================
def build_diagram():
    b = DiagramBuilder()

    # reference: q_d = sin(t), qd_d = cos(t)
    reference = b.AddNamedSystem(
        "reference",
        Sine(np.array([1.0, 1.0]), np.array([1.0, 1.0]), np.array([0.0, np.pi / 2])),
    )

    controller = b.AddNamedSystem("controller", make_controller())
    plant = b.AddNamedSystem("plant", make_plant())

    # observation split + logging
    state_split = b.AddNamedSystem("state_split", Demultiplexer(2, 1))
    logger = b.AddNamedSystem("state_logger", VectorLogSink(2))

    b.Connect(reference.get_output_port(0), controller.GetInputPort("desired_state"))
    b.Connect(plant.GetOutputPort("state"), controller.GetInputPort("estimated_state"))
    b.Connect(controller.GetOutputPort("force"), plant.GetInputPort("force"))
    b.Connect(plant.GetOutputPort("state"), state_split.get_input_port())
    b.Connect(plant.GetOutputPort("state"), logger.get_input_port())

    # unconnected splitter outputs are fine; export one so it shows up as a diagram output
    b.ExportOutput(state_split.get_output_port(0), "position")

    diagram = b.Build()
    diagram.set_name("example_system")
    return diagram, logger


# =============================================================================
# export
# =============================================================================
def export_svg(diagram, svg_path: Path, max_depth: int | None = None):
    """Write the diagram as Graphviz SVG (needs the `dot` executable)."""
    if shutil.which("dot") is None:
        sys.exit(
            "Graphviz `dot` not found. Install it (e.g. `sudo dnf install graphviz`)."
        )
    dot = diagram.GetGraphvizString(max_depth=max_depth)
    svg = subprocess.run(
        ["dot", "-Tsvg"], input=dot, capture_output=True, text=True, check=True
    ).stdout
    svg_path.write_text(svg)
    print(f"wrote {svg_path}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--out",
        default=str(Path(__file__).with_name("system_diagram.svg")),
        help="SVG file to write (default: system_diagram.svg next to this script)",
    )
    ap.add_argument(
        "--simulate",
        action="store_true",
        help="also simulate for 10 s and publish port values with drake_probe",
    )
    ap.add_argument(
        "--time", type=float, default=10.0, help="simulation time for --simulate"
    )
    args = ap.parse_args()

    diagram, logger = build_diagram()
    export_svg(diagram, Path(args.out))

    if args.simulate:
        here = Path(__file__).resolve().parent
        sys.path[:0] = [
            str(here),
            str(here.parent),
        ]  # find drake_probe.py in this folder or the repo root
        from drake_probe import run_debug

        sim = Simulator(diagram)
        sim.Initialize()
        sim.set_target_realtime_rate(1.0)
        run_debug(sim, diagram, Path(args.out).stem, period=0.5, end_time=args.time)


if __name__ == "__main__":
    main()
