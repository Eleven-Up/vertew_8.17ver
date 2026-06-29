// Sample test to confirm the vitest + fast-check toolchain runs green.
// This trivial test exists only to validate the test framework setup (Task 1).
// Real fast-check property tests for the Kiosk_UI state machine are added later.

import { describe, it, expect } from "vitest";
import fc from "fast-check";

describe("frontend test setup", () => {
  it("runs vitest", () => {
    expect(1 + 1).toBe(2);
  });

  it("runs fast-check", () => {
    fc.assert(
      fc.property(fc.integer(), (n) => {
        return n + 0 === n;
      }),
      { numRuns: 100 },
    );
  });
});
