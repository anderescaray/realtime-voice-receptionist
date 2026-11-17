import { useCallback, useEffect, useRef, useState } from "react";
import type { Lang, MessageKey } from "../i18n";
import { reviewDaysFromEnv, t } from "../i18n";
import {
  levelsFromStream,
  startVoiceCall,
  type CallPhase,
  type VoiceCallHandle,
} from "../call/webrtc";
import type { StudioCardView } from "../studio/cards";

type CallPanelProps = {
  lang: Lang;
  reviewDays?: number;
  studioLink?: boolean;
  activeCard?: StudioCardView | null;
};

function statusKey(phase: CallPhase, detail?: string): MessageKey {
  if (phase === "error") {
    if (detail === "mic-denied") return "callStatusMicDenied";
    if (detail === "server-error") return "callStatusServerError";
    return "callStatusConnectionFailed";
  }
  switch (phase) {
    case "requesting-mic":
      return "callStatusRequestingMic";
    case "connecting":
      return "callStatusConnecting";
    case "live":
      return "callStatusLive";
    case "ended":
      return "callStatusEnded";
    default:
      return "callStatusIdle";
  }
}

export function CallPanel({
  lang,
  reviewDays = reviewDaysFromEnv(),
  studioLink = false,
  activeCard = null,
}: CallPanelProps) {
  const [understood, setUnderstood] = useState(false);
  const [phase, setPhase] = useState<CallPhase>("idle");
  const [statusDetail, setStatusDetail] = useState<string | undefined>();
  const [callerLevel, setCallerLevel] = useState(0);
  const [agentLevel, setAgentLevel] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);

  const callRef = useRef<VoiceCallHandle | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const liveSinceRef = useRef<number | null>(null);

  const hasMedia =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function";

  const stopMeters = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    void audioCtxRef.current?.close();
    audioCtxRef.current = null;
    setCallerLevel(0);
    setAgentLevel(0);
  }, []);

  const startMeters = useCallback(
    (local: MediaStream, remote: MediaStream) => {
      stopMeters();
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      const you = levelsFromStream(ctx, local);
      const agent = levelsFromStream(ctx, remote);
      const tick = () => {
        setCallerLevel(you());
        setAgentLevel(agent());
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
    },
    [stopMeters],
  );

  const hangUp = useCallback(() => {
    callRef.current?.hangUp();
    callRef.current = null;
    stopMeters();
    liveSinceRef.current = null;
  }, [stopMeters]);

  useEffect(() => () => hangUp(), [hangUp]);

  useEffect(() => {
    hangUp();
    setPhase("idle");
    setStatusDetail(undefined);
    setElapsedSec(0);
  }, [activeCard?.id, hangUp]);

  useEffect(() => {
    if (phase !== "live") return;
    const id = window.setInterval(() => {
      if (liveSinceRef.current) {
        setElapsedSec(Math.floor((Date.now() - liveSinceRef.current) / 1000));
      }
    }, 500);
    return () => clearInterval(id);
  }, [phase]);

  const retention = t(lang, "callNoticeRetention").replace("{days}", String(reviewDays));

  const startCall = async () => {
    if (!understood || !hasMedia || phase === "live" || phase === "connecting" || phase === "requesting-mic") {
      return;
    }
    hangUp();
    setElapsedSec(0);
    const handle = await startVoiceCall({
      language: lang,
      deps: {
        getUserMedia: (c) => navigator.mediaDevices.getUserMedia(c),
        RTCPeerConnection: window.RTCPeerConnection,
        fetch: window.fetch.bind(window),
      },
      onHandle: (session) => {
        callRef.current = session;
      },
      onRemoteStream: (stream) => {
        if (audioRef.current) audioRef.current.srcObject = stream;
        const local = callRef.current?.getLocalStream();
        if (local) startMeters(local, stream);
      },
      onPhase: (next, detail) => {
        setPhase(next);
        setStatusDetail(detail);
        if (next === "live") {
          liveSinceRef.current = Date.now();
        }
        if (next === "ended" || next === "error") {
          stopMeters();
          liveSinceRef.current = null;
        }
      },
    });
    callRef.current = handle;
  };

  const onMainButton = () => {
    if (phase === "live" || phase === "connecting" || phase === "requesting-mic") {
      hangUp();
      setPhase("ended");
      return;
    }
    void startCall();
  };

  const inCall = phase === "live" || phase === "connecting" || phase === "requesting-mic";
  const talkEnabled = understood && hasMedia && !inCall;
  const showHangUp = inCall;

  const statusText = t(lang, statusKey(phase, statusDetail));

  return (
    <section className="call-panel" aria-labelledby="call-panel-heading">
      <h2 id="call-panel-heading" className="visually-hidden">
        {t(lang, "callTalk")}
      </h2>

      {activeCard ? (
        <div className="call-panel__role" data-testid="active-role">
          <h3>{activeCard.title}</h3>
          <p>
            <strong>{activeCard.whoLabel}:</strong> {activeCard.who}
          </p>
          <p>
            <strong>{activeCard.goalLabel}:</strong> {activeCard.goal}
          </p>
          <ul>
            {activeCard.hints.map((hint) => (
              <li key={hint}>{hint}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <NoticeList lang={lang} retention={retention} studioLink={studioLink} />

      <label className="call-panel__check">
        <input
          type="checkbox"
          checked={understood}
          onChange={(e) => setUnderstood(e.target.checked)}
          data-testid="call-understand"
        />
        {t(lang, "callUnderstand")}
      </label>

      {!hasMedia ? (
        <p className="call-panel__warn" role="alert">
          {t(lang, "callStatusNoMedia")}
        </p>
      ) : null}

      <div className="call-panel__actions">
        <button
          type="button"
          className={showHangUp ? "btn btn--danger" : "btn btn--primary"}
          disabled={showHangUp ? false : !talkEnabled}
          onClick={onMainButton}
          data-testid="call-main-button"
        >
          {showHangUp ? t(lang, "callHangUp") : t(lang, "callTalk")}
        </button>
      </div>

      <p className="call-panel__status" aria-live="polite" data-testid="call-status">
        {statusText}
      </p>

      {phase === "live" ? (
        <div className="call-panel__meters">
          <div className="meter-row">
            <span>{t(lang, "callSpeakingYou")}</span>
            <meter value={callerLevel} min={0} max={0.35} />
          </div>
          <div className="meter-row">
            <span>{t(lang, "callSpeakingAgent")}</span>
            <meter value={agentLevel} min={0} max={0.35} />
          </div>
          <p className="call-panel__elapsed">
            {t(lang, "callElapsed")}: {elapsedSec}s
          </p>
        </div>
      ) : null}

      <audio ref={audioRef} autoPlay playsInline className="visually-hidden" />
    </section>
  );
}

function NoticeList({
  lang,
  retention,
  studioLink,
}: {
  lang: Lang;
  retention: string;
  studioLink: boolean;
}) {
  const privacyBody = studioLink
    ? t(lang, "callNoticePrivacyLanding")
    : t(lang, "callNoticePrivacy");

  const privacyLine =
    studioLink && privacyBody.includes("{studio}") ? (
      (() => {
        const [before, after] = privacyBody.split("{studio}");
        return (
          <>
            {before}
            <a href="#/studio">{t(lang, "navStudio")}</a>
            {after}
          </>
        );
      })()
    ) : (
      privacyBody
    );

  return (
    <ul className="call-panel__notice" data-testid="call-notice">
      <li>{t(lang, "callNoticeRecorded")}</li>
      <li>{t(lang, "callNoticeAi")}</li>
      <li data-testid="call-retention">{retention}</li>
      <li data-testid="call-notice-privacy">{privacyLine}</li>
      <li>{t(lang, "callNoticeCap")}</li>
    </ul>
  );
}