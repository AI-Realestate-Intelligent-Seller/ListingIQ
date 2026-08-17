import { redirect } from "next/navigation";

type JoinTokenPageProps = {
  params: Promise<{ token: string }>;
};

/**
 * Legacy path-style invitation links. Invitation emails now link to
 * `/join?token=...`, so anything sent earlier is forwarded there.
 */
export default async function JoinTokenPage({ params }: JoinTokenPageProps) {
  const { token } = await params;
  redirect(`/join?token=${encodeURIComponent(token)}`);
}
