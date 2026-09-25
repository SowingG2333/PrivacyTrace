import { BaseClient } from "./base-client.js";
import type { DrugName } from "../types/index.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface DrugNameSearchParams {
  drug_name?: string;
  name_type?: "g" | "generic" | "b" | "brand" | "both";
  manufacturer?: string;
  page?: number;
  pageSize?: number;
}

export interface PaginatedDrugNameResponse {
  data: DrugName[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class DrugNameClient extends BaseClient {
  async searchDrugNames(query: string): Promise<DrugName[]> {
    if (!query || typeof query !== "string") {
      throw new Error("Valid query string is required");
    }

    const response = await this.searchDrugNamesAdvanced({ drug_name: query });
    return response.data;
  }

  async searchDrugNamesAdvanced(params: DrugNameSearchParams = {}): Promise<PaginatedDrugNameResponse> {
    const { page = 1, pageSize = 100, ...searchParams } = params;
    
    validatePaginationParams(page, pageSize, 100);

    try {
      const queryParams: any = {
        page,
        pagesize: Math.min(pageSize, 100), // API max is 100
      };

      // Add search filters
      if (searchParams.drug_name) queryParams.drug_name = searchParams.drug_name;
      if (searchParams.name_type) queryParams.name_type = searchParams.name_type;
      if (searchParams.manufacturer) queryParams.manufacturer = searchParams.manufacturer;

      const response = await this.client.get("/drugnames.json", {
        params: queryParams,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const drugNames = response.data.data.map((item: any) => ({
          drugName: item.drug_name,
          routeOfAdministration: item.route_of_administration,
          activeIngredient: item.active_ingredient,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || drugNames.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: drugNames,
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
        throw new Error("Unexpected response structure for drug name search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search drug names: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async getAllDrugNames(page: number = 1, pageSize: number = 100): Promise<PaginatedDrugNameResponse> {
    return this.searchDrugNamesAdvanced({ page, pageSize });
  }
}