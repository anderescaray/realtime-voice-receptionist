import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { CallPanel } from "./CallPanel";

const trackStop = vi.fn();
const getUserMedia = vi.fn<() => Promise<MediaStream>>(async () => {
  const track = { kind: "audio", stop: trackStop };
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
});

beforeEach(() => {
  trackStop.mockClear();
  getUserMedia.mockClear();
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CallPanel", () => {
  it("does not call getUserMedia on render, checkbox tick, or before enabled click", async () => {
    const user = userEvent.setup();
    render(<CallPanel lang="es" reviewDays={7} />);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.getByTestId("call-main-button")).toBeDisabled();

    await user.click(screen.getByTestId("call-understand"));
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.getByTestId("call-main-button")).toBeEnabled();
  });

  it("disables talk until checkbox is ticked, then calls getUserMedia once", async () => {
    const user = userEvent.setup();
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
        addEventListener = vi.fn();
        close = vi.fn();
      },
    );
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ sdp: "a", type: "answer" })));
    vi.stubGlobal(
      "AudioContext",
      class {
        close = vi.fn();
        createAnalyser = () => ({ fftSize: 1024, getFloatTimeDomainData: vi.fn() });
        createMediaStreamSource = () => ({ connect: vi.fn() });
      },
    );

    render(<CallPanel lang="es" reviewDays={7} />);
    const button = screen.getByTestId("call-main-button");
    expect(button).toBeDisabled();

    await user.click(screen.getByTestId("call-understand"));
    expect(button).toBeEnabled();

    await user.click(button);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("renders legal notice with retention days in both languages", () => {
    const { rerender } = render(<CallPanel lang="es" reviewDays={7} studioLink />);
    expect(screen.getByTestId("call-notice")).toBeInTheDocument();
    expect(screen.getByTestId("call-retention")).toHaveTextContent("7");

    rerender(<CallPanel lang="en" reviewDays={14} studioLink />);
    expect(screen.getByTestId("call-retention")).toHaveTextContent("14");
    expect(screen.getByText(/AI assistant/i)).toBeInTheDocument();
  });

  it("uses singular retention copy when reviewDays is 1", () => {
    const { rerender } = render(<CallPanel lang="es" reviewDays={1} />);
    expect(screen.getByTestId("call-retention")).toHaveTextContent(/1 día/);
    expect(screen.getByTestId("call-retention")).not.toHaveTextContent(/1 días/);

    rerender(<CallPanel lang="en" reviewDays={1} />);
    expect(screen.getByTestId("call-retention")).toHaveTextContent(/1 day/);
    expect(screen.getByTestId("call-retention")).not.toHaveTextContent(/1 days/);
  });

  it("shows message when mediaDevices is missing", () => {
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: undefined,
    });
    render(<CallPanel lang="en" reviewDays={7} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/microphone/i);
  });

  it("cleans up on unmount during connecting", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "connecting";
        localDescription = { sdp: "x", type: "offer" };
        addTransceiver = vi.fn();
        createDataChannel = vi.fn(() => ({ addEventListener: vi.fn(), readyState: "open" }));
        setLocalDescription = vi.fn();
        createOffer = vi.fn(async () => ({}));
        setRemoteDescription = vi.fn();
        addEventListener = vi.fn();
        close = close;
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    vi.stubGlobal(
      "AudioContext",
      class {
        close = vi.fn();
        createAnalyser = () => ({ fftSize: 1024, getFloatTimeDomainData: vi.fn() });
        createMediaStreamSource = () => ({ connect: vi.fn() });
      },
    );

    const { unmount } = render(<CallPanel lang="es" reviewDays={7} />);
    await user.click(screen.getByTestId("call-understand"));
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/Conectando|Connecting/),
    );
    unmount();
    expect(close).toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalled();
  });

  it("enables talk after connection error when checkbox is ticked", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "connecting";
        localDescription = { sdp: "x", type: "offer" };
        addTransceiver = vi.fn();
        createDataChannel = vi.fn(() => ({ addEventListener: vi.fn(), readyState: "open" }));
        setLocalDescription = vi.fn();
        createOffer = vi.fn(async () => {
          throw new Error("offer failed");
        });
        setRemoteDescription = vi.fn();
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        close = vi.fn();
      },
    );
    vi.stubGlobal("fetch", vi.fn());
    vi.stubGlobal(
      "AudioContext",
      class {
        close = vi.fn();
        createAnalyser = () => ({ fftSize: 1024, getFloatTimeDomainData: vi.fn() });
        createMediaStreamSource = () => ({ connect: vi.fn() });
      },
    );

    render(<CallPanel lang="es" reviewDays={7} />);
    await user.click(screen.getByTestId("call-understand"));
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/No se pudo establecer/),
    );
    expect(screen.getByTestId("call-main-button")).toBeEnabled();
  });

  it("ignores a stale getUserMedia completion after hang-up and a new start", async () => {
    const user = userEvent.setup();
    const trackStopFirst = vi.fn();
    const trackStopSecond = vi.fn();
    let resolveFirst: (s: MediaStream) => void = () => {};
    const firstPromise = new Promise<MediaStream>((resolve) => {
      resolveFirst = resolve;
    });
    let callIndex = 0;
    getUserMedia.mockImplementation(async () => {
      callIndex += 1;
      if (callIndex === 1) return firstPromise;
      const track = { kind: "audio", stop: trackStopSecond };
      return {
        getAudioTracks: () => [track],
        getTracks: () => [track],
      } as unknown as MediaStream;
    });

    const pcInstances: Array<{
      close: ReturnType<typeof vi.fn>;
      listeners: Map<string, EventListener>;
    }> = [];
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "connected";
        localDescription = { sdp: "x", type: "offer" };
        close = vi.fn();
        listeners = new Map<string, EventListener>();
        addTransceiver = vi.fn();
        createDataChannel = vi.fn(() => ({ addEventListener: vi.fn(), readyState: "open" }));
        setLocalDescription = vi.fn();
        createOffer = vi.fn(async () => ({}));
        setRemoteDescription = vi.fn();
        removeEventListener = vi.fn();
        addEventListener = vi.fn((type: string, fn: EventListener) => {
          this.listeners.set(type, fn);
        });
        constructor() {
          pcInstances.push({ close: this.close, listeners: this.listeners });
        }
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

    HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);

    render(<CallPanel lang="es" reviewDays={7} />);
    await user.click(screen.getByTestId("call-understand"));
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));

    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/finalizada|ended/i),
    );
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(pcInstances.length).toBe(1));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/Conectando|Connecting/),
    );

    const livePc = pcInstances[0]!;
    livePc.listeners.get("connectionstatechange")?.(new Event("connectionstatechange"));
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/Llamada en curso|Call live/),
    );

    const firstTrack = { kind: "audio", stop: trackStopFirst };
    resolveFirst({
      getAudioTracks: () => [firstTrack],
      getTracks: () => [firstTrack],
    } as unknown as MediaStream);
    await waitFor(() => expect(trackStopFirst).toHaveBeenCalled());

    await user.click(screen.getByTestId("call-main-button"));
    expect(livePc.close).toHaveBeenCalled();
    expect(trackStopSecond).toHaveBeenCalled();
    expect(trackStopFirst).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("call-status")).toHaveTextContent(/finalizada|ended/i),
    );
  });

  it("shows tap-to-hear when remote audio play() rejects", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "MediaStream",
      class {
        constructor(_tracks: unknown[]) {}
      },
    );
    let onTrack: ((ev: RTCTrackEvent) => void) | undefined;
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
          if (type === "track") onTrack = fn as (ev: RTCTrackEvent) => void;
        });
        removeEventListener = vi.fn();
        close = vi.fn();
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

    vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(
      new Error("gesture required"),
    );

    render(<CallPanel lang="en" reviewDays={7} />);
    await user.click(screen.getByTestId("call-understand"));
    await user.click(screen.getByTestId("call-main-button"));
    await waitFor(() => expect(onTrack).toBeDefined());
    onTrack!({ track: { kind: "audio" } } as RTCTrackEvent);
    expect(await screen.findByTestId("call-tap-to-hear")).toHaveTextContent(/Tap to hear/i);
  });
});