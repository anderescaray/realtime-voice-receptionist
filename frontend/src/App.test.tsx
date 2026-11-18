import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { App } from "./App";

const getUserMedia = vi.fn(async () => {
  const track = { kind: "audio", stop: vi.fn() };
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  };
});

beforeEach(() => {
  window.location.hash = "#/";
  getUserMedia.mockClear();
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  Object.defineProperty(globalThis.navigator, "language", {
    configurable: true,
    value: "es-ES",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

  it("keeps an in-progress call when switching language", async () => {
    window.history.replaceState(null, "", "?lang=es");
    const user = userEvent.setup();
    const close = vi.fn();
    const listeners = new Map<string, EventListener>();
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "connected";
        localDescription = { sdp: "x", type: "offer" };
        addTransceiver = vi.fn();
        createDataChannel = vi.fn(() => ({ addEventListener: vi.fn(), readyState: "open" }));
        setLocalDescription = vi.fn();
        createOffer = vi.fn(async () => ({}));
        setRemoteDescription = vi.fn();
        addEventListener = vi.fn((type: string, fn: EventListener) => {
          listeners.set(type, fn);
        });
        removeEventListener = vi.fn();
        close = close;
      },
    );
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ sdp: "a", type: "answer" })));
    vi.stubGlobal(
      "AudioContext",
      class {
        resume = vi.fn(async () => {});
        close = vi.fn();
        createAnalyser = () => ({ fftSize: 1024, getFloatTimeDomainData: vi.fn() });
        createMediaStreamSource = () => ({ connect: vi.fn() });
      },
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);

    render(<App />);
    await user.click(screen.getByTestId("call-understand"));
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/Conectando|Connecting/),
    );
    listeners.get("connectionstatechange")?.(new Event("connectionstatechange"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/Llamada en curso|Call live/),
    );

    close.mockClear();
    await user.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByTestId("call-status")).toHaveTextContent(/Call live/);
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByTestId("call-understand")).toBeChecked();
  });
});