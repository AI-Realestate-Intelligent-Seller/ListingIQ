import Link from "next/link";

import { ArrowIcon } from "@/components/icons/arrow-icon";
import { CheckIcon } from "@/components/icons/check-icon";

import { AUTH_COPY } from "../constants/auth-copy";
import type { AuthMode } from "../types/auth.types";

type AuthSuccessProps = {
  mode: AuthMode;
};

export function AuthSuccess({ mode }: AuthSuccessProps) {
  const copy = AUTH_COPY[mode];

  return (
    <div className="auth-success" role="status">
      <span>
        <CheckIcon />
      </span>
      <h2>{copy.successTitle}</h2>
      <p>{copy.successDescription}</p>
      <Link className="button button-large" href="/">
        Continue to ListingIQ
        <ArrowIcon />
      </Link>
    </div>
  );
}
