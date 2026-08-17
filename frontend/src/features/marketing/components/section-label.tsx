type SectionLabelProps = {
  number: string;
  children: React.ReactNode;
  light?: boolean;
};

export function SectionLabel({
  number,
  children,
  light = false,
}: SectionLabelProps) {
  const className = light ? "section-label light" : "section-label";

  return (
    <div className={className}>
      <span>{number}</span>
      {children}
    </div>
  );
}
