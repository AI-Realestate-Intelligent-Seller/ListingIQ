import type { AreaGeometry, MapBounds, MapPoint } from "@/features/leads/types/leads.types";


type LocationCoordinate = {
  latitude?: string | number;
  longitude?: string | number;
};


function coordinatePoints(data: unknown): MapPoint[] {
  if (!Array.isArray(data)) return [];

  return data
    .map((item) => item as LocationCoordinate)
    .filter(
      (item) =>
        item.latitude !== undefined &&
        item.latitude !== "" &&
        item.longitude !== undefined &&
        item.longitude !== "",
    )
    .map((item): MapPoint => [Number(item.latitude), Number(item.longitude)])
    .filter(([latitude, longitude]) => Number.isFinite(latitude) && Number.isFinite(longitude));
}


/** Build a padded viewport from the state's ZIP centroids returned by the API. */
export function getLocationBounds(data: unknown): MapBounds | null {
  const points = coordinatePoints(data);

  if (points.length === 0) return null;

  const latitudes = points.map(([latitude]) => latitude);
  const longitudes = points.map(([, longitude]) => longitude);
  const north = Math.max(...latitudes);
  const south = Math.min(...latitudes);
  const east = Math.max(...longitudes);
  const west = Math.min(...longitudes);
  const latitudePadding = Math.max((north - south) * 0.08, 0.15);
  const longitudePadding = Math.max((east - west) * 0.08, 0.15);

  return {
    north: Math.min(90, north + latitudePadding),
    south: Math.max(-90, south - latitudePadding),
    east: Math.min(180, east + longitudePadding),
    west: Math.max(-180, west - longitudePadding),
  };
}


/** Trace the outer edge of a state's ZIP coordinates for a highlighted map outline. */
export function getLocationOutline(data: unknown): MapPoint[] | null {
  const points = [...new Map(
    coordinatePoints(data).map(([latitude, longitude]) => [
      `${latitude}:${longitude}`,
      [longitude, latitude] as [number, number],
    ]),
  ).values()].sort(([x1, y1], [x2, y2]) => x1 - x2 || y1 - y2);

  if (points.length < 3) {
    const bounds = getLocationBounds(data);
    if (!bounds) return null;
    return [
      [bounds.south, bounds.west],
      [bounds.south, bounds.east],
      [bounds.north, bounds.east],
      [bounds.north, bounds.west],
    ];
  }

  const cross = (
    origin: [number, number],
    first: [number, number],
    second: [number, number],
  ) =>
    (first[0] - origin[0]) * (second[1] - origin[1]) -
    (first[1] - origin[1]) * (second[0] - origin[0]);
  const half = (items: [number, number][]) => {
    const hull: [number, number][] = [];
    for (const point of items) {
      while (hull.length >= 2 && cross(hull[hull.length - 2], hull[hull.length - 1], point) <= 0) {
        hull.pop();
      }
      hull.push(point);
    }
    return hull;
  };

  const outline = [
    ...half(points).slice(0, -1),
    ...half([...points].reverse()).slice(0, -1),
  ];
  return outline.map(([longitude, latitude]): MapPoint => [latitude, longitude]);
}


/** Fetch the real, dissolved outline for a set of ZIP codes from the boundary service. */
export async function getBoundary(zipCodes: string[]): Promise<AreaGeometry | null> {
  const zips = [...new Set(zipCodes.filter(Boolean))];
  if (zips.length === 0) return null;

  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/boundary?zips=${encodeURIComponent(zips.join(","))}`,
  );

  if (!response.ok) return null;

  const feature = await response.json();
  return (feature?.geometry as AreaGeometry) ?? null;
}

/** Compute a padded viewport from a Polygon/MultiPolygon's own coordinates. */
export function getBoundsFromGeometry(geometry: AreaGeometry | null): MapBounds | null {
  if (!geometry) return null;

  const rings: number[][][] =
    geometry.type === "Polygon" ? geometry.coordinates : geometry.coordinates.flat();

  let north = -Infinity;
  let south = Infinity;
  let east = -Infinity;
  let west = Infinity;

  for (const ring of rings) {
    for (const [longitude, latitude] of ring) {
      if (latitude > north) north = latitude;
      if (latitude < south) south = latitude;
      if (longitude > east) east = longitude;
      if (longitude < west) west = longitude;
    }
  }

  if (!Number.isFinite(north) || !Number.isFinite(south) || !Number.isFinite(east) || !Number.isFinite(west)) {
    return null;
  }

  const latitudePadding = Math.max((north - south) * 0.08, 0.02);
  const longitudePadding = Math.max((east - west) * 0.08, 0.02);

  return {
    north: Math.min(90, north + latitudePadding),
    south: Math.max(-90, south - latitudePadding),
    east: Math.min(180, east + longitudePadding),
    west: Math.max(-180, west - longitudePadding),
  };
}

export async function getFilterData() {
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/filter`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch states");
  }

  return response.json();
}


/* ==========================================
   Get ZIP Codes by State
   ========================================== */

export async function getZipCodes(state: string) {
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/zipcodes/${encodeURIComponent(
      state
    )}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch ZIP codes");
  }

  return response.json();
}


/* ==========================================
   Get All ZIP Codes
   ========================================== */

export async function getAllZipCodes() {
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/zipcodes`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch ZIP codes");
  }

  return response.json();
}


/* ==========================================
   Get Cities
   State -> Cities
   ZIP -> City
   State + ZIP -> City
   ========================================== */

export async function getCities(params: {
  zipcode?: string;
  state?: string;
}) {
  const query = new URLSearchParams();

  if (params.zipcode) {
    query.append("zipcode", params.zipcode);
  }

  if (params.state) {
    query.append("state", params.state);
  }

  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/cities?${query.toString()}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch Cities");
  }

  return response.json();
}


/* ==========================================
   Get States by City
   ========================================== */

export async function getStatesByCity(city: string) {
  const query = new URLSearchParams();

  if (city) {
    query.append("city", city);
  }

  const response = await fetch(
    `${
      process.env.NEXT_PUBLIC_API_URL
    }/location/states-by-city?${query.toString()}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch states by city");
  }

  return response.json();
}


/* ==========================================
   Get ZIP Codes by City
   ========================================== */

export async function getZipCodesByCity(city: string) {
  const query = new URLSearchParams();

  if (city) {
    query.append("city", city);
  }

  const response = await fetch(
    `${
      process.env.NEXT_PUBLIC_API_URL
    }/location/zipcodes-by-city?${query.toString()}`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch ZIP codes by city");
  }

  return response.json();
}


/* ==========================================
   Geocoding / Location Resolver Types
   ========================================== */

export type ResolvedLocationApiItem = {
  city: string;
  state: string;
  state_code?: string;
  zip: string;

  county?: string | null;

  latitude?: number;
  longitude?: number;
};

export type ResolveLocationResponse = {
  found: boolean;
  data: ResolvedLocationApiItem[] | null;
};


/* ==========================================
   Resolve City / Place / Alias

   Examples:

   Barrington Hills
   Brington
   Brington Hills
   Neighborhood / subdivision names

   Geocoder returns canonical:
   city
   state
   state_code
   zip
   ========================================== */

export async function resolveLocation(
  city: string,
  state?: string
): Promise<ResolveLocationResponse> {
  const query = new URLSearchParams();

  if (city) {
    query.append("city", city);
  }

  if (state) {
    query.append("state", state);
  }

  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/resolve?${query.toString()}`
  );

  if (!response.ok) {
    throw new Error("Failed to resolve location");
  }

  return response.json() as Promise<ResolveLocationResponse>;
}


/* ==========================================
   Get State + ZIPs by City

   Exact CSV city lookup.

   Example:
   Barrington
        ->
   Illinois,IL
   60010
   60011
   ========================================== */

export async function getByCity(city: string) {
  if (!city) {
    return {
      states: [],
      zipcodes: [],
    };
  }

  const query = new URLSearchParams({
    city,
  });

  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/by-city?${query.toString()}`
  );

  if (!response.ok) {
    if (response.status === 404) {
      return {
        states: [],
        zipcodes: [],
      };
    }

    throw new Error("Failed to fetch location data by city");
  }

  return response.json();
}


export async function getByCounty(county: string, state?: string) {
  const query = new URLSearchParams({ county });
  if (state) query.set("state", state);
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/by-county?${query.toString()}`
  );
  if (!response.ok) {
    if (response.status === 404) return { states: [], zipcodes: [] };
    throw new Error("Failed to fetch location data by county");
  }
  return response.json();
}


/* ==========================================
   Get State + City by ZIP

   Example:
   60010
        ->
   Illinois,IL
   Barrington
   ========================================== */

export async function getByZip(zipcode: string) {
  if (!zipcode) {
    return {
      states: [],
      cities: [],
      zipcodes: [],
    };
  }

  const query = new URLSearchParams({
    zipcode,
  });

  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/location/by-zip?${query.toString()}`
  );

  if (!response.ok) {
    if (response.status === 404) {
      return {
        states: [],
        cities: [],
        zipcodes: [],
      };
    }

    throw new Error("Failed to fetch location data by ZIP");
  }

  return response.json();
}
