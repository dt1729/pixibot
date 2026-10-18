"""Pixibot - a dynamic multi-agent software-development system.

The **public surface is this package's root**: the modules you see beside this
file. ``pixibot.lib`` is internal and may change without notice (lld-seed's
entry-point boundary: depth, not designation).

What to reach for:

* :class:`pixibot.engine.Engine` - one steerable run: build, tell, revise, gate.
* :func:`pixibot.run.run_pipeline` - the one-shot form, no interaction.
* :mod:`pixibot.providers` - which models a run uses (``get``, ``detect``).
* :mod:`pixibot.cli` / :mod:`pixibot.tui` - the shell and the split-pane view.
* :mod:`pixibot.benchmark` - compare the topology arms.
* :class:`pixibot.blackboard.Blackboard`, :class:`pixibot.workspace.Workspace` -
  the event log and the project directory a run works in.
* :mod:`pixibot.gates`, :mod:`pixibot.checkpoints` - the mechanical verdict and
  the human one.

Nothing is re-exported here on purpose: an import says which module it came
from, and this file stays a place to read rather than a list to maintain.
"""

__version__ = "0.1.0"
