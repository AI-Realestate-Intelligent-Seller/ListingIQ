import { useEffect, useRef, type RefObject } from "react";

import type { SmsMessage } from "../types/sms.types";

/** How close to the bottom (px) still counts as "reading the newest message". */
const NEAR_BOTTOM_PX = 80;

/**
 * Keep the newest message in view without fighting the reader.
 *
 * Jumps to the bottom when a different conversation opens. When a new message
 * arrives, follows it only if the reader was already at the bottom, or after
 * the returned function is called (on send). Polling refreshes that bring
 * nothing new never move the scroll position.
 */
export function useThreadAutoscroll(
  threadRef: RefObject<HTMLDivElement | null>,
  messages: SmsMessage[],
  conversationId: number | null,
): () => void {
  const nearBottom = useRef(true);
  const shown = useRef<{ conversationId: number | null; lastId: number | null }>({ conversationId: null, lastId: null });

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    const track = () => {
      nearBottom.current = thread.scrollHeight - thread.scrollTop - thread.clientHeight <= NEAR_BOTTOM_PX;
    };
    thread.addEventListener("scroll", track, { passive: true });
    return () => thread.removeEventListener("scroll", track);
  }, [threadRef, conversationId]);

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    const last = messages.length ? messages[messages.length - 1] : null;
    const lastId = last?.id ?? null;
    const previous = shown.current;
    const switched = previous.conversationId !== conversationId;
    const newMessage = lastId !== null && lastId !== previous.lastId;
    shown.current = { conversationId, lastId };

    if (switched || (newMessage && nearBottom.current)) {
      thread.scrollTop = thread.scrollHeight;
      nearBottom.current = true;
    }
  }, [threadRef, messages, conversationId]);

  // Sending from anywhere in the thread should show the sent message.
  return () => {
    nearBottom.current = true;
  };
}
