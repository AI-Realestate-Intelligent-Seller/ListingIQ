import type { ReactNode } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PropertySatelliteMap } from "./property-satellite-map";

const mocks = vi.hoisted(() => ({ map: vi.fn(), tile: vi.fn(), marker: vi.fn() }));

vi.mock("react-leaflet", () => ({
  MapContainer: ({ children, ...props }: { children: ReactNode; [key: string]: unknown }) => {
    mocks.map(props);
    return <div data-testid="map">{children}</div>;
  },
  TileLayer: (props: Record<string, unknown>) => {
    mocks.tile(props);
    return null;
  },
  CircleMarker: (props: Record<string, unknown>) => {
    mocks.marker(props);
    return null;
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PropertySatelliteMap", () => {
  it("uses satellite imagery and enables scroll and button zoom without provider controls", () => {
    render(<PropertySatelliteMap latitude={41.778644} longitude={-87.656574} />);

    expect(mocks.map).toHaveBeenCalledWith(expect.objectContaining({
      center: [41.778644, -87.656574],
      scrollWheelZoom: true,
      zoomControl: true,
      attributionControl: false,
    }));
    expect(mocks.tile).toHaveBeenCalledWith(expect.objectContaining({ url: expect.stringContaining("World_Imagery") }));
    expect(mocks.marker).toHaveBeenCalledWith(expect.objectContaining({ center: [41.778644, -87.656574] }));
  });
});
