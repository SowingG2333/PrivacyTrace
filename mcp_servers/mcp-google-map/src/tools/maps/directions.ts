import { z } from "zod";
import { PlacesSearcher } from "../../services/PlacesSearcher.js";

const NAME = "maps_directions";
const DESCRIPTION = "Get Geoapify turn-by-turn directions; supplied departure or arrival times label derived estimates but do not change route selection";

const SCHEMA = {
  origin: z.string().describe("Starting point address or coordinates"),
  destination: z.string().describe("Destination address or coordinates"),
  mode: z.enum(["driving", "walking", "bicycling", "transit"]).default("driving").describe("Travel mode for directions"),
  departure_time: z.string().optional().describe("Reference departure time (ISO); Geoapify does not use it to select the route"),
  arrival_time: z.string().optional().describe("Reference arrival time (ISO); Geoapify derives departure from route duration"),
};

export type DirectionsParams = z.infer<z.ZodObject<typeof SCHEMA>>;

let placesSearcher: PlacesSearcher | null = null;

async function ACTION(params: DirectionsParams): Promise<{ content: any[]; isError?: boolean }> {
  try {
    if (!placesSearcher) {
      placesSearcher = new PlacesSearcher();
    }
    const result = await placesSearcher.getDirections(
      params.origin,
      params.destination,
      params.mode,
      params.departure_time,
      params.arrival_time
    );

    if (!result.success) {
      return {
        content: [{ type: "text", text: result.error || "獲取路線指引失敗" }],
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
      content: [{ type: "text", text: `獲取路線指引錯誤: ${errorMessage}` }],
    };
  }
}

export const Directions = {
  NAME,
  DESCRIPTION,
  SCHEMA,
  ACTION,
};
