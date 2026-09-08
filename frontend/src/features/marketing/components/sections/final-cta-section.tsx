import Link from "next/link";

/* CTA — cream background (breaks the dark-green stats → dark-green footer run) */
export function FinalCtaSection() {
  return (
    <section className="lp-cta" id="contact">
      <div className="lp-cta-inner">
        <p className="lp-eyebrow lp-eyebrow-dark">Get started today</p>
        <h2>Stop managing tools.<br />Start managing leads</h2>
        <p>
          AI handles the first touch. Your team closes the deal.
          No scattered systems. No guesswork. No bad replies.
        </p>
        <div className="lp-cta-btns">
          <Link href="/register" className="lp-btn lp-btn-green">
            Create a free account
          </Link>
          
        </div>
      </div>
    </section>
  );
}
