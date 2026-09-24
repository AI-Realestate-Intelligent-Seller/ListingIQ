import { describe, expect, it } from "vitest";

import { getLocationBounds, getLocationOutline } from "./getstates";


describe("getLocationBounds", () => {
  it("builds a padded search viewport for any state's coordinate records", () => {
    const bounds = getLocationBounds([
      { latitude: "32.5", longitude: "-124.4" },
      { latitude: "42.0", longitude: "-114.1" },
      { latitude: "not-a-number", longitude: "-120" },
    ]);

    expect(bounds).toEqual({
      north: 42.76,
      south: 31.74,
      east: -113.276,
      west: -125.224,
    });
  });

  it("returns null when the state has no usable coordinates", () => {
    expect(getLocationBounds([])).toBeNull();
    expect(getLocationBounds([{ latitude: "", longitude: "" }])).toBeNull();
  });

  it("creates a visible highlighted area for a single ZIP coordinate", () => {
    const outline = getLocationOutline([{ latitude: 40.4406, longitude: -79.9959 }]);

    const expected = [
      [40.2906, -80.1459],
      [40.2906, -79.8459],
      [40.5906, -79.8459],
      [40.5906, -80.1459],
    ];
    expect(outline).toHaveLength(expected.length);
    expected.forEach(([latitude, longitude], index) => {
      expect(outline?.[index][0]).toBeCloseTo(latitude);
      expect(outline?.[index][1]).toBeCloseTo(longitude);
    });
  });

  it("traces an outer state outline while excluding interior locations", () => {
    expect(getLocationOutline([
      { latitude: 40, longitude: -80 },
      { latitude: 42, longitude: -80 },
      { latitude: 42, longitude: -74 },
      { latitude: 40, longitude: -74 },
      { latitude: 41, longitude: -77 },
    ])).toEqual([
      [40, -80],
      [40, -74],
      [42, -74],
      [42, -80],
    ]);
  });
});
