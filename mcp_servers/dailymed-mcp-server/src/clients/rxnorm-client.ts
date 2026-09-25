import { BaseClient } from "./base-client.js";
import type { RxCUI } from "../types/index.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface RxCUISearchParams {
  rxtty?: "PSN" | "SBD" | "SCD" | "BPCK" | "GPCK" | "SY";
  rxstring?: string;
  rxcui?: string;
  page?: number;
  pageSize?: number;
}

export interface PaginatedRxCUIResponse {
  data: RxCUI[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class RxNormClient extends BaseClient {
  async getAllRxCUIs(page: number = 1, pageSize: number = 25): Promise<PaginatedRxCUIResponse> {
    return this.searchRxCUIs({ page, pageSize });
  }

  async searchRxCUIs(params: RxCUISearchParams = {}): Promise<PaginatedRxCUIResponse> {
    const { page = 1, pageSize = 25, ...searchParams } = params;
    
    validatePaginationParams(page, pageSize, 100);

    try {
      const queryParams: any = {
        page,
        pagesize: Math.min(pageSize, 100), // API max is 100
      };

      // Add search filters
      if (searchParams.rxstring) queryParams.rxstring = searchParams.rxstring;
      if (searchParams.rxcui) queryParams.rxcui = searchParams.rxcui;
      if (searchParams.rxtty) queryParams.rxtty = searchParams.rxtty;

      const response = await this.client.get("/rxcuis.json", {
        params: queryParams,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const rxcuis = response.data.data.map((item: any) => ({
          rxcui: item.rxcui,
          drugName: item.rxstring || item.drug_name || item.drugName,
          termType: item.rxtty,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || rxcuis.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: rxcuis,
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
        throw new Error("Unexpected response structure for RxCUI search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search RxCUIs: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }
}