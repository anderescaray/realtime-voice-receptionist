import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { startVoiceCall, type WebRtcDeps } from "./webrtc";

function mockTrack() {
  return { kind: "audio", stop: vi.fn() } as unknown as MediaStreamTrack;
}

function mockStream() {
  return { getAudioTracks: () => [mockTrack()], getTracks: () => [mockTrack()] } as MediaStream;
}

type TransceiverCall = { arg: unknown; opts?: RTCRtpTransceiverInit };

class MockPeerConnection {
  iceGatheringState: RTCIceGatheringState = "complete";
  connectionState: RTCPeerConnectionState = "connected";
  localDescription: RTCSessionDescriptionInit = { sdp: "local", type: "offer" };
  transceivers: TransceiverCall[] = [];
  closed = false;

  addTransceiver = vi.fn((arg: unknown, opts?: RTCRtpTransceiverInit) => {
    this.transceivers.push({ arg, opts });
  });

  createDataChannel = vi.fn((_name?: string, _init?: RTCDataChannelInit) => ({
    readyState: "open",
    send: vi.fn(),
    addEventListener: vi.fn(),
  }));

  setLocalDescription = vi.fn(async (_desc?: RTCLocalSessionDescriptionInit) => {});
  createOffer = vi.fn(async (_options?: RTCOfferOptions) => ({ sdp: "offer", type: "offer" }));
  setRemoteDescription = vi.fn(async (_desc?: RTCSessionDescriptionInit) => {});
  addEventListener = vi.fn((_type?: string, _listener?: EventListener) => {});
  removeEventListener = vi.fn((_type?: string, _listener?: EventListener) => {});

  close = vi.fn(() => {
    this.closed = true;
  });
}

function buildDeps(overrides: Partial<WebRtcDeps> & { pc?: MockPeerConnection } = {}) {
  const instances: MockPeerConnection[] = [];
  class RTCPeerConnection {
    instance: MockPeerConnection;
    constructor() {
      this.instance = overrides.pc ?? new MockPeerConnection();
      instances.push(this.instance);
    }
    addTransceiver(arg: unknown, opts?: RTCRtpTransceiverInit) {
      return this.instance.addTransceiver(arg, opts);
    }
    createDataChannel(name: string, init?: RTCDataChannelInit) {
      return this.instance.createDataChannel(name, init);
    }
    setLocalDescription(desc: RTCLocalSessionDescriptionInit) {
      return this.instance.setLocalDescription(desc);
    }
    createOffer(options?: RTCOfferOptions) {
      return this.instance.createOffer(options);
    }
    setRemoteDescription(desc: RTCSessionDescriptionInit) {
      return this.instance.setRemoteDescription(desc);
    }
    addEventListener(type: string, listener: EventListener) {
      this.instance.addEventListener(type, listener);
    }
    removeEventListener(type: string, listener: EventListener) {
      this.instance.removeEventListener(type, listener);
    }
    close() {
      this.instance.close();
    }
    get iceGatheringState() {
      return this.instance.iceGatheringState;
    }
    get connectionState() {
      return this.instance.connectionState;
    }
    get localDescription() {
      return this.instance.localDescription;
    }
  }

  const getUserMedia = vi.fn(async () => mockStream());
  const fetch = vi.fn(async () =>
    Response.json({ sdp: "answer", type: "answer", pc_id: "pc1" }),
  );

  const deps: WebRtcDeps = {
    getUserMedia: overrides.getUserMedia ?? getUserMedia,
    RTCPeerConnection: RTCPeerConnection as unknown as typeof window.RTCPeerConnection,
    fetch: overrides.fetch ?? fetch,
  };

  return { deps, getUserMedia, fetch, getLatestPc: () => instances.at(-1)!, pcInstances: instances };
}

describe("startVoiceCall", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
      setInterval: globalThis.setInterval,
      clearInterval: globalThis.clearInterval,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("posts offer with request_data.language and no top-level language", async () => {
    const { deps, fetch } = buildDeps();
    await startVoiceCall({
      language: "en",
      deps,
      onPhase: () => {},
    });
    expect(fetch).toHaveBeenCalledOnce();
    const call = fetch.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(call[1].body));
    expect(body.request_data).toEqual({ language: "en" });
    expect(body.language).toBeUndefined();
    expect(body.sdp).toBe("local");
  });

  it("sends Spanish in request_data when language is es", async () => {
    const { deps, fetch } = buildDeps();
    await startVoiceCall({ language: "es", deps, onPhase: () => {} });
    const call = fetch.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(call[1].body));
    expect(body.request_data.language).toBe("es");
  });

  it("adds audio transceiver before video", async () => {
    const { deps, getLatestPc } = buildDeps();
    await startVoiceCall({ language: "es", deps, onPhase: () => {} });
    const pc = getLatestPc();
    expect(pc.transceivers).toHaveLength(2);
    expect(pc.transceivers[0].opts?.direction).toBe("sendrecv");
    expect(pc.transceivers[1].arg).toBe("video");
  });

  it("does not fetch when microphone is denied", async () => {
    const denied = vi.fn(() => Promise.reject(new DOMException("denied", "NotAllowedError")));
    const { deps, fetch } = buildDeps({ getUserMedia: denied });
    const phases: string[] = [];
    await startVoiceCall({
      language: "es",
      deps,
      onPhase: (p, d) => {
        phases.push(`${p}:${d ?? ""}`);
      },
    });
    expect(denied).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
    expect(phases).toContain("error:mic-denied");
  });

  it("closes peer connection and stops tracks when offer returns 500", async () => {
    const trackStop = vi.fn();
    const stream = {
      getAudioTracks: () => [{ kind: "audio", stop: trackStop }],
      getTracks: () => [{ kind: "audio", stop: trackStop }],
    } as unknown as MediaStream;
    const micGetUserMedia = vi.fn(async () => stream);
    const { deps, getLatestPc } = buildDeps({
      getUserMedia: micGetUserMedia,
      fetch: vi.fn(async () => new Response("fail", { status: 500 })),
    });
    const phases: string[] = [];
    await startVoiceCall({ language: "es", deps, onPhase: (p) => phases.push(p) });
    expect(micGetUserMedia).toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalled();
    expect(getLatestPc().closed).toBe(true);
    expect(phases).toContain("error");
  });

  it("hangUp is safe to call twice", async () => {
    const { deps } = buildDeps();
    const handle = await startVoiceCall({ language: "es", deps, onPhase: () => {} });
    handle.hangUp();
    expect(() => handle.hangUp()).not.toThrow();
  });

  it("stops mic and skips peer connection when hung up during getUserMedia", async () => {
    const trackStop = vi.fn();
    const pendingStream = {
      getAudioTracks: () => [{ kind: "audio", stop: trackStop }],
      getTracks: () => [{ kind: "audio", stop: trackStop }],
    } as unknown as MediaStream;
    let resolveMedia: (s: MediaStream) => void = () => {};
    const slowMic = vi.fn(
      () =>
        new Promise<MediaStream>((resolve) => {
          resolveMedia = resolve;
        }),
    );
    const { deps, pcInstances } = buildDeps({ getUserMedia: slowMic });
    const phases: string[] = [];
    let sessionHandle: import("./webrtc").VoiceCallHandle | undefined;
    const startPromise = startVoiceCall({
      language: "es",
      deps,
      onHandle: (h) => {
        sessionHandle = h;
      },
      onPhase: (p) => phases.push(p),
    });
    await vi.waitFor(() => expect(sessionHandle).toBeDefined());
    sessionHandle!.hangUp();
    expect(phases).toContain("ended");
    resolveMedia(pendingStream);
    await startPromise;
    expect(trackStop).toHaveBeenCalled();
    expect(pcInstances).toHaveLength(0);
    expect(phases).not.toContain("error");
  });

  it("does not set remote description when hung up during fetch", async () => {
    const track = mockTrack();
    const stream = {
      getAudioTracks: () => [track],
      getTracks: () => [track],
    } as MediaStream;
    let resolveFetch: (r: Response) => void = () => {};
    const slowFetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const { deps, getLatestPc } = buildDeps({
      getUserMedia: vi.fn(async () => stream),
      fetch: slowFetch,
    });
    let handle: import("./webrtc").VoiceCallHandle | undefined;
    const phases: string[] = [];
    const started = startVoiceCall({
      language: "es",
      deps,
      onHandle: (h) => {
        handle = h;
      },
      onPhase: (p) => phases.push(p),
    });
    await vi.waitFor(() => expect(slowFetch).toHaveBeenCalled());
    handle!.hangUp();
    resolveFetch(Response.json({ sdp: "answer", type: "answer" }));
    await expect(started).resolves.toBeDefined();
    expect(getLatestPc().setRemoteDescription).not.toHaveBeenCalled();
    expect(phases).not.toContain("error");
  });

  it("removes ice listener and clears timeout when gathering completes", async () => {
    const slowPc = new MockPeerConnection();
    slowPc.iceGatheringState = "gathering";
    const { deps, getLatestPc } = buildDeps({ pc: slowPc });
    await startVoiceCall({ language: "es", deps, onPhase: () => {} });
    const pc = getLatestPc();
    slowPc.iceGatheringState = "complete";
    const iceCalls = pc.addEventListener.mock.calls.filter((c) => c[0] === "icegatheringstatechange");
    expect(iceCalls.length).toBeGreaterThan(0);
    const handler = iceCalls[0]![1] as () => void;
    handler();
    expect(pc.removeEventListener).toHaveBeenCalledWith("icegatheringstatechange", handler);
  });

  it("waits 6s before hang-up on disconnected and cancels if connected returns", async () => {
    vi.useFakeTimers();
    const slowPc = new MockPeerConnection();
    slowPc.connectionState = "connecting";
  const connectionListeners: Array<(ev: Event) => void> = [];
    slowPc.addEventListener = vi.fn((type?: string, listener?: EventListener) => {
      if (type === "connectionstatechange" && listener) {
        connectionListeners.push(listener as (ev: Event) => void);
      }
    });
    const { deps, getLatestPc } = buildDeps({ pc: slowPc });
    const phases: string[] = [];
    const started = startVoiceCall({ language: "es", deps, onPhase: (p) => phases.push(p) });
    await vi.waitFor(() => expect(getLatestPc().addTransceiver).toHaveBeenCalled());
    await started;

    slowPc.connectionState = "disconnected";
    connectionListeners.forEach((fn) => fn(new Event("connectionstatechange")));
    vi.advanceTimersByTime(5000);
    expect(phases).not.toContain("ended");

    slowPc.connectionState = "connected";
    connectionListeners.forEach((fn) => fn(new Event("connectionstatechange")));
    vi.advanceTimersByTime(6000);
    expect(phases).not.toContain("ended");

    slowPc.connectionState = "disconnected";
    connectionListeners.forEach((fn) => fn(new Event("connectionstatechange")));
    vi.advanceTimersByTime(6000);
    expect(phases).toContain("ended");
    vi.useRealTimers();
  });

  it("reports connection-failed and cleans up when createOffer rejects", async () => {
    const trackStop = vi.fn();
    const stream = {
      getAudioTracks: () => [{ kind: "audio", stop: trackStop }],
      getTracks: () => [{ kind: "audio", stop: trackStop }],
    } as unknown as MediaStream;
    const badPc = new MockPeerConnection();
    badPc.createOffer = vi.fn(async () => {
      throw new Error("offer failed");
    });
    const { deps, getLatestPc } = buildDeps({
      getUserMedia: vi.fn(async () => stream),
      pc: badPc,
    });
    const phases: string[] = [];
    await startVoiceCall({
      language: "es",
      deps,
      onPhase: (p, d) => phases.push(`${p}:${d ?? ""}`),
    });
    expect(phases).toContain("error:connection-failed");
    expect(trackStop).toHaveBeenCalled();
    expect(getLatestPc().closed).toBe(true);
  });
});