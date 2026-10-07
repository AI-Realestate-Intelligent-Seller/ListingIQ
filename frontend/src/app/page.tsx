"use client";
import { useEffect, useRef, useState } from "react";
import type { Call, INotification, TelnyxRTC, } from "@telnyx/webrtc";
import { API_URL, apiRequest } from "@/lib/api";
type Connection = "disconnected" | "connecting" | "ready";
const message = (error: unknown) => error instanceof Error
    ? error.message
    : "Something went wrong";
async function backendStatus() {
    try {
        const [info, health] = await Promise.all([
            apiRequest<{
                message: string;
            }>("/"),
            apiRequest<{
                status: string;
                missing_configuration?: string[];
            }>("/health"),
        ]);
        return {
            status: health.status === "ok" && info.message
                ? "Backend online"
                : "Backend unavailable",
            missing: health.missing_configuration ?? [],
        };
    }
    catch {
        return {
            status: "Backend unavailable",
            missing: [] as string[],
        };
    }
}
export default function Home() {
    const [phoneNumber, setPhoneNumber] = useState("");
    const [connection, setConnection] = useState<Connection>("disconnected");
    const [hasClient, setHasClient] = useState(false);
    const [callerNumber, setCallerNumber] = useState("");
    const [health, setHealth] = useState("Checking backend...");
    const [missingConfiguration, setMissingConfiguration,] = useState<string[]>([]);
    const [status, setStatus] = useState("Connect to Telnyx to get started");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [inCall, setInCall] = useState(false);
    const [muted, setMuted] = useState(false);
    const [incomingCall, setIncomingCall] = useState(false);
    const [incomingCaller, setIncomingCaller,] = useState("");
    /*
     * -------------------------------------------------------
     * INBOUND DIAGNOSTIC
     * -------------------------------------------------------
     */
    const [inboundDebug, setInboundDebug] = useState("No inbound call detected yet");
    const [lastInboundEvent, setLastInboundEvent,] = useState("");
    const clientRef = useRef<TelnyxRTC | null>(null);
    const callRef = useRef<Call | null>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const operationRef = useRef(false);
    const mountedRef = useRef(false);
    function releaseMicrophone() {
        streamRef.current
            ?.getTracks()
            .forEach((track) => track.stop());
        streamRef.current = null;
    }
    async function checkBackend() {
        setHealth("Checking backend...");
        const result = await backendStatus();
        if (mountedRef.current) {
            setHealth(result.status);
            setMissingConfiguration(result.missing);
        }
    }
    useEffect(() => {
        mountedRef.current = true;
        void backendStatus().then((result) => {
            if (mountedRef.current) {
                setHealth(result.status);
                setMissingConfiguration(result.missing);
            }
        });
        return () => {
            mountedRef.current = false;
            const client = clientRef.current;
            clientRef.current = null;
            if (callRef.current) {
                void callRef.current
                    .hangup()
                    .catch(() => { });
            }
            releaseMicrophone();
            if (client) {
                client.off("telnyx.ready");
                client.off("telnyx.error");
                client.off("telnyx.notification");
                client.off("telnyx.socket.close");
                void client
                    .disconnect()
                    .catch(() => { });
            }
        };
    }, []);
    async function disconnect() {
        if (operationRef.current) {
            return;
        }
        operationRef.current = true;
        setBusy(true);
        setError("");
        const client = clientRef.current;
        clientRef.current = null;
        try {
            try {
                await callRef.current?.hangup();
            }
            finally {
                await client?.disconnect();
            }
            setStatus("Disconnected");
            setInboundDebug("Disconnected from Telnyx");
        }
        catch (err) {
            setError(message(err));
        }
        finally {
            client?.off("telnyx.ready");
            client?.off("telnyx.error");
            client?.off("telnyx.notification");
            client?.off("telnyx.socket.close");
            callRef.current = null;
            releaseMicrophone();
            setConnection("disconnected");
            setHasClient(false);
            setInCall(false);
            setIncomingCall(false);
            setIncomingCaller("");
            setMuted(false);
            setBusy(false);
            operationRef.current = false;
        }
    }
    async function answerCall() {
        const call = callRef.current;
        console.log("ANSWERING INBOUND CALL:", call);
        if (operationRef.current ||
            !call ||
            call.direction !== "inbound" ||
            call.state !== "ringing") {
            return;
        }
        operationRef.current = true;
        setBusy(true);
        setError("");
        try {
            setStatus("Requesting microphone...");
            if (!navigator.mediaDevices
                ?.getUserMedia) {
                throw new Error("Microphone access requires localhost or HTTPS");
            }
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: true,
            });
            if (!mountedRef.current ||
                callRef.current?.id !==
                    call.id ||
                call.state !== "ringing") {
                stream
                    .getTracks()
                    .forEach((track) => track.stop());
                return;
            }
            streamRef.current = stream;
            call.options.localStream =
                stream;
            call.options.audio = true;
            call.options.video = false;
            setIncomingCall(false);
            setStatus("Answering...");
            setInboundDebug("Inbound call detected - answering...");
            console.log("CALLING call.answer()");
            await call.answer({
                remoteElement: "remoteAudio",
                video: false,
            });
            console.log("call.answer() completed");
        }
        catch (err) {
            console.error("FAILED TO ANSWER CALL:", err);
            releaseMicrophone();
            if (mountedRef.current &&
                callRef.current?.id ===
                    call.id) {
                setIncomingCall(call.state === "ringing");
                setError(message(err));
                setStatus(call.state === "ringing"
                    ? "Incoming call - could not answer"
                    : "Call failed");
                setInboundDebug(`Inbound answer failed: ${message(err)}`);
            }
        }
        finally {
            operationRef.current = false;
            if (mountedRef.current) {
                setBusy(false);
            }
        }
    }
    async function rejectCall() {
        if (callRef.current?.direction !==
            "inbound" ||
            callRef.current.state !==
                "ringing") {
            return;
        }
        console.log("REJECTING INBOUND CALL:", callRef.current);
        setInboundDebug("Inbound call rejected");
        await hangup();
    }
    async function connect() {
        if (operationRef.current ||
            clientRef.current) {
            return;
        }
        operationRef.current = true;
        setBusy(true);
        setError("");
        setConnection("connecting");
        setStatus("Getting a secure calling token...");
        setInboundDebug("Connecting to Telnyx...");
        setLastInboundEvent("");
        let client: TelnyxRTC | null = null;
        try {
            const data = await apiRequest<{
                token: string;
                caller_number: string;
            }>("/webrtc/token", {});
            if (!data.token ||
                !/^\+[1-9]\d{1,14}$/.test(data.caller_number)) {
                throw new Error("The backend must return a token and a valid caller_number");
            }
            setCallerNumber(data.caller_number);
            const { TelnyxRTC } = await import("@telnyx/webrtc");
            if (!mountedRef.current) {
                return;
            }
            // One JWT-authenticated TelnyxRTC client handles BOTH outbound and inbound calls.
            // Keep the long-lived SIP username/password off the frontend.
            client = new TelnyxRTC({
                login_token: data.token,
                hangupOnBeforeUnload: true,
            });
            const activeClient = client;
            clientRef.current =
                client;
            setHasClient(true);
            setStatus("Connecting to Telnyx...");
            /*
             * =====================================================
             * TELNYX NOTIFICATION LISTENER
             * =====================================================
             */
            client.on("telnyx.notification", (event: INotification) => {
                console.log("================================");
                console.log("TELNYX NOTIFICATION:", event);
                console.log("================================");
                /*
                 * This proves we got SOMETHING
                 * from Telnyx.
                 */
                setInboundDebug(`Telnyx notification received: ${event.type}`);
                if (clientRef.current !==
                    activeClient) {
                    console.log("Ignoring notification from old Telnyx client");
                    return;
                }
                if (event.type !==
                    "callUpdate") {
                    console.log("TELNYX EVENT TYPE:", event.type);
                    return;
                }
                if (!event.call) {
                    console.log("Call update received without call object");
                    setInboundDebug("callUpdate received but no call object");
                    return;
                }
                const call = event.call;
                console.log("TELNYX CALL OBJECT:", call);
                console.log("CALL ID:", call.id);
                console.log("CALL DIRECTION:", call.direction);
                console.log("CALL STATE:", call.state);
                console.log("CALL OPTIONS:", call.options);
                console.log("REMOTE CALLER NUMBER:", call.options
                    .remoteCallerNumber);
                console.log("REMOTE CALLER NAME:", call.options
                    .remoteCallerName);
                /*
                 * Visible diagnostic
                 */
                setInboundDebug(`Call detected | direction=${call.direction} | state=${call.state}`);
                setLastInboundEvent(JSON.stringify({
                    id: call.id,
                    direction: call.direction,
                    state: call.state,
                    remoteCallerNumber: call.options
                        .remoteCallerNumber,
                    remoteCallerName: call.options
                        .remoteCallerName,
                }, null, 2));
                const ended = [
                    "hangup",
                    "destroy",
                    "purge",
                ].includes(call.state);
                /*
                 * Another call already exists.
                 */
                if (callRef.current &&
                    callRef.current.id !==
                        call.id) {
                    console.log("Another call is already active");
                    if (call.direction ===
                        "inbound" &&
                        call.state ===
                            "ringing") {
                        console.log("Rejecting second inbound call with 486 Busy");
                        void call
                            .hangup({
                            cause: "USER_BUSY",
                            causeCode: 17,
                            sipCode: 486,
                        })
                            .catch((err) => {
                            console.error("Failed to reject second call:", err);
                        });
                    }
                    return;
                }
                /*
                 * CALL ENDED
                 */
                if (ended) {
                    console.log("TELNYX CALL ENDED:", call);
                    if (callRef.current
                        ?.id !== call.id) {
                        return;
                    }
                    callRef.current =
                        null;
                    releaseMicrophone();
                    setInCall(false);
                    setIncomingCall(false);
                    setIncomingCaller("");
                    setMuted(false);
                    setStatus("Call ended");
                    if (call.direction ===
                        "inbound") {
                        setInboundDebug(`Inbound call ended | state=${call.state}`);
                    }
                    return;
                }
                /*
                 * Store active call.
                 */
                callRef.current =
                    call;
                /*
                 * =================================================
                 * INBOUND CALL
                 * =================================================
                 */
                if (call.direction ===
                    "inbound") {
                    console.log("================================");
                    console.log("***** INBOUND CALL RECEIVED *****");
                    console.log("Inbound call ID:", call.id);
                    console.log("Inbound state:", call.state);
                    console.log("Inbound caller:", call.options
                        .remoteCallerNumber ||
                        call.options
                            .remoteCallerName ||
                        "Unknown caller");
                    console.log("================================");
                    setInboundDebug(`INBOUND DETECTED | state=${call.state}`);
                    setInCall(true);
                    setIncomingCaller(call.options
                        .remoteCallerNumber ||
                        call.options
                            .remoteCallerName ||
                        "Unknown caller");
                    setIncomingCall(call.state ===
                        "ringing");
                    setStatus(call.state ===
                        "ringing"
                        ? "Incoming call..."
                        : call.state ===
                            "active"
                            ? "Connected"
                            : "Answering...");
                    return;
                }
                /*
                 * =================================================
                 * OUTBOUND CALL
                 * =================================================
                 */
                console.log("OUTBOUND CALL UPDATE:", call.state);
                setInCall(true);
                setStatus(call.state ===
                    "active"
                    ? "Connected"
                    : [
                        "ringing",
                        "early",
                    ].includes(call.state)
                        ? "Ringing..."
                        : "Calling...");
            });
            /*
             * WebSocket close
             */
            client.on("telnyx.socket.close", () => {
                console.warn("TELNYX SOCKET CLOSED");
                if (clientRef.current !==
                    activeClient) {
                    return;
                }
                setConnection("connecting");
                setStatus("Connection lost. Reconnecting...");
                setInboundDebug("Telnyx socket disconnected");
            });
            await new Promise<void>((resolve, reject) => {
                const timer = setTimeout(() => reject(new Error("Telnyx connection timed out. Check your WebRTC credentials.")), 20000);
                activeClient.on("telnyx.ready", () => {
                    clearTimeout(timer);
                    if (clientRef.current !==
                        activeClient) {
                        return;
                    }
                    console.log("TELNYX WEBRTC READY");
                    console.log("READY FOR INBOUND + OUTBOUND CALLS");
                    setConnection("ready");
                    setStatus("Ready for inbound and outbound calls");
                    setInboundDebug("Telnyx WebRTC connected with JWT. Ready for inbound and outbound calls...");
                    resolve();
                });
                activeClient.on("telnyx.error", (telnyxError: unknown) => {
                    clearTimeout(timer);
                    console.error("TELNYX SDK ERROR:", telnyxError);
                    setInboundDebug(`Telnyx SDK error: ${message(telnyxError)}`);
                    if (clientRef.current !==
                        activeClient) {
                        return;
                    }
                    setError(telnyxError instanceof
                        Error
                        ? telnyxError.message
                        : "Telnyx connection error. Check your SIP connection and WebRTC credential.");
                    setConnection("disconnected");
                    setStatus("Connection failed");
                    reject(new Error("Could not connect to Telnyx"));
                });
                void activeClient
                    .connect()
                    .catch((err: unknown) => {
                    clearTimeout(timer);
                    console.error("TELNYX CONNECT FAILED:", err);
                    reject(err);
                });
            });
        }
        catch (err) {
            console.error("WEBRTC CONNECTION ERROR:", err);
            if (clientRef.current ===
                client) {
                clientRef.current =
                    null;
            }
            client?.off("telnyx.ready");
            client?.off("telnyx.error");
            client?.off("telnyx.notification");
            client?.off("telnyx.socket.close");
            await client
                ?.disconnect()
                .catch(() => { });
            if (mountedRef.current) {
                setConnection("disconnected");
                setHasClient(false);
                setStatus("Connection failed");
                setError(message(err));
                setInboundDebug(`Connection failed: ${message(err)}`);
            }
        }
        finally {
            operationRef.current =
                false;
            if (mountedRef.current) {
                setBusy(false);
            }
        }
    }
    async function makeCall() {
        if (operationRef.current ||
            callRef.current) {
            return;
        }
        setError("");
        const number = phoneNumber.trim();
        if (!/^\+[1-9]\d{1,14}$/.test(number)) {
            setError("Enter number in E.164 format");
            return;
        }
        if (connection !== "ready") {
            setError("Connect to Telnyx first");
            return;
        }
        operationRef.current =
            true;
        setBusy(true);
        try {
                setStatus("Requesting microphone...");
                if (!navigator
                    .mediaDevices
                    ?.getUserMedia) {
                    throw new Error("Microphone access requires localhost or HTTPS");
                }
                const stream = await navigator.mediaDevices.getUserMedia({
                    audio: true,
                });
                if (!mountedRef.current ||
                    !clientRef.current) {
                    stream
                        .getTracks()
                        .forEach((track) => track.stop());
                    return;
                }
                streamRef.current =
                    stream;
                console.log("STARTING OUTBOUND WEBRTC CALL");
                console.log("Destination:", number);
                console.log("Caller number:", callerNumber);
                callRef.current =
                    clientRef.current.newCall({
                        destinationNumber: number,
                        callerNumber,
                        localStream: stream,
                        audio: true,
                        video: false,
                        remoteElement: "remoteAudio",
                    });
                console.log("OUTBOUND CALL OBJECT:", callRef.current);
                setInCall(true);
                setStatus("Calling...");
        }
        catch (err) {
            console.error("CALL FAILED:", err);
            releaseMicrophone();
            setStatus("Call failed");
            setError(message(err));
        }
        finally {
            operationRef.current =
                false;
            setBusy(false);
        }
    }
    async function hangup() {
        if (operationRef.current ||
            !callRef.current) {
            return;
        }
        operationRef.current =
            true;
        setBusy(true);
        setError("");
        try {
            console.log("HANGING UP CALL:", callRef.current);
            await callRef.current.hangup();
            callRef.current =
                null;
            releaseMicrophone();
            setInCall(false);
            setIncomingCall(false);
            setIncomingCaller("");
            setMuted(false);
            setStatus("Call ended");
        }
        catch (err) {
            console.error("HANGUP FAILED:", err);
            setError(message(err));
        }
        finally {
            operationRef.current =
                false;
            setBusy(false);
        }
    }
    function toggleMute() {
        if (!callRef.current) {
            return;
        }
        if (muted) {
            callRef.current.unmuteAudio();
        }
        else {
            callRef.current.muteAudio();
        }
        setMuted(!muted);
    }
    return (
      <main className="workspace">
        <header className="topbar">
          <div className="brand"><span className="brand-mark">T</span>Telnyx <span className="muted">/ Dialer</span></div>
          <span className="badge"><span className={'dot ' + (health === "Backend online" ? "online" : "")}/>{health}</span>
        </header>
        <section className="intro">
          <p className="eyebrow">Browser calling</p>
          <h1>Let&apos;s make a call.</h1>
          <p className="muted">Dial a number and talk directly from your browser.</p>
        </section>
        <div className="columns">
          <section className="card" aria-labelledby="call-heading">
            <div className="card-header"><h2 id="call-heading">Phone dialer</h2><span className="eyebrow">Voice</span></div>
            <div className="connection">
              <div>
                <strong>{connection === "ready" ? "Telnyx connected" : connection === "connecting" ? "Connecting..." : "Telnyx disconnected"}</strong>
                <span className="muted">{connection === "ready" ? callerNumber : "Connect before making a call"}</span>
              </div>
              <button className="button" type="button" disabled={busy} onClick={hasClient ? disconnect : connect}>{hasClient ? "Disconnect" : "Connect"}</button>
            </div>
            {incomingCall && (
              <div className="connection" role="status" aria-live="polite">
                <div><strong>Incoming call</strong><span className="muted">{incomingCaller}</span></div>
                <div className="actions">
                  <button type="button" className="button primary" disabled={busy} onClick={answerCall}>Answer</button>
                  <button type="button" className="button danger" disabled={busy} onClick={rejectCall}>Reject</button>
                </div>
              </div>
            )}
            <form onSubmit={(event) => { event.preventDefault(); void makeCall(); }}>
              <label htmlFor="phone">Phone number</label>
              <input id="phone" className="phone-input" type="tel" autoComplete="tel" placeholder="+13125551234" value={phoneNumber} disabled={busy || inCall} onChange={(event) => setPhoneNumber(event.target.value)} aria-describedby="phone-help"/>
              <p id="phone-help" className="help">Include + and the country code. Example: +13125551234</p>
              <div className="dialer">
                <div className="dialpad" role="group" aria-label="Phone keypad">
                  {[["1", ""], ["2", "ABC"], ["3", "DEF"], ["4", "GHI"], ["5", "JKL"], ["6", "MNO"], ["7", "PQRS"], ["8", "TUV"], ["9", "WXYZ"], ["*", ""], ["0", ""], ["#", ""]].map(([digit, letters]) => (
                    <button key={digit} type="button" className="dial-key" disabled={busy || inCall} aria-label={'Add ' + digit} onClick={() => setPhoneNumber((value) => value + digit)}>
                      <span>{digit}</span><small aria-hidden="true">{letters || "\u00a0"}</small>
                    </button>
                  ))}
                </div>
                <div className="dialer-tools">
                  <button type="button" className="dial-tool" disabled={busy || inCall} aria-label="Add country code plus" onClick={() => setPhoneNumber((value) => value.startsWith("+") ? value : "+" + value)}>+</button>
                  <button type="submit" className="call-button" disabled={busy || inCall || connection !== "ready" || !phoneNumber.trim()} aria-label={busy ? "Please wait" : "Call phone number"} title="Call">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.2 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.96.35 1.9.69 2.79a2 2 0 0 1-.45 2.11L8.08 9.89a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.89.34 1.83.57 2.79.69A2 2 0 0 1 22 16.92z"/>
                    </svg>
                  </button>
                  <button type="button" className="dial-tool" disabled={busy || inCall || !phoneNumber} aria-label="Delete last digit" title="Backspace" onClick={() => setPhoneNumber((value) => value.slice(0, -1))}>
                    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"/><path d="m10 9 6 6m0-6-6 6"/></svg>
                  </button>
                </div>
              </div>
              <div className="actions">
                <button className="button" type="button" disabled={!inCall || incomingCall || busy} aria-pressed={muted} onClick={toggleMute}>{muted ? "Unmute" : "Mute"}</button>
                <button className="button danger" type="button" disabled={!inCall || incomingCall || busy} onClick={hangup}>Hang up</button>
              </div>
            </form>
            <div className="status" role="status"><span className={'dot ' + (inCall ? "online" : "")}/>{status}</div>
            {error && <p className="error" role="alert">{error}</p>}
            <p className="help microphone-help">Uses your microphone and speakers. Allow microphone access when prompted.</p>
            <details className="response">
              <summary>Inbound diagnostic</summary>
              <p>{inboundDebug}</p>
              {lastInboundEvent && <pre>{lastInboundEvent}</pre>}
            </details>
            <audio id="remoteAudio" autoPlay controls={inCall} aria-label="Call audio"/>
          </section>
          <aside className="card">
            <div className="card-header">
              <h2>Connection guide</h2>
              <button className="button" type="button" onClick={checkBackend} disabled={health === "Checking backend..."}>Refresh</button>
            </div>
            {missingConfiguration.length > 0 && <p className="error" role="alert">Set these values in backend/.env: {missingConfiguration.join(", ")}. Restart FastAPI, then refresh.</p>}
            <div className="route-list">
              <div className="route"><code>1. Connect</code><p>Connect to Telnyx to enable inbound and outbound browser calls.</p></div>
              <div className="route"><code>2. Dial</code><p>Type a phone number or use the keypad. Add + and the country code.</p></div>
              <div className="route"><code>3. Call</code><p>Press the green phone icon and allow microphone access. Use Mute or Hang up during your call.</p></div>
            </div>
            <a className="docs" href={API_URL + "/docs"} target="_blank" rel="noreferrer">Open backend API docs ↗</a>
            <p className="footer">API keys stay on the backend. Browser calls use a temporary session token.</p>
          </aside>
        </div>
        <p className="footer">Local test environment · Calls use your Telnyx account.</p>
      </main>
    );
}
