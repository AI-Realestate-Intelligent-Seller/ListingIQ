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