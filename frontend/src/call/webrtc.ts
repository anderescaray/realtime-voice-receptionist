export type LangCode = "es" | "en";

export type WebRtcDeps = {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  RTCPeerConnection: typeof RTCPeerConnection;
  fetch: typeof fetch;
};

export type CallPhase =
  | "idle"
  | "requesting-mic"
  | "connecting"
  | "live"
  | "ended"
  | "error";

export type StartCallOptions = {
  language: LangCode;
  deps: WebRtcDeps;
  onPhase: (phase: CallPhase, detail?: string) => void;
  /** Fired as soon as the session object exists so the UI can hang up while connecting. */
  onHandle?: (handle: VoiceCallHandle) => void;
  onRemoteStream?: (stream: MediaStream) => void;
};

export type VoiceCallHandle = {
  hangUp: () => void;
  getPeerConnection: () => RTCPeerConnection | null;
  getLocalStream: () => MediaStream | null;
  getRemoteStream: () => MediaStream | null;
};

const DISCONNECT_HANGUP_MS = 6000;

function waitForIceComplete(pc: RTCPeerConnection, maxMs = 2000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      if (pc.iceGatheringState === "complete") {
        finish();
      }
    };
    const finish = () => {
      pc.removeEventListener("icegatheringstatechange", done);
      clearTimeout(timer);
      resolve();
    };
    pc.addEventListener("icegatheringstatechange", done);
    const timer = window.setTimeout(finish, maxMs);
  });
}

export async function startVoiceCall(options: StartCallOptions): Promise<VoiceCallHandle> {
  const { language, deps, onPhase, onHandle, onRemoteStream } = options;
  let mic: MediaStream | null = null;
  let pc: RTCPeerConnection | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let disconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let remoteStream: MediaStream | null = null;
  let closed = false;

  const clearPing = () => {
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  };

  const clearDisconnectTimer = () => {
    if (disconnectTimer !== null) {
      clearTimeout(disconnectTimer);
      disconnectTimer = null;
    }
  };

  const cleanup = () => {
    if (closed) return;
    closed = true;
    clearPing();
    clearDisconnectTimer();
    try {
      pc?.close();
    } catch {
      /* ignore */
    }
    pc = null;
    mic?.getTracks().forEach((track) => track.stop());
    mic = null;
    remoteStream = null;
  };

  const hangUp = () => {
    cleanup();
    onPhase("ended");
  };

  const failConnection = (): VoiceCallHandle => {
    if (closed) return publicHandle;
    const shouldNotify = !closed;
    cleanup();
    if (shouldNotify) onPhase("error", "connection-failed");
    return publicHandle;
  };

  let publicHandle: VoiceCallHandle = {
    hangUp: () => {
      cleanup();
      onPhase("ended");
    },
    getPeerConnection: () => pc,
    getLocalStream: () => mic,
    getRemoteStream: () => remoteStream,
  };
  onHandle?.(publicHandle);

  if (!closed) onPhase("requesting-mic");
  try {
    mic = await deps.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    const shouldNotify = !closed;
    cleanup();
    if (shouldNotify) {
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        onPhase("error", "mic-denied");
      } else {
        onPhase("error", "connection-failed");
      }
    }
    publicHandle = {
      hangUp: () => cleanup(),
      getPeerConnection: () => null,
      getLocalStream: () => null,
      getRemoteStream: () => null,
    };
    onHandle?.(publicHandle);
    return publicHandle;
  }

  if (closed) {
    mic.getTracks().forEach((track) => track.stop());
    mic = null;
    return publicHandle;
  }

  try {
    if (!closed) onPhase("connecting");
    pc = new deps.RTCPeerConnection();
    publicHandle.hangUp = hangUp;

    const audioTrack = mic.getAudioTracks()[0];
    pc.addTransceiver(audioTrack, { direction: "sendrecv" });
    pc.addTransceiver("video", { direction: "sendrecv" });

    const channel = pc.createDataChannel("control");
    channel.addEventListener("open", () => {
      pingTimer = setInterval(() => {
        if (channel.readyState === "open") channel.send("ping");
      }, 1000);
    });

    pc.addEventListener("track", (event) => {
      if (event.track.kind !== "audio") return;
      remoteStream = new MediaStream([event.track]);
      onRemoteStream?.(remoteStream);
    });

    pc.addEventListener("connectionstatechange", () => {
      if (!pc) return;
      const state = pc.connectionState;
      if (state === "connected") {
        clearDisconnectTimer();
        if (!closed) onPhase("live");
      }
      if (state === "failed" || state === "closed") {
        hangUp();
      }
      if (state === "disconnected") {
        clearDisconnectTimer();
        disconnectTimer = setTimeout(() => hangUp(), DISCONNECT_HANGUP_MS);
      }
    });

    await pc.setLocalDescription(await pc.createOffer());
    if (closed) return publicHandle;

    await waitForIceComplete(pc);
    if (closed) return publicHandle;

    let res: Response;
    try {
      res = await deps.fetch("/api/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp: pc.localDescription!.sdp,
          type: pc.localDescription!.type,
          request_data: { language },
        }),
      });
    } catch {
      return failConnection();
    }
    if (closed) return publicHandle;

    if (!res.ok) {
      if (closed) return publicHandle;
      const shouldNotify = !closed;
      cleanup();
      if (shouldNotify) onPhase("error", "server-error");
      return publicHandle;
    }

    const answer = (await res.json()) as { sdp: string; type: RTCSdpType; pc_id?: string };
    if (closed) return publicHandle;

    await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
    if (closed) return publicHandle;

    publicHandle.hangUp = hangUp;
    return publicHandle;
  } catch {
    return failConnection();
  }
}

export function levelsFromStream(
  audioCtx: AudioContext,
  stream: MediaStream,
): () => number {
  const node = audioCtx.createAnalyser();
  node.fftSize = 1024;
  const source = audioCtx.createMediaStreamSource(stream);
  source.connect(node);
  const buf = new Float32Array(node.fftSize);
  return () => {
    node.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const x of buf) sum += x * x;
    return Math.sqrt(sum / buf.length);
  };
}