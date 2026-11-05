import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { RenderedText } from "./RenderedText";

afterEach(cleanup);

const segments = [
  { text: "Refund Rp ", agent: false },
  { text: "4.500.000", agent: true },
];

it("marks agent-supplied values", () => {
  const { container } = render(
    <RenderedText text="Refund Rp 4.500.000" segments={segments} />,
  );
  expect(container.querySelector("mark")?.textContent).toBe("4.500.000");
});

it("renders agent text as text, never as HTML", () => {
  const evil = [{ text: "<img src=x onerror=alert(1)>", agent: true }];
  const { container } = render(
    <RenderedText text={evil[0].text} segments={evil} />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toBe(evil[0].text);
});

it("falls back to plain text when segments disagree", () => {
  const { container } = render(
    <RenderedText text="Something else" segments={segments} />,
  );
  expect(container.querySelector("mark")).toBeNull();
  expect(container.textContent).toBe("Something else");
});
