/**
 * BatchData's quickLists flags, worded the way "Why this lead stands out"
 * shows them. Shared by the details drawer and the pool card.
 */
export const QUICK_LIST_LABELS: Record<string, string> = {
  activeAuction: "Active auction",
  activeListing: "Actively listed",
  cashBuyer: "Cash buyer",
  corporateOwned: "Corporate owned",
  expiredListing: "Expired listing",
  failedListing: "Failed listing",
  fixAndFlip: "Fix & flip",
  forSaleByOwner: "For sale by owner",
  freeAndClear: "Free & clear",
  highEquity: "High equity",
  inherited: "Inherited property",
  listedBelowMarketPrice: "Below market price",
  noticeOfDefault: "Notice of default",
  noticeOfLisPendens: "Lis pendens filed",
  noticeOfSale: "Notice of sale",
  onMarket: "On market",
  outOfStateOwner: "Out-of-state owner",
  ownerOccupied: "Owner occupied",
  pendingListing: "Pending listing",
  preforeclosure: "Pre-foreclosure",
  recentlySold: "Recently sold",
  taxDefault: "Tax default",
  tiredLandlord: "Tired landlord",
  trustOwned: "Trust owned",
  vacant: "Vacant",
  vacantLot: "Vacant lot",
};

/** Unknown flags read as words: samePropertyAndMailingAddress → "Same Property And Mailing Address". */
export function opportunityLabel(key: string): string {
  const known = QUICK_LIST_LABELS[key];
  if (known) return known;
  const words = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
