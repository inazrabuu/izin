import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Settings } from "./Settings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("enables WhatsApp only with a valid number and consent", async () => {
  const me = {
    name: "Ana",
    roles: [],
    available: true,
    phone: null,
    whatsapp_opted_in_at: null,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(me), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  render(<Settings onSignOut={() => {}} />);
  const button = (await screen.findByRole("button", {
    name: "Turn on WhatsApp",
  })) as HTMLButtonElement;
  expect(button.disabled).toBe(true);

  fireEvent.change(screen.getByLabelText(/Phone number/), {
    target: { value: "+6281234567890" },
  });
  expect(button.disabled).toBe(true); // a valid number alone is not consent

  fireEvent.click(screen.getByRole("checkbox"));
  expect(button.disabled).toBe(false);
});
