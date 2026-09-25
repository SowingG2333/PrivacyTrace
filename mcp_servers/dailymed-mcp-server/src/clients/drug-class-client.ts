import { BaseClient } from "./base-client.js";
import type { DrugClass, PaginatedSPLResponse } from "../types/index.js";
import { createSPLDocumentFromItem } from "../utils/spl-utils.js";
import { validatePaginationParams } from "../utils/pagination-utils.js";

export interface DrugClassSearchParams {
  drug_class_code?: string;
  drug_class_coding_system?: string;
  class_code_type?: "all" | "epc" | "moa" | "pe" | "ci";
  class_name?: string;
  unii_code?: string;
  page?: number;
  pageSize?: number;
}

export interface PaginatedDrugClassResponse {
  data: DrugClass[];
  pagination: {
    page: number;
    pageSize: number;
    totalResults: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  };
}

export class DrugClassClient extends BaseClient {
  async getAllDrugClasses(page: number = 1, pageSize: number = 100): Promise<PaginatedDrugClassResponse> {
    return this.searchDrugClassesAdvanced({ page, pageSize });
  }

  async searchDrugClasses(params: any): Promise<DrugClass[]> {
    const response = await this.searchDrugClassesAdvanced(params);
    return response.data;
  }

  async searchDrugClassesAdvanced(params: DrugClassSearchParams = {}): Promise<PaginatedDrugClassResponse> {
    const { page = 1, pageSize = 100, ...searchParams } = params;
    
    validatePaginationParams(page, pageSize, 100);

    try {
      const queryParams: any = {
        page,
        pagesize: Math.min(pageSize, 100), // API max is 100
      };

      // Add search filters
      if (searchParams.drug_class_code) queryParams.drug_class_code = searchParams.drug_class_code;
      if (searchParams.drug_class_coding_system) queryParams.drug_class_coding_system = searchParams.drug_class_coding_system;
      if (searchParams.class_code_type) queryParams.class_code_type = searchParams.class_code_type;
      if (searchParams.class_name) queryParams.class_name = searchParams.class_name;
      if (searchParams.unii_code) queryParams.unii_code = searchParams.unii_code;

      const response = await this.client.get("/drugclasses.json", {
        params: queryParams,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const drugClasses = response.data.data.map((item: any) => ({
          drugClassName: item.name,
          drugClassCode: item.code,
          drugClassCodingSystem: item.codingSystem,
          classCodeType: item.type,
          uniiCode: item.unii_code,
        }));

        // Extract pagination metadata from API response
        const totalResults = response.data.metadata?.total_elements || drugClasses.length;
        const totalPages = Math.ceil(totalResults / pageSize);

        return {
          data: drugClasses,
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
        throw new Error("Unexpected response structure for drug class search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search drug classes: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async searchDrugsByPharmacologicClass(drugClassCode: string, codingSystem?: string, page: number = 1, pageSize: number = 25): Promise<PaginatedSPLResponse> {
    if (!drugClassCode || typeof drugClassCode !== "string") {
      throw new Error("Valid drug class code is required");
    }

    validatePaginationParams(page, pageSize, 100);

    try {
      const params: any = { 
        drug_class_code: drugClassCode,
        drug_class_coding_system: codingSystem || "2.16.840.1.113883.6.345",
        pagesize: pageSize,
        page: page
      };
      
      const response = await this.client.get("/spls.json", {
        params: params,
      });

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        const splDocs = response.data.data.map((item: any) => createSPLDocumentFromItem(item, this.mappingService));
        
        // DailyMed API handles pagination for us
        const totalResults = response.data.metadata?.total_elements || splDocs.length;
        const totalPages = Math.ceil(totalResults / pageSize);
        
        return {
          data: splDocs,
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
        throw new Error("Unexpected response structure for drug class search");
      }
    } catch (error) {
      throw new Error(
        `Failed to search drugs by pharmacologic class: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }
}