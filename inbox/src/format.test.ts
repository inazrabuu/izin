import { afterEach, expect, it, vi } from "vitest";
import { timeAgo, timeUntil } from "./format";

afterEach(() => vi.useRealTimers());

it("says how long ago", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-04T12:00:00Z"));
  expect(timeAgo("2026-11-04T11:00:00Z")).toBe("1 hour ago");
});

it("says how long until", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-04T12:00:00Z"));
  expect(timeUntil("2026-11-04T12:30:00Z")).toBe("in 30 minutes");
});
