"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";

/**
 * A textarea that grows with its content: while typing, when filled from an
 * import, and when its width changes (for example on rotating a phone). The
 * text is never altered; line breaks, emoji and Korean are kept as typed.
 */
export const AutoTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { value: string }>(
  function AutoTextarea({ className, rows = 2, ...props }, forwarded) {
    const ref = useRef<HTMLTextAreaElement>(null);
    useImperativeHandle(forwarded, () => ref.current as HTMLTextAreaElement);
    const resize = useCallback(() => {
      const element = ref.current;
      if (!element) return;
      element.style.height = "auto";
      // scrollHeight covers content and padding; add the borders so nothing is clipped.
      const borders = element.offsetHeight - element.clientHeight;
      element.style.height = `${element.scrollHeight + Math.max(borders, 0)}px`;
    }, []);
    useLayoutEffect(resize, [props.value, resize]);
    useEffect(() => {
      const element = ref.current;
      if (!element || typeof ResizeObserver === "undefined") return;
      let width = element.clientWidth;
      const observer = new ResizeObserver(() => {
        if (element.clientWidth !== width) { width = element.clientWidth; resize(); }
      });
      observer.observe(element);
      return () => observer.disconnect();
    }, [resize]);
    return <textarea {...props} rows={rows} ref={ref} className={`persona-autosize${className ? ` ${className}` : ""}`} />;
  },
);
