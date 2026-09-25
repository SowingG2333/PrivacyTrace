import { BaseClient } from "./base-client.js";
import type { UNII } from "../types/index.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface UNIISearchParams {
  active_moiety?: string;
  drug_class_code?: string;
  drug_class_coding_system?: string;
  rxcui?: string;
  unii_code?: string;
  page?: number;
  pageSize?: number;
}

export interface PaginatedUNIIResponse {
  data: UNII[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class UNIIClient extends BaseClient {
  async getAllUNIIs(page: number = 1, pageSize: number = 25): Promise<PaginatedUNIIResponse> {
    return this.searchUNIIs({ page, pageSize });
  }

  async searchUNIIs(params: UNIISearchParams = {}): Promise<PaginatedUNIIResponse> {
    const { page = 1, pageSize = 25, ...searchParams } = params;
    
    validatePaginationParams(page, pageSize, 100);

    try {
      const queryParams: any = {
        page,
        pagesize: Math.min(pageSize, 100), // API max is 100
      };

      // Add search filters
      if (searchParams.active_moiety) queryParams.active_moiety = searchParams.active_moiety;
      if (searchParams.drug_class_code) queryParams.drug_class_code = searchParams.drug_class_code;
      if (searchParams.drug_class_coding_system) queryParams.drug_class_coding_system = searchParams.drug_class_coding_system;
      if (searchParams.rxcui) queryParams.rxcui = searchParams.rxcui;
      if (searchParams.unii_code) queryParams.unii_code = searchParams.unii_code;

      const response = await this.client.get("/uniis.json", {
        params: queryParams,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const uniis = response.data.data.map((item: any) => ({
          unii: item.unii,
          substanceName: item.substance_name || item.substanceName,
          uniiType: item.unii_type || item.uniiType,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || uniis.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: uniis,
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
        throw new Error("Unexpected response structure for UNII search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search UNIIs: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }
}