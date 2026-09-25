import dotenv from "dotenv";
import { Logger } from "../index.js";

dotenv.config();

const DEFAULT_BASE_URL = "https://api.geoapify.com";
const ATTRIBUTION = "Powered by Geoapify | © OpenStreetMap contributors";

type TravelMode = "driving" | "walking" | "bicycling" | "transit";
type FetchImplementation = typeof fetch;

interface GeoapifyMapsToolsOptions {
  apiKey?: string;
  baseUrl?: string;
  language?: string;
  fetchImpl?: FetchImplementation;
  timeoutMs?: number;
}

interface SearchParams {
  location: { lat: number; lng: number };
  radius?: number;
  keyword?: string;
  openNow?: boolean;
  minRating?: number;
}

interface PlaceResult {
  name: string;
  place_id: string;
  formatted_address?: string;
  geometry: { location: { lat: number; lng: number } };
  rating: null;
  user_ratings_total: null;
  opening_hours: { open_now: null };
  categories?: string[];
  distance?: number;
  attribution: string;
  provider_note?: string;
}

interface GeocodeResult {
  lat: number;
  lng: number;
  formatted_address?: string;
  place_id?: string;
  attribution?: string;
}

interface GeoapifyFeature {
  properties?: Record<string, any>;
  geometry?: { coordinates?: number[]; type?: string };
}

interface GeoapifyFeatureCollection {
  features?: GeoapifyFeature[];
}

interface GeoapifyGeocodeResponse {
  results?: Array<Record<string, any>>;
}

interface ResolvedLocation extends GeocodeResult {
  source: string;
}

const CATEGORY_RULES: Array<[RegExp, string]> = [
  [/(restaurant|restaurants|food|eat|餐厅|餐馆|美食)/i, "catering.restaurant"],
  [/(cafe|coffee|咖啡)/i, "catering.cafe"],
  [/(bar|pub|酒吧)/i, "catering.bar,catering.pub"],
  [/(hotel|motel|hostel|酒店|旅馆|宾馆)/i, "accommodation"],
  [/(hospital|clinic|医院|诊所)/i, "healthcare.hospital,healthcare.clinic_or_praxis"],
  [/(pharmacy|drugstore|药店)/i, "healthcare.pharmacy"],
  [/(school|university|college|学校|大学)/i, "education"],
  [/(museum|博物馆)/i, "entertainment.museum"],
  [/(park|公园)/i, "leisure.park"],
  [/(tourist|attraction|sightseeing|景点|旅游)/i, "tourism"],
  [/(supermarket|grocery|超市)/i, "commercial.supermarket"],
  [/(mall|shopping center|购物中心|商场)/i, "commercial.shopping_mall"],
  [/(gas|fuel|petrol|加油站)/i, "service.vehicle.fuel"],
  [/(charging|充电站)/i, "service.vehicle.charging_station"],
  [/(parking|停车)/i, "parking"],
  [/(airport|机场)/i, "airport"],
  [/(bank|atm|银行|取款机)/i, "service.financial"],
];

const DEFAULT_PLACE_CATEGORIES = [
  "catering",
  "accommodation",
  "commercial",
  "tourism",
  "entertainment",
  "healthcare",
  "education",
  "service",
  "leisure",
  "public_transport",
].join(",");

export class GeoapifyMapsTools {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly language: string;
  private readonly fetchImpl: FetchImplementation;
  private readonly timeoutMs: number;

  constructor(options: GeoapifyMapsToolsOptions = {}) {
    this.apiKey = options.apiKey ?? process.env.GEOAPIFY_API_KEY ?? "";
    this.baseUrl = (options.baseUrl ?? process.env.GEOAPIFY_API_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.language = options.language ?? process.env.GEOAPIFY_LANGUAGE ?? "zh";
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.timeoutMs = options.timeoutMs ?? Number(process.env.GEOAPIFY_TIMEOUT_MS ?? 15_000);

    if (!this.apiKey) {
      throw new Error("Geoapify API Key is required (set GEOAPIFY_API_KEY)");
    }
    if (typeof this.fetchImpl !== "function") {
      throw new Error("This server requires Node.js 18 or newer with global fetch support");
    }
  }

  private async request<T>(
    path: string,
    params: Record<string, string | number | boolean | undefined> = {},
    init: RequestInit = {}
  ): Promise<T> {
    const url = new URL(path, `${this.baseUrl}/`);
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) url.searchParams.set(key, String(value));
    });
    url.searchParams.set("apiKey", this.apiKey);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(url, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
        signal: controller.signal,
      });
      const responseText = await response.text();
      let data: any = {};
      if (responseText) {
        try {
          data = JSON.parse(responseText);
        } catch {
          if (response.ok) throw new Error("Geoapify returned an invalid JSON response");
        }
      }
      if (!response.ok) {
        const message = data?.message ?? data?.error ?? response.statusText;
        throw new Error(`Geoapify request failed (${response.status}): ${message}`);
      }
      return data as T;
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new Error(`Geoapify request timed out after ${this.timeoutMs} ms`);
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  private logAndRethrow(operation: string, error: unknown): never {
    Logger.error(`Geoapify ${operation} error:`, error);
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${operation}失败: ${detail}`);
  }

  private validateCoordinates(lat: number, lng: number): void {
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      throw new Error("无效坐标：纬度须在 -90 到 90，经度须在 -180 到 180");
    }
  }

  private featureCoordinates(feature: GeoapifyFeature): { lat: number; lng: number } | null {
    const props = feature.properties ?? {};
    const lat = Number(props.lat ?? feature.geometry?.coordinates?.[1]);
    const lng = Number(props.lon ?? feature.geometry?.coordinates?.[0]);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return { lat, lng };
  }

  private geocodeResult(properties: Record<string, any>): GeocodeResult | null {
    const lat = Number(properties.lat);
    const lng = Number(properties.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return {
      lat,
      lng,
      formatted_address: properties.formatted ?? properties.address_line1,
      place_id: properties.place_id,
      attribution: ATTRIBUTION,
    };
  }

  private async geocodeAddress(address: string): Promise<GeocodeResult> {
    const response = await this.request<GeoapifyGeocodeResponse>("/v1/geocode/search", {
      text: address,
      format: "json",
      limit: 1,
      lang: this.language,
    });
    const result = response.results?.[0] ? this.geocodeResult(response.results[0]) : null;
    if (!result) throw new Error(`找不到地址：${address}`);
    return result;
  }

  private parseCoordinates(coordString: string): GeocodeResult {
    const parts = coordString.split(",").map((part) => Number(part.trim()));
    if (parts.length !== 2 || parts.some((part) => !Number.isFinite(part))) {
      throw new Error("无效坐标格式，请使用“纬度,经度”格式");
    }
    this.validateCoordinates(parts[0], parts[1]);
    return { lat: parts[0], lng: parts[1], attribution: ATTRIBUTION };
  }

  private looksLikeCoordinates(value: string): boolean {
    return /^\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*,\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*$/.test(value);
  }

  private async resolveLocation(value: string): Promise<ResolvedLocation> {
    const result = this.looksLikeCoordinates(value) ? this.parseCoordinates(value) : await this.geocodeAddress(value);
    return { ...result, source: value, formatted_address: result.formatted_address ?? value };
  }

  private categoryForKeyword(keyword?: string): string | null {
    if (!keyword?.trim()) return DEFAULT_PLACE_CATEGORIES;
    return CATEGORY_RULES.find(([pattern]) => pattern.test(keyword))?.[1] ?? null;
  }

  private normalizePlace(feature: GeoapifyFeature, keyword?: string): PlaceResult | null {
    const props = feature.properties ?? {};
    const location = this.featureCoordinates(feature);
    if (!location) return null;
    const providerNotes: string[] = [];
    providerNotes.push("Geoapify/OSM does not provide Google-style user ratings, review counts, or live open-now status.");
    return {
      name: props.name ?? props.address_line1 ?? props.formatted ?? keyword ?? "Unnamed place",
      place_id: props.place_id ?? "",
      formatted_address: props.formatted ?? [props.address_line1, props.address_line2].filter(Boolean).join(", "),
      geometry: { location },
      rating: null,
      user_ratings_total: null,
      opening_hours: { open_now: null },
      categories: Array.isArray(props.categories) ? props.categories : undefined,
      distance: Number.isFinite(Number(props.distance)) ? Number(props.distance) : undefined,
      attribution: ATTRIBUTION,
      provider_note: providerNotes.join(" "),
    };
  }

  async searchNearbyPlaces(params: SearchParams): Promise<PlaceResult[]> {
    try {
      this.validateCoordinates(params.location.lat, params.location.lng);
      const radius = Math.max(1, Math.min(Math.round(params.radius ?? 1000), 50_000));
      const keyword = params.keyword?.trim();
      const categories = this.categoryForKeyword(keyword);
      let features: GeoapifyFeature[] = [];

      if (categories) {
        const response = await this.request<GeoapifyFeatureCollection>("/v2/places", {
          categories,
          filter: `circle:${params.location.lng},${params.location.lat},${radius}`,
          bias: `proximity:${params.location.lng},${params.location.lat}`,
          limit: 20,
          lang: this.language,
        });
        features = response.features ?? [];
      } else {
        const response = await this.request<GeoapifyGeocodeResponse>("/v1/geocode/search", {
          text: keyword,
          type: "amenity",
          filter: `circle:${params.location.lng},${params.location.lat},${radius}`,
          bias: `proximity:${params.location.lng},${params.location.lat}`,
          format: "json",
          limit: 20,
          lang: this.language,
        });
        features = (response.results ?? []).map((properties) => ({ properties }));
      }

      // Geoapify has no equivalent user-rating or dynamic open-now fields. Keep the
      // parameters for MCP input compatibility, but return honest null values.
      void params.openNow;
      void params.minRating;
      return features.map((feature) => this.normalizePlace(feature, keyword)).filter((place): place is PlaceResult => place !== null);
    } catch (error) {
      this.logAndRethrow("附近地点搜索", error);
    }
  }

  async getPlaceDetails(placeId: string): Promise<any> {
    try {
      const response = await this.request<GeoapifyFeatureCollection>("/v2/place-details", {
        id: placeId,
        features: "details",
        lang: this.language,
      });
      const feature = response.features?.find((item) => item.properties?.feature_type === "details") ?? response.features?.[0];
      if (!feature) throw new Error(`找不到地点详情：${placeId}`);
      const props = feature.properties ?? {};
      const location = this.featureCoordinates(feature);
      const contact = props.contact ?? {};
      const raw = props.datasource?.raw ?? {};
      const openingHours = props.opening_hours ?? raw.opening_hours;
      return {
        name: props.name ?? props.address_line1 ?? props.formatted,
        place_id: props.place_id ?? placeId,
        formatted_address: props.formatted ?? [props.address_line1, props.address_line2].filter(Boolean).join(", "),
        geometry: location ? { location } : undefined,
        rating: null,
        user_ratings_total: null,
        opening_hours: { open_now: null, text: openingHours ?? null },
        formatted_phone_number: contact.phone ?? raw.phone ?? null,
        website: props.website ?? contact.website ?? raw.website ?? null,
        price_level: null,
        reviews: [],
        categories: props.categories ?? [],
        attribution: ATTRIBUTION,
        provider_note: "Geoapify/OSM place details do not include Google user ratings, reviews, price level, or live open-now status.",
      };
    } catch (error) {
      this.logAndRethrow("地点详情查询", error);
    }
  }

  async getLocation(center: { value: string; isCoordinates: boolean }): Promise<GeocodeResult> {
    try {
      return center.isCoordinates ? this.parseCoordinates(center.value) : await this.geocodeAddress(center.value);
    } catch (error) {
      this.logAndRethrow("位置解析", error);
    }
  }

  async geocode(address: string): Promise<{
    location: { lat: number; lng: number };
    formatted_address: string;
    place_id: string;
    attribution: string;
  }> {
    try {
      const result = await this.geocodeAddress(address);
      return {
        location: { lat: result.lat, lng: result.lng },
        formatted_address: result.formatted_address ?? "",
        place_id: result.place_id ?? "",
        attribution: ATTRIBUTION,
      };
    } catch (error) {
      this.logAndRethrow("地址地理编码", error);
    }
  }

  async reverseGeocode(latitude: number, longitude: number): Promise<{
    formatted_address: string;
    place_id: string;
    address_components: any[];
    attribution: string;
  }> {
    try {
      this.validateCoordinates(latitude, longitude);
      const response = await this.request<GeoapifyGeocodeResponse>("/v1/geocode/reverse", {
        lat: latitude,
        lon: longitude,
        format: "json",
        limit: 1,
        lang: this.language,
      });
      const props = response.results?.[0];
      if (!props) throw new Error("找不到该坐标对应的地址");
      const componentKeys = ["housenumber", "street", "district", "city", "county", "state", "postcode", "country"];
      return {
        formatted_address: props.formatted ?? props.address_line1 ?? "",
        place_id: props.place_id ?? "",
        address_components: componentKeys
          .filter((key) => props[key] !== undefined)
          .map((key) => ({ long_name: String(props[key]), short_name: String(props[key]), types: [key] })),
        attribution: ATTRIBUTION,
      };
    } catch (error) {
      this.logAndRethrow("反向地理编码", error);
    }
  }

  private routingMode(mode: TravelMode): "drive" | "walk" | "bicycle" | "transit" {
    return ({ driving: "drive", walking: "walk", bicycling: "bicycle", transit: "transit" } as const)[mode];
  }

  private distanceInMeters(distance: number, units?: string): number {
    const normalized = (units ?? "meters").toLowerCase();
    if (normalized.startsWith("kilometer")) return Math.round(distance * 1000);
    if (normalized.startsWith("mile")) return Math.round(distance * 1609.344);
    return Math.round(distance);
  }

  private formatDistance(meters: number): string {
    return meters >= 1000 ? `${(meters / 1000).toFixed(meters >= 10_000 ? 0 : 1)} km` : `${Math.round(meters)} m`;
  }

  private formatDuration(seconds: number): string {
    const minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours} hr ${remainder} min` : `${hours} hr`;
  }

  async calculateDistanceMatrix(
    origins: string[],
    destinations: string[],
    mode: TravelMode = "driving"
  ): Promise<{
    distances: any[][];
    durations: any[][];
    origin_addresses: string[];
    destination_addresses: string[];
    attribution: string;
  }> {
    try {
      if (!origins.length || !destinations.length) throw new Error("起点和终点列表不能为空");
      const cache = new Map<string, Promise<ResolvedLocation>>();
      const resolveCached = (value: string) => {
        if (!cache.has(value)) cache.set(value, this.resolveLocation(value));
        return cache.get(value)!;
      };
      const [resolvedOrigins, resolvedDestinations] = await Promise.all([
        Promise.all(origins.map(resolveCached)),
        Promise.all(destinations.map(resolveCached)),
      ]);
      const response = await this.request<any>("/v1/routematrix", {}, {
        method: "POST",
        body: JSON.stringify({
          mode: this.routingMode(mode),
          sources: resolvedOrigins.map((item) => ({ location: [item.lng, item.lat] })),
          targets: resolvedDestinations.map((item) => ({ location: [item.lng, item.lat] })),
          units: "metric",
          type: "balanced",
        }),
      });
      const matrix = response.sources_to_targets ?? [];
      const distances = resolvedOrigins.map((_, sourceIndex) =>
        resolvedDestinations.map((__, targetIndex) => {
          const value = matrix[sourceIndex]?.[targetIndex]?.distance;
          return Number.isFinite(value) ? { value: Math.round(value), text: this.formatDistance(value) } : null;
        })
      );
      const durations = resolvedOrigins.map((_, sourceIndex) =>
        resolvedDestinations.map((__, targetIndex) => {
          const value = matrix[sourceIndex]?.[targetIndex]?.time;
          return Number.isFinite(value) ? { value: Math.round(value), text: this.formatDuration(value) } : null;
        })
      );
      return {
        distances,
        durations,
        origin_addresses: resolvedOrigins.map((item) => item.formatted_address ?? item.source),
        destination_addresses: resolvedDestinations.map((item) => item.formatted_address ?? item.source),
        attribution: ATTRIBUTION,
      };
    } catch (error) {
      this.logAndRethrow("距离矩阵计算", error);
    }
  }

  async getDirections(
    origin: string,
    destination: string,
    mode: TravelMode = "driving",
    departureTime?: Date,
    arrivalTime?: Date
  ): Promise<{
    routes: any[];
    summary: string;
    total_distance: { value: number; text: string };
    total_duration: { value: number; text: string };
    arrival_time: string;
    departure_time: string;
    attribution: string;
    provider_note: string;
  }> {
    try {
      const [resolvedOrigin, resolvedDestination] = await Promise.all([
        this.resolveLocation(origin),
        this.resolveLocation(destination),
      ]);
      const response = await this.request<any>("/v1/routing", {
        waypoints: `${resolvedOrigin.lat},${resolvedOrigin.lng}|${resolvedDestination.lat},${resolvedDestination.lng}`,
        mode: this.routingMode(mode),
        format: "json",
        details: "instruction_details",
        units: "metric",
        lang: "en",
        traffic: mode === "driving" ? "approximated" : undefined,
      });
      const route = response.results?.[0];
      if (!route) throw new Error("找不到路线");
      const seconds = Math.round(Number(route.time));
      if (!Number.isFinite(seconds)) throw new Error("路线响应缺少有效时长");
      const meters = this.distanceInMeters(Number(route.distance), route.distance_units);
      if (!Number.isFinite(meters)) throw new Error("路线响应缺少有效距离");

      if (departureTime && Number.isNaN(departureTime.getTime())) throw new Error("departure_time 不是有效的 ISO 时间");
      if (arrivalTime && Number.isNaN(arrivalTime.getTime())) throw new Error("arrival_time 不是有效的 ISO 时间");
      const arrival = arrivalTime ?? new Date((departureTime ?? new Date()).getTime() + seconds * 1000);
      const departure = arrivalTime ? new Date(arrivalTime.getTime() - seconds * 1000) : (departureTime ?? new Date());

      return {
        routes: [route],
        summary: `${resolvedOrigin.formatted_address ?? origin} → ${resolvedDestination.formatted_address ?? destination}`,
        total_distance: { value: meters, text: this.formatDistance(meters) },
        total_duration: { value: seconds, text: this.formatDuration(seconds) },
        arrival_time: arrival.toISOString(),
        departure_time: departure.toISOString(),
        attribution: ATTRIBUTION,
        provider_note: "Geoapify does not use departure_time or arrival_time to select this route; the returned timestamps are derived from the route duration.",
      };
    } catch (error) {
      this.logAndRethrow("路线查询", error);
    }
  }

  async getElevation(locations: Array<{ latitude: number; longitude: number }>): Promise<Array<{
    elevation: number;
    location: { lat: number; lng: number };
    attribution: string;
  }>> {
    try {
      if (!locations.length) throw new Error("位置列表不能为空");
      locations.forEach((item) => this.validateCoordinates(item.latitude, item.longitude));
      const response = await this.request<any>("/v1/geodata/elevation", {}, {
        method: "POST",
        body: JSON.stringify({
          format: "json",
          units: "metric",
          locations: locations.map((item) => ({ lat: item.latitude, lon: item.longitude })),
        }),
      });
      const results = response.results ?? [];
      return locations.map((input, index) => {
        const result = results[index] ?? {};
        const resultLocation = result.location ?? {};
        return {
          elevation: Number(result.elevation),
          location: {
            lat: Number(resultLocation.lat ?? resultLocation[1] ?? input.latitude),
            lng: Number(resultLocation.lon ?? resultLocation.lng ?? resultLocation[0] ?? input.longitude),
          },
          attribution: ATTRIBUTION,
        };
      });
    } catch (error) {
      this.logAndRethrow("海拔查询", error);
    }
  }
}
