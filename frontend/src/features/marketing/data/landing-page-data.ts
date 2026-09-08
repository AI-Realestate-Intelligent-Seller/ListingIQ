export const NAVIGATION_ITEMS = [
  { label: "Product", href: "#product" },
  { label: "How it works", href: "#workflow" },
  { label: "Solutions", href: "#solutions" },
  { label: "Security", href: "#security" },
] as const;

export const SELLER_SIGNALS = [
  "FSBO",
  "Expired listing",
  "Pre-foreclosure",
  "High equity",
  "Absentee owner",
  "Probate",
  "Vacant property",
  "Tax delinquent",
  "Lis pendens",
  "Auction",
] as const;

export const CAPABILITIES = [
  {
    number: "01",
    title: "See the signal, not the noise",
    description:
      "Bring property, ownership, contact data, and seller signals into one calm, prioritized workspace.",
    tag: "Seller intelligence",
  },
  {
    number: "02",
    title: "Start the right conversation",
    description:
      "Reach homeowners naturally across voice, SMS, and email with shared context and a respectful cadence.",
    tag: "AI engagement",
  },
  {
    number: "03",
    title: "Hand off with confidence",
    description:
      "Qualify intent, book the next step, and assign the opportunity to the right agent—with the full story attached.",
    tag: "Brokerage workflow",
  },
] as const;

export const WORKFLOW_STEPS = [
  {
    title: "Discover",
    description: "Seller and property signals arrive in one unified view.",
  },
  {
    title: "Prioritize",
    description: "AI surfaces the opportunities worth attention now.",
  },
  {
    title: "Engage",
    description: "Human-sounding outreach opens a useful conversation.",
  },
  {
    title: "Convert",
    description: "Qualified sellers move to the right agent and next step.",
  },
] as const;

export const PREVIEW_METRICS = [
  { label: "Ready now", value: "24", detail: "+8 this week" },
  { label: "Conversations", value: "67", detail: "12 interested" },
  { label: "Appointments", value: "09", detail: "3 today" },
] as const;

export const PREVIEW_LEADS = [
  {
    score: 94,
    address: "2147 N Oakley Ave",
    owner: "Chicago, IL · Elena Park",
    signal: "High equity",
    signalClassName: "orange",
    status: "Interested",
    statusClassName: "",
    featured: true,
  },
  {
    score: 88,
    address: "4821 W Byron St",
    owner: "Chicago, IL · Daniel Reed",
    signal: "Expired",
    signalClassName: "blue",
    status: "Follow up",
    statusClassName: "warm",
    featured: false,
  },
  {
    score: 83,
    address: "915 S Claremont Ave",
    owner: "Chicago, IL · Nina Shah",
    signal: "Absentee",
    signalClassName: "violet",
    status: "Engaged",
    statusClassName: "cool",
    featured: false,
  },
] as const;



export const SLIDES = [
  { src: "/images/home-exterior.jpg",       alt: "Residential property exterior" },
  { src: "/images/hero-slide-2.jpg",        alt: "House with green lawn" },
  { src: "/images/hero-slide-3.jpg",        alt: "Suburban home at dusk" },
  { src: "/images/hero-slide-4.jpg",        alt: "Bungalow house exterior" },
  { src: "/images/chicago-neighborhood.jpg",alt: "Chicago residential neighbourhood" },
];