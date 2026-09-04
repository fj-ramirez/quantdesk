import { describe, expect, it } from "vitest";

import {
  formatCount,
  formatDistance,
  formatDistancePct,
  formatGex,
  formatIv,
  formatPrice,
  formatStrike,
} from "./format";

describe("formatGex", () => {
  it("scales to T/B/M/K", () => {
    expect(formatGex(5.23e10)).toBe("$52.3B");
    expect(formatGex(4.5e8)).toBe("$450.0M");
    expect(formatGex(1.2e12)).toBe("$1.2T");
    expect(formatGex(7500)).toBe("$7.5K");
    expect(formatGex(250)).toBe("$250");
  });

  it("keeps the sign outside the dollar symbol", () => {
    expect(formatGex(-4.5e8)).toBe("-$450.0M");
  });

  it("renders a dash for null, undefined and non-finite input", () => {
    // flip_point is legitimately null when the profile has no sign change.
    expect(formatGex(null)).toBe("—");
    expect(formatGex(undefined)).toBe("—");
    expect(formatGex(NaN)).toBe("—");
    expect(formatGex(Infinity)).toBe("—");
  });

  it("distinguishes a genuine zero from unknown", () => {
    expect(formatGex(0)).toBe("$0");
    expect(formatGex(null)).toBe("—");
  });
});

describe("formatStrike / formatPrice", () => {
  it("adds thousands separators", () => {
    expect(formatStrike(7710)).toBe("7,710");
    expect(formatStrike(770.5)).toBe("770.5");
    expect(formatPrice(7710.17)).toBe("7,710.17");
  });

  it("dashes on null", () => {
    expect(formatStrike(null)).toBe("—");
    expect(formatPrice(null)).toBe("—");
  });
});

describe("distance helpers", () => {
  it("signs the offset from spot", () => {
    expect(formatDistance(7750, 7710.17)).toBe("+39.83");
    expect(formatDistance(7700, 7710.17)).toBe("-10.17");
    expect(formatDistancePct(7750, 7710.17)).toBe("+0.52%");
  });

  it("dashes on a null level rather than rendering NaN", () => {
    expect(formatDistance(null, 7710.17)).toBe("—");
    expect(formatDistancePct(null, 7710.17)).toBe("—");
  });
});

describe("formatIv", () => {
  it("treats input as a decimal fraction, never a percent", () => {
    expect(formatIv(0.1061)).toBe("10.61%");
    // Deep-ITM inversion artifacts are real in this data and must not be clamped.
    expect(formatIv(7.97)).toBe("797.00%");
  });
});

describe("formatCount", () => {
  it("preserves 0 versus unknown", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(null)).toBe("—");
    expect(formatCount(12345)).toBe("12,345");
  });
});
