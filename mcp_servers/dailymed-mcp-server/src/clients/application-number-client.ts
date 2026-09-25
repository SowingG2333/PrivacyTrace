import { BaseClient } from "./base-client.js";
import type { ApplicationNumber } from "../types/index.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface ApplicationNumberSearchParams {
  application_number?: string;
  marketing_category_code?: string;
  setid?: string;
  page?: number;
  pageSize?: number;
}

export interface PaginatedApplicationNumberResponse {
  data: ApplicationNumber[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class ApplicationNumberClient extends BaseClient {
  async getAllApplicationNumbers(page: number = 1, pageSize: number = 100): Promise<PaginatedApplicationNumberResponse> {
    return this.searchApplicationNumbersAdvanced({ page, pageSize });
  }

  async searchApplicationNumbers(params: any): Promise<ApplicationNumber[]> {
    const response = await this.searchApplicationNumbersAdvanced(params);
    return response.data;
  }

  async searchApplicationNumbersAdvanced(params: ApplicationNumberSearchParams = {}): Promise<PaginatedApplicationNumberResponse> {
    const { page = 1, pageSize = 100, ...searchParams } = params;
    
    validatePaginationParams(page, pageSize, 100);

    try {
      const queryParams: any = {
        page,
        pagesize: Math.min(pageSize, 100), // API max is 100
      };

      // Add search filters
      if (searchParams.application_number) queryParams.application_number = searchParams.application_number;
      if (searchParams.marketing_category_code) queryParams.marketing_category_code = searchParams.marketing_category_code;
      if (searchParams.setid) queryParams.setid = searchParams.setid;

      const response = await this.client.get("/applicationnumbers.json", {
        params: queryParams,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const applicationNumbers = response.data.data.map((item: any) => ({
          applicationNumber: item.application_number || item.applicationNumber,
          applicationNumberType: item.application_number_type || item.applicationNumberType,
          marketingCategoryCode: item.marketing_category_code || item.marketingCategoryCode,
          setId: item.setid,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || applicationNumbers.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: applicationNumbers,
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
        throw new Error("Unexpected response structure for application number search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search application numbers: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }
}