import Link from "next/link";

type BrandProps = {
  light?: boolean;
};

export function Brand({ light = false }: BrandProps) {
  const className = light ? "brand brand-light" : "brand";

  return (
    <Link className={className} href="/" aria-label="ListingIQ home">
      <span className="brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <strong>
        Listing<span>IQ</span>
      </strong>
    </Link>
  );
}
