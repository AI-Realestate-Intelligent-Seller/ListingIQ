import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SmsMessage } from "../types/sms.types";
import { useThreadAutoscroll } from "./use-thread-autoscroll";

const message = (id: number): SmsMessage => ({ id, direction: "inbound" } as SmsMessage);

/** A 1000px-tall thread in a 200px viewport. */
function thread() {
  const element = document.createElement("div");
  Object.defineProperty(element, "scrollHeight", { value: 1000 });
  Object.defineProperty(element, "clientHeight", { value: 200 });
  return element;
}

function scrollTo(element: HTMLDivElement, top: number) {
  element.scrollTop = top;
  element.dispatchEvent(new Event("scroll"));
}

describe("useThreadAutoscroll", () => {
  it("leaves the reader where they scrolled when a refresh brings nothing new", () => {
    const element = thread();
    const ref = { current: element };
    const { rerender } = renderHook(({ messages }) => useThreadAutoscroll(ref, messages, 1),
      { initialProps: { messages: [message(1), message(2)] } });
    expect(element.scrollTop).toBe(1000); // opening the thread shows the newest message

    scrollTo(element, 100);
    rerender({ messages: [message(1), message(2)] }); // polling: same messages, new array
    expect(element.scrollTop).toBe(100);

    rerender({ messages: [message(1), message(2), message(3)] }); // new message while reading history
    expect(element.scrollTop).toBe(100);
  });

  it("follows a new message when the reader is at the bottom, or after sending", () => {
    const element = thread();
    const ref = { current: element };
    const { result, rerender } = renderHook(({ messages }) => useThreadAutoscroll(ref, messages, 1),
      { initialProps: { messages: [message(1)] } });

    scrollTo(element, 790); // within 80px of the bottom
    rerender({ messages: [message(1), message(2)] });
    expect(element.scrollTop).toBe(1000);

    scrollTo(element, 100);
    result.current(); // the owner sends a message
    rerender({ messages: [message(1), message(2), message(3)] });
    expect(element.scrollTop).toBe(1000);
  });

  it("jumps to the newest message when another conversation opens", () => {
    const element = thread();
    const ref = { current: element };
    const { rerender } = renderHook(({ id }) => useThreadAutoscroll(ref, [message(1)], id), { initialProps: { id: 1 } });
    scrollTo(element, 100);
    rerender({ id: 2 });
    expect(element.scrollTop).toBe(1000);
  });
});
