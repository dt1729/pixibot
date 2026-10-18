# Testing standards (v0)

- **Every public interface and integration point gets a test.**
- **Test behavior, not implementation.** Assert on observable outputs/contracts.
- **One reason to fail per test.** Clear arrange / act / assert.
- **Cover the error paths**, not just the happy path.
- **Deterministic.** No reliance on wall-clock, network, or ordering unless that
  is the thing under test.
- **Fast.** Unit tests run in-process; mark slow/integration tests separately.
- Coverage is a floor signal, not the goal — a green suite that asserts nothing
  is worse than honest gaps.
