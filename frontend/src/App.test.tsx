import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { App } from "./App";

beforeEach(() => {
  window.location.hash = "#/";
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn() },
  });
  Object.defineProperty(globalThis.navigator, "language", {
    configurable: true,
    value: "es-ES",
  });
});

describe("App", () => {
  it("uses ?lang=en for initial copy and html lang", () => {
    window.history.replaceState(null, "", "?lang=en");
    render(<App />);
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Voice reception/i);
  });

  it("updates html lang when switching language", async () => {
    window.history.replaceState(null, "", "?lang=es");
    const user = userEvent.setup();
    render(<App />);
    expect(document.documentElement.lang).toBe("es");
    await user.click(screen.getByRole("button", { name: "EN" }));
    expect(document.documentElement.lang).toBe("en");
  });
});