import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Lead } from "../types/leads.types";
import { LeadMap } from "./lead-map";


const fakeMap = {
  getBounds: () => ({ getNorth: () => 42, getSouth: () => 41, getEast: () => -87, getWest: () => -88 }),
  getZoom: () => 10,
  flyTo: vi.fn(),
  flyToBounds: vi.fn(),
  fitBounds: vi.fn(),
};
const mapHandlers: Record<string, (event: { latlng: { lat: number; lng: number } }) => void> = {};

vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div data-testid="map-container">{children}</div>,
  TileLayer: () => null,
  Popup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Marker: ({ children, eventHandlers }: { children: ReactNode; eventHandlers: { click: () => void } }) => (
    <div role="button" tabIndex={0} data-testid="marker" onClick={eventHandlers.click}>{children}</div>
  ),
  Polygon: ({ pathOptions }: { pathOptions: { color?: string; weight?: number; fillOpacity?: number } }) => (
    <div
      data-testid="polygon"
      data-color={pathOptions.color}
      data-weight={pathOptions.weight}
      data-fill-opacity={pathOptions.fillOpacity}
    />
  ),
  Polyline: () => <div data-testid="polyline" />,
  CircleMarker: () => <div data-testid="polygon-point" />,
  GeoJSON: ({ style }: { style: { color?: string; weight?: number; fillOpacity?: number } }) => (
    <div
      data-testid="geojson"
      data-color={style.color}
      data-weight={style.weight}
      data-fill-opacity={style.fillOpacity}
    />
  ),
  useMap: () => fakeMap,
  useMapEvents: (handlers: typeof mapHandlers) => {
    Object.assign(mapHandlers, handlers);
    return fakeMap;
  },
}));
vi.mock("react-leaflet-cluster", () => ({
  default: ({ children }: { children: ReactNode }) => <div data-testid="clusters">{children}</div>,
}));

const baseLead: Lead = {
  id: 1,
  owner_name: "Marcus Webb",
  phone: "+13125550142",
  phone_numbers: [{ phone: "+13125550142", dnc: false }],
  property_address: "4517 W Adams St, Chicago, IL, 60624",
  area: "Chicago",
  latitude: 41.88,
  longitude: -87.74,
  geocoding_status: "success",
  geocoding_provider: "nominatim",
  source: "propertyradar",
  property_type: "Single family",
  estimated_value: 325000,
  listing_price: null,
  signals: [{ key: "expired", label: "Expired" }],
  score: 80,
  outreach_reason: null,
  stage: "ready",
  last_activity_at: null,
  conversation_id: null,
  created_at: null,
};

const illinoisBounds = { north: 42.5085, south: 36.9703, east: -87.0199, west: -91.5131 };
const pennsylvaniaBounds = { north: 42.2699, south: 39.7198, east: -74.6895, west: -80.5199 };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LeadMap", () => {
  it("clusters mapped leads, renders popup facts, and ignores missing coordinates", () => {
    const toggle = vi.fn();
    render(
      <LeadMap
        leads={[baseLead, { ...baseLead, id: 2, owner_name: "Waiting", latitude: null, longitude: null }]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={null}
        searchAreaPolygon={null}
        onToggleLead={toggle}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={vi.fn()}
        onPolygonClear={vi.fn()}
      />,
    );

    expect(screen.getAllByTestId("marker")).toHaveLength(1);
    expect(screen.getByText("Marcus Webb")).toBeTruthy();
    expect(screen.getByText("propertyradar", { exact: false })).toBeTruthy();
    expect(screen.getByText("$325,000")).toBeTruthy();
    expect(screen.queryByText("Waiting")).toBeNull();
    fireEvent.click(screen.getByTestId("marker"));
    expect(toggle).toHaveBeenCalledWith(1);
  });

  it("opens the existing lead detail behavior from a marker popup", () => {
    const view = vi.fn();
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[1]}
        focusLeadId={1}
        fitToken={0}
        searchAreaBounds={null}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={view}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={vi.fn()}
        onPolygonClear={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View lead" }));
    expect(view).toHaveBeenCalledWith(1);
  });

  it("lets the user draw and apply a polygon filter", () => {
    const apply = vi.fn();
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={null}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={apply}
        onPolygonClear={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Draw polygon" }));
    act(() => {
      mapHandlers.click({ latlng: { lat: 42, lng: -88 } });
      mapHandlers.click({ latlng: { lat: 42, lng: -87 } });
      mapHandlers.click({ latlng: { lat: 41, lng: -87 } });
    });
    fireEvent.click(screen.getByRole("button", { name: "Finish & filter" }));
    expect(apply).toHaveBeenCalledWith([[42, -88], [42, -87], [41, -87]]);
    expect(screen.queryByTestId("polygon")).toBeNull();
  });

  it("clears an existing polygon before a new drawing starts", () => {
    const clear = vi.fn();
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={null}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={[[42, -88], [42, -87], [41, -87]]}
        onPolygonApply={vi.fn()}
        onPolygonClear={clear}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Draw polygon" }));
    expect(clear).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Clear polygon" })).toBeNull();
  });

  it("clears the applied polygon from the clear button", () => {
    const clear = vi.fn();
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={null}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={[[42, -88], [42, -87], [41, -87]]}
        onPolygonApply={vi.fn()}
        onPolygonClear={clear}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Clear polygon" }));
    expect(clear).toHaveBeenCalledOnce();
  });

  it("fits the whole Illinois search area instead of only its lead markers", () => {
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={illinoisBounds}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={vi.fn()}
        onPolygonClear={vi.fn()}
      />,
    );

    const [bounds, options] = fakeMap.flyToBounds.mock.calls.at(-1) ?? [];
    expect(bounds.getNorth()).toBeCloseTo(42.5085);
    expect(bounds.getSouth()).toBeCloseTo(36.9703);
    expect(bounds.getEast()).toBeCloseTo(-87.0199);
    expect(bounds.getWest()).toBeCloseTo(-91.5131);
    expect(options).toEqual({ padding: [36, 36], duration: 0.7 });
  });

  it("still fits Illinois when that search area contains no leads", () => {
    render(
      <LeadMap
        leads={[]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={illinoisBounds}
        searchAreaPolygon={null}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={vi.fn()}
        onPolygonClear={vi.fn()}
      />,
    );

    const [bounds] = fakeMap.flyToBounds.mock.calls.at(-1) ?? [];
    expect(bounds.getNorth()).toBeCloseTo(42.5085);
    expect(bounds.getSouth()).toBeCloseTo(36.9703);
    expect(bounds.getEast()).toBeCloseTo(-87.0199);
    expect(bounds.getWest()).toBeCloseTo(-91.5131);
  });

  it("animates from Chicago leads to Pennsylvania when Pennsylvania is searched", () => {
    render(
      <LeadMap
        leads={[baseLead]}
        selectedIds={[]}
        focusLeadId={null}
        fitToken={0}
        searchAreaBounds={pennsylvaniaBounds}
        searchAreaPolygon={{
          type: "Polygon",
          coordinates: [[[-80.5, 39.7], [-80.5, 42.2], [-74.7, 42.2], [-74.7, 39.7], [-80.5, 39.7]]],
        }}
        onToggleLead={vi.fn()}
        onViewLead={vi.fn()}
        onBoundsChange={vi.fn()}
        activePolygon={null}
        onPolygonApply={vi.fn()}
        onPolygonClear={vi.fn()}
      />,
    );

    const [bounds, options] = fakeMap.flyToBounds.mock.calls.at(-1) ?? [];
    expect(bounds.getNorth()).toBeCloseTo(42.2699);
    expect(bounds.getSouth()).toBeCloseTo(39.7198);
    expect(bounds.getEast()).toBeCloseTo(-74.6895);
    expect(bounds.getWest()).toBeCloseTo(-80.5199);
    expect(options).toEqual({ padding: [36, 36], duration: 0.7 });
    expect(fakeMap.fitBounds).not.toHaveBeenCalled();
    expect(screen.getByTestId("geojson")).toBeTruthy();
  });
});
