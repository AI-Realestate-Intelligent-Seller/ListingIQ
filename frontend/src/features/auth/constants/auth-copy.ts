import type { AuthMode } from "../types/auth.types";

type AuthPageCopy = {
  eyebrow: string;
  title: string;
  description: string;
  visualTitle: string;
  visualDescription: string;
  successTitle: string;
  successDescription: string;
  submitLabel: string;
};

export const AUTH_COPY: Record<AuthMode, AuthPageCopy> = {
  login: {
    eyebrow: "WELCOME BACK",
    title: "Log in to ListingIQ.",
    description: "Enter your details to access your workspace.",
    visualTitle: "Your brokerage intelligence, ready when you are.",
    visualDescription: "Pick up exactly where your team left off.",
    successTitle: "You’re logged in.",
    successDescription: "Your ListingIQ session is active and ready to use.",
    submitLabel: "Log in",
  },
  register: {
    eyebrow: "START YOUR WORKSPACE",
    title: "Create your account.",
    description:
      "Set up your brokerage workspace and invite your team when you’re ready.",
    visualTitle: "Give every seller opportunity a clear next step.",
    visualDescription:
      "Prioritized leads. Natural outreach. Confident agent handoffs.",
    successTitle: "Your account is ready.",
    successDescription:
      "Your brokerage workspace has been created and your session is active.",
    submitLabel: "Create brokerage account",
  },
};
