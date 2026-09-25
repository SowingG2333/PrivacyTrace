import { z } from "zod";
import { PlacesSearcher } from "../../services/PlacesSearcher.js";

const NAME = "search_nearby";
const DESCRIPTION = "Search Geoapify/OSM places near a location; rating and open-now inputs are retained for compatibility but are not provider-supported filters";

const SCHEMA = {
  center: z.object({
    value: z.string().describe("Address, landmark name, or coordinates (coordinate format: lat,lng)"),
    isCoordinates: z.boolean().default(false).describe("Whether the value is coordinates"),
  }).describe("Search center point"),
  keyword: z.string().optional().describe("Search keyword (e.g., restaurant, cafe, hotel)"),
  radius: z.number().default(1000).describe("Search radius in meters"),
  openNow: z.boolean().default(false).describe("Compatibility input; Geoapify does not expose reliable live open-now filtering"),
  minRating: z.number().min(0).max(5).optional().describe("Compatibility input; Geoapify does not provide Google-style user ratings"),
};

export type SearchNearbyParams = z.infer<z.ZodObject<typeof SCHEMA>>;

let placesSearcher: PlacesSearcher | null = null;

async function ACTION(params: SearchNearbyParams): Promise<{ content: any[]; isError?: boolean }> {
  try {
    if (!placesSearcher) {
      placesSearcher = new PlacesSearcher();
    }
    const result = await placesSearcher.searchNearby(params);

    if (!result.success) {
      return {
        content: [{ type: "text", text: result.error || "搜尋失敗" }],
        isError: true,
      };
    }

    return {
      content: [
        {
          type: "text",
          text: `location: ${JSON.stringify(result.location, null, 2)}\n` + JSON.stringify(result.data, null, 2),
        },
      ],
      isError: false,
    };
  } catch (error: any) {
    const errorMessage = error instanceof Error ? error.message : JSON.stringify(error);
    return {
      isError: true,
      content: [{ type: "text", text: `搜尋附近地點錯誤: ${errorMessage}` }],
    };
  }
}

export const SearchNearby = {
  NAME,
  DESCRIPTION,
  SCHEMA,
  ACTION,
};
