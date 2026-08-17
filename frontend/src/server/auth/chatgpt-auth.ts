import { headers } from "next/headers";
import { redirect } from "next/navigation";

export type ChatGPTUser = {
  displayName: string;
  email: string;
  fullName: string | null;
};

const AUTH_HEADERS = {
  email: "oai-authenticated-user-email",
  fullName: "oai-authenticated-user-full-name",
  fullNameEncoding: "oai-authenticated-user-full-name-encoding",
} as const;

const PERCENT_ENCODED_UTF8 = "percent-encoded-utf-8";
const CHATGPT_AUTH_PATHS = {
  signIn: "/signin-with-chatgpt",
  signOut: "/signout-with-chatgpt",
  callback: "/callback",
} as const;

export async function getChatGPTUser(): Promise<ChatGPTUser | null> {
  const requestHeaders = await headers();
  const email = requestHeaders.get(AUTH_HEADERS.email);

  if (!email) {
    return null;
  }

  const encodedFullName = requestHeaders.get(AUTH_HEADERS.fullName);
  const isPercentEncoded =
    requestHeaders.get(AUTH_HEADERS.fullNameEncoding) === PERCENT_ENCODED_UTF8;
  const fullName =
    encodedFullName && isPercentEncoded
      ? safelyDecodeURIComponent(encodedFullName)
      : null;

  return {
    displayName: fullName ?? email,
    email,
    fullName,
  };
}

export async function requireChatGPTUser(
  returnTo: string,
): Promise<ChatGPTUser> {
  const user = await getChatGPTUser();

  if (user) {
    return user;
  }

  redirect(getChatGPTSignInPath(returnTo));
}

export function getChatGPTSignInPath(returnTo: string): string {
  return buildAuthPath(CHATGPT_AUTH_PATHS.signIn, returnTo);
}

export function getChatGPTSignOutPath(returnTo = "/"): string {
  return buildAuthPath(CHATGPT_AUTH_PATHS.signOut, returnTo);
}

function buildAuthPath(path: string, returnTo: string): string {
  const safeReturnTo = getSafeRelativeReturnPath(returnTo);
  return `${path}?return_to=${encodeURIComponent(safeReturnTo)}`;
}

function getSafeRelativeReturnPath(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//")) {
    return "/";
  }

  let url: URL;

  try {
    url = new URL(value, "https://ListingIQ.local");
  } catch {
    return "/";
  }

  if (url.origin !== "https://ListingIQ.local") {
    return "/";
  }

  if (isReservedAuthPath(url.pathname)) {
    return "/";
  }

  return `${url.pathname}${url.search}${url.hash}`;
}

function isReservedAuthPath(pathname: string): boolean {
  return Object.values(CHATGPT_AUTH_PATHS).includes(
    pathname as (typeof CHATGPT_AUTH_PATHS)[keyof typeof CHATGPT_AUTH_PATHS],
  );
}

function safelyDecodeURIComponent(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}
