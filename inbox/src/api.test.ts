import { afterEach, expect, it, vi } from "vitest";
import { linkClient } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("link decisions return the updated request", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: "r1", status: "APPROVED" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const result = await linkClient("tok.en").decide("ignored", "approve");

  expect(result).toMatchObject({ id: "r1" });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/v1/link/decision");
  expect(new Headers(init.headers).get("Izin-Link")).toBe("tok.en");
});
