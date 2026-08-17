function containsSchedulingPressure(text = '') {
  return /\b(?:schedule|book|appointment|what time works|when (?:can|could|would) (?:we|you)|quick (?:call|conversation|chat)|phone call|i can do .{0,60}(?:a\.?m\.?|p\.?m\.?))\b|\b(?:would you|could we|can we|want to|prefer to|let['’]?s)\b[^.!?]{0,35}\b(?:call|talk|meet|speak)\b/i.test(String(text));
}

function containsTimeProposal(text = '') {
  const value = String(text);
  const time = /\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b/i;
  const weekday = /\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/i;
  return (time.test(value) || weekday.test(value))
    && /\b(?:i can do|i have|available|open|would .{0,20} work|does .{0,20} work|tomorrow at|today at)\b/i.test(value);
}

function containsUnbookedConfirmation(text = '') {
  return /\b(?:you(?:['’]re| are) all set|confirmed|booked|i(?:['’]ll| will) (?:book|confirm|reserve|schedule|set (?:it|that) up|put you down|get (?:it|that) booked|give you a call|call you|ring you)|see you at|talk (?:to you )?(?:then|at)|looking forward to (?:it|our call))\b/i.test(String(text));
}

// These are output safety guards only. Conversation intent and calendar state
// are decided by DeepSeek's structured disposition response in ai-runtime.js.
export { containsSchedulingPressure, containsTimeProposal, containsUnbookedConfirmation };
