import { z } from "zod";
import { PlacesSearcher } from "../../services/PlacesSearcher.js";

const NAME = "get_place_details";
const DESCRIPTION = "Get Geoapify place details including address, coordinates, contact details, website, categories, and tagged opening hours";

const SCHEMA = {
  placeId: z.string().describe("Geoapify place ID returned by search_nearby or maps_geocode"),
};

export type PlaceDetailsParams = z.infer<z.ZodObject<typeof SCHEMA>>;

let placesSearcher: PlacesSearcher | null = null;

async function ACTION(params: PlaceDetailsParams): Promise<{ content: any[]; isError?: boolean }> {
  try {
    if (!placesSearcher) {
      placesSearcher = new PlacesSearcher();
    }
    const result = await placesSearcher.getPlaceDetails(params.placeId);

    if (!result.success) {
      return {
        content: [{ type: "text", text: result.error || "獲取詳細資訊失敗" }],
        isError: true,
      };
    }

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(result.data, null, 2),
        },
      ],
      isError: false,
    };
  } catch (error: any) {
    const errorMessage = error instanceof Error ? error.message : JSON.stringify(error);
    return {
      isError: true,
      content: [{ type: "text", text: `獲取地點詳細資訊錯誤: ${errorMessage}` }],
    };
  }
}

export const PlaceDetails = {
  NAME,
  DESCRIPTION,
  SCHEMA,
  ACTION,
};
