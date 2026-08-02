# Low-level design standards (v0)

- **Interfaces before implementations.** State the contract — inputs, outputs,
  errors, invariants — before writing the body.
- **One module = one responsibility.** Name it after what it owns.
- **Make illegal states unrepresentable** where the language allows.
- **Explicit data models.** Define the shapes that cross a boundary; don't pass
  untyped dicts between modules.
- **Handshakes are contracts.** When two features meet, write the interface to
  the blackboard before either side codes against it.
- **Design for testability** — dependencies injected, side effects at the edges.
