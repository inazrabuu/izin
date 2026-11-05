import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

it("opens a decision link without signing in", () => {
  localStorage.clear();
  sessionStorage.clear();
  window.location.hash = "#/link/tok.en";
  const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);

  expect(fetchMock).toHaveBeenCalledWith("/v1/link", expect.anything());
});
