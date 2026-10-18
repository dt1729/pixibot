"""Pixibot internals.

Everything in this package is **private**: it may change without notice and
nothing outside ``pixibot`` should import it. The public surface is the modules
at the root of ``pixibot`` (DESIGN.md §16; lld-seed's entry-point boundary).

What lives here and why:

* ``agent``, ``context_manager``, ``orchestrator``, ``factory`` - the loop, the
  scheduler, the materializer and the prompt builder. A caller drives these
  through :class:`pixibot.engine.Engine`, never directly.
* ``tpm`` - the planning agent, reached through the engine.
* ``tools`` - the tool bodies an agent is given; the surface that configures them
  is ``Toolset``, which is in ``agent`` here, because an agent is an internal.
* ``standards``, ``console``, ``logbook``, ``completion``, ``mockrun`` -
  prompt text, terminal styling, the run log, shell completion and the offline
  scripted models.
"""
