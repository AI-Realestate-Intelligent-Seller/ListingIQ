"use client";

import { useEffect } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";

type AppointmentLocationMapProps = {
  address: string;
  latitude: number;
  longitude: number;
};

function CenterOnAddress({ latitude, longitude }: Pick<AppointmentLocationMapProps, "latitude" | "longitude">) {
  const map = useMap();

  useEffect(() => {
    map.setView([latitude, longitude], 17);
  }, [latitude, longitude, map]);

  return null;
}

export function AppointmentLocationMap({
  address,
  latitude,
  longitude,
}: AppointmentLocationMapProps) {
  return (
    <div className="appointment-map" data-testid="appointment-map">
      <MapContainer
        center={[latitude, longitude]}
        zoom={17}
        scrollWheelZoom={false}
        attributionControl={false}
        className="appointment-map-canvas"
      >
        <TileLayer
          url="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          maxZoom={19}
        />
        <TileLayer
          url="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          maxZoom={19}
        />
        <CenterOnAddress latitude={latitude} longitude={longitude} />
        <CircleMarker
          center={[latitude, longitude]}
          radius={9}
          pathOptions={{
            color: "#ffffff",
            weight: 3,
            fillColor: "#1f5c4d",
            fillOpacity: 1,
          }}
        >
          <Popup>{address}</Popup>
        </CircleMarker>
      </MapContainer>
      <span className="appointment-map-attribution">
        Map imagery © Esri
      </span>
    </div>
  );
}
