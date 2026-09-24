"use client";

import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Marker,
  Polygon,
  Polyline,
  Popup,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";

import type { AreaGeometry, Lead, MapBounds, MapPoint } from "../types/leads.types";
import { parseAddress } from "./lead-filterComponents";


type Props = {
  leads: Lead[];
  selectedIds: number[];
  focusLeadId: number | null;
  fitToken: number;
  searchAreaBounds: MapBounds | null;
  searchAreaPolygon: AreaGeometry | null;
  onToggleLead: (leadId: number) => void;
  onViewLead: (leadId: number) => void;
  onBoundsChange: (bounds: MapBounds) => void;
  activePolygon: MapPoint[] | null;
  onPolygonApply: (polygon: MapPoint[]) => void;
  onPolygonClear: () => void;
};


function currentBounds(map: L.Map): MapBounds {
  const bounds = map.getBounds();
  return {
    north: bounds.getNorth(),
    south: bounds.getSouth(),
    east: bounds.getEast(),
    west: bounds.getWest(),
  };
}


function PolygonCapture({ drawing, onPoint }: {
  drawing: boolean;
  onPoint: (point: MapPoint) => void;
}) {
  useMapEvents({
    click: (event) => {
      if (drawing) onPoint([event.latlng.lat, event.latlng.lng]);
    },
  });
  return null;
}


function BoundsReporter({ onChange }: { onChange: (bounds: MapBounds) => void }) {
  const map = useMapEvents({
    moveend: () => onChange(currentBounds(map)),
    zoomend: () => onChange(currentBounds(map)),
  });
  useEffect(() => onChange(currentBounds(map)), [map, onChange]);
  return null;
}


function MapPosition({ leads, focusLeadId, fitToken, searchAreaBounds }: {
  leads: Lead[];
  focusLeadId: number | null;
  fitToken: number;
  searchAreaBounds: MapBounds | null;
}) {
  const map = useMap();
  useEffect(() => {
    const lead = leads.find((item) => item.id === focusLeadId);
    if (lead?.latitude != null && lead.longitude != null) {
      map.flyTo([lead.latitude, lead.longitude], Math.max(map.getZoom(), 15), { duration: 0.45 });
    }
  }, [focusLeadId, leads, map]);
  useEffect(() => {
    if (searchAreaBounds) {
      map.flyToBounds(
        L.latLngBounds(
          [searchAreaBounds.south, searchAreaBounds.west],
          [searchAreaBounds.north, searchAreaBounds.east],
        ),
        { padding: [36, 36], duration: 0.7 },
      );
      return;
    }

    const points = leads
      .filter((lead) => lead.latitude != null && lead.longitude != null)
      .map((lead) => L.latLng(lead.latitude as number, lead.longitude as number));
    if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 15 });
  }, [fitToken, leads, map, searchAreaBounds]);
  return null;
}


function markerIcon(selected: boolean): L.DivIcon {
  return L.divIcon({
    className: "leads-map-marker-shell",
    html: `<span class="leads-map-marker${selected ? " selected" : ""}"><span></span></span>`,
    iconSize: [28, 36],
    iconAnchor: [14, 34],
    popupAnchor: [0, -32],
  });
}


function money(value: string | number | null): string | null {
  if (value == null || value === "") return null;
  const number = typeof value === "number" ? value : Number(String(value).replace(/[$,]/g, ""));
  return Number.isFinite(number)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(number)
    : String(value);
}


export function LeadMap({
  leads,
  selectedIds,
  focusLeadId,
  fitToken,
  searchAreaBounds,
  searchAreaPolygon,
  onToggleLead,
  onViewLead,
  onBoundsChange,
  activePolygon,
  onPolygonApply,
  onPolygonClear,
}: Props) {
  const [drawing, setDrawing] = useState(false);
  const [draftPolygon, setDraftPolygon] = useState<MapPoint[]>([]);
  const [showAttribution, setShowAttribution] = useState(false);
  const mappable = useMemo(
    () => leads.filter((lead) => lead.latitude != null && lead.longitude != null),
    [leads],
  );
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  function startDrawing() {
    setDraftPolygon([]);
    onPolygonClear();
    setDrawing(true);
  }

  function finishDrawing() {
    if (draftPolygon.length < 3) return;
    const polygon = draftPolygon;
    setDrawing(false);
    setDraftPolygon([]);
    onPolygonApply(polygon);
  }

  function cancelDrawing() {
    setDrawing(false);
    setDraftPolygon([]);
  }

  function clearPolygon() {
    setDrawing(false);
    setDraftPolygon([]);
    onPolygonClear();
  }

  return (
    <div className="leads-map-frame" data-testid="lead-map">
      <div className="leads-map-draw-controls" role="toolbar" aria-label="Polygon filter controls">
        {!drawing ? (
          <button type="button" onClick={startDrawing}>Draw polygon</button>
        ) : (
          <>
            <button
              type="button"
              disabled={draftPolygon.length === 0}
              onClick={() => setDraftPolygon((points) => points.slice(0, -1))}
            >
              Undo point
            </button>
            <button type="button" disabled={draftPolygon.length < 3} onClick={finishDrawing}>
              Finish & filter
            </button>
            <button type="button" onClick={cancelDrawing}>Cancel</button>
          </>
        )}
        {activePolygon && !drawing ? (
          <button type="button" className="clear" onClick={clearPolygon}>Clear polygon</button>
        ) : null}
      </div>
      <MapContainer
        center={[39.5, -98.35]}
        zoom={4}
        scrollWheelZoom
        attributionControl={false}
        className="leads-map-canvas"
      >
        <TileLayer
          url="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          maxZoom={19}
        />
        <TileLayer
          url="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          maxZoom={19}
        />
        <BoundsReporter onChange={onBoundsChange} />
        <PolygonCapture
          drawing={drawing}
          onPoint={(point) => setDraftPolygon((points) => [...points, point])}
        />
        <MapPosition
          leads={mappable}
          focusLeadId={focusLeadId}
          fitToken={fitToken}
          searchAreaBounds={searchAreaBounds}
        />
        {searchAreaPolygon ? (
          <GeoJSON
            key={JSON.stringify(searchAreaPolygon)}
            data={searchAreaPolygon}
            style={{ color: "#2563eb", weight: 3, fillColor: "#3b82f6", fillOpacity: 0.1 }}
          />
        ) : null}
        {activePolygon && !drawing ? (
          <Polygon
            positions={activePolygon}
            pathOptions={{
              color: "#ffdc00",
              weight: 5,
              opacity: 1,
              fillColor: "#fff200",
              fillOpacity: 0.24,
            }}
          />
        ) : null}
        {drawing && draftPolygon.length >= 3 ? (
          <Polygon
            positions={draftPolygon}
            pathOptions={{
              color: "#ffdc00",
              weight: 5,
              opacity: 1,
              dashArray: "10 7",
              fillColor: "#fff200",
              fillOpacity: 0.24,
            }}
          />
        ) : drawing && draftPolygon.length >= 2 ? (
          <Polyline
            positions={draftPolygon}
            pathOptions={{ color: "#ffdc00", weight: 5, opacity: 1, dashArray: "10 7" }}
          />
        ) : null}
        {drawing ? draftPolygon.map((point, index) => (
          <CircleMarker
            key={`${point[0]}-${point[1]}-${index}`}
            center={point}
            radius={6}
            pathOptions={{ color: "#111827", fillColor: "#fff200", fillOpacity: 1, weight: 3 }}
          />
        )) : null}
        <MarkerClusterGroup chunkedLoading maxClusterRadius={48}>
          {mappable.map((lead) => {
            const location = parseAddress(lead.property_address);
            const estimated = money(lead.estimated_value);
            const listing = money(lead.listing_price);
            return (
              <Marker
                key={lead.id}
                position={[lead.latitude as number, lead.longitude as number]}
                icon={markerIcon(selected.has(lead.id))}
                eventHandlers={{ click: () => onToggleLead(lead.id) }}
              >
                <Popup minWidth={230}>
                  <article className="leads-map-popup">
                    <strong>{lead.owner_name || lead.phone || "Unnamed owner"}</strong>
                    <span>{lead.property_address || "Address unavailable"}</span>
                    {(location.city || location.state || location.zip) ? (
                      <small>{[location.city, location.state, location.zip].filter(Boolean).join(", ")}</small>
                    ) : null}
                    <dl>
                      <div><dt>Status</dt><dd>{lead.stage.replaceAll("_", " ")}</dd></div>
                      {lead.signals.length ? (
                        <div><dt>Category</dt><dd>{lead.signals.map((signal) => signal.label).join(", ")}</dd></div>
                      ) : null}
                      <div><dt>Source</dt><dd>{lead.source.replaceAll("_", " ")}</dd></div>
                      {lead.property_type ? <div><dt>Type</dt><dd>{lead.property_type}</dd></div> : null}
                      {estimated ? <div><dt>Est. value</dt><dd>{estimated}</dd></div> : null}
                      {listing ? <div><dt>Listing</dt><dd>{listing}</dd></div> : null}
                    </dl>
                    <button type="button" onClick={() => onViewLead(lead.id)}>View lead</button>
                  </article>
                </Popup>
              </Marker>
            );
          })}
        </MarkerClusterGroup>
      </MapContainer>
      <div className="leads-map-attribution">
        <button
          type="button"
          aria-expanded={showAttribution}
          onClick={() => setShowAttribution((visible) => !visible)}
        >
          Map data
        </button>
        {showAttribution ? (
          <div role="note">
            Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community.
            Labels © Esri and its data providers.
          </div>
        ) : null}
      </div>
    </div>
  );
}
