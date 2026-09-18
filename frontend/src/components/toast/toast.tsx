"use client";

import { useEffect } from "react";

export type ToastTone = "error" | "success";

/**
 * A transient status message that slides in from the top-right and
 * auto-dismisses. Re-mounting on `message` (via the `key` below) restarts
 * the slide-in/out animation for each new message.
 */
export function Toast({
  message,
  tone,
  duration = 7000,
  onDone,
}: {
  message: string;
  tone: ToastTone;
  duration?: number;
  onDone: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDone, duration);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only when the message itself changes
  }, [message]);
  return (
    <div className="toast-stack">
      <div
        key={message}
        role={tone === "error" ? "alert" : "status"}
        className={`toast toast-${tone}`}
      >
        {message}
      </div>
    </div>
  );
}
