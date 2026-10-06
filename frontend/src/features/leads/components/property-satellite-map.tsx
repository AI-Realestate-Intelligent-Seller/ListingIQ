"use client";

import { CircleMarker, MapContainer, TileLayer } from "react-leaflet";

export function PropertySatelliteMap({ latitude, longitude }: { latitude: number; longitude: number }) {
  return (
    <MapContainer
      center={[latitude, longitude]}
      zoom={17}
      minZoom={3}
      maxZoom={19}
      scrollWheelZoom
      zoomControl
      attributionControl={false}
      className="leads-property-map-canvas"
    >
      <TileLayer
        url="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        maxZoom={19}
      />
      <TileLayer
        url="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
        maxZoom={19}
      />
      <CircleMarker
        center={[latitude, longitude]}
        radius={8}
        pathOptions={{ color: "#fff", weight: 3, fillColor: "#1f5c4d", fillOpacity: 1 }}
      />
    </MapContainer>
  );
}
