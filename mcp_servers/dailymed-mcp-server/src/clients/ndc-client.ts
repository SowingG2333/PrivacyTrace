import { BaseClient } from "./base-client.js";
import type { NDC } from "../types/index.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface PaginatedNDCResponse {
  data: NDC[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class NDCClient extends BaseClient {
  async getAllNDCs(page: number = 1, pageSize: number = 25): Promise<PaginatedNDCResponse> {
    validatePaginationParams(page, pageSize, 100);

    try {
      const response = await this.client.get("/ndcs.json", {
        params: {
          page,
          pagesize: Math.min(pageSize, 100), // API max is 100
        },
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const ndcs = response.data.data.map((item: any) => ({
          ndc: item.ndc,
          packageNdc: item.package_ndc || item.packageNdc,
          productNdc: item.product_ndc || item.productNdc,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || ndcs.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: ndcs,
          pagination: {
            page,
            pageSize,
            totalResults,
            totalPages,
            hasNextPage: page < totalPages,
            hasPreviousPage: page > 1,
          },
        };
      } else {
        throw new Error("Unexpected response structure for NDCs");
      }
    } catch (error) {
      throw new Error(
        `Failed to fetch NDCs: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }
}