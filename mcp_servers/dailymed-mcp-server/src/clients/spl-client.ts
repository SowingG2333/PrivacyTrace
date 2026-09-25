import { parseString } from "xml2js";
import { BaseClient } from "./base-client.js";
import type { DrugNameClient } from "./drug-name-client.js";
import type { 
  SPLDocument, 
  SPLSection, 
  PaginatedSPLResponse, 
  AdvancedSPLSearchParams 
} from "../types/index.js";
import { createSPLDocumentFromItem } from "../utils/spl-utils.js";
import { paginateResults, validatePaginationParams } from "../utils/pagination-utils.js";
import { extractTextContent, extractListContent, extractTableContent } from "../utils/text-extraction-utils.js";

export class SPLClient extends BaseClient {
  private drugNameClient?: DrugNameClient;

  setDrugNameClient(drugNameClient: DrugNameClient) {
    this.drugNameClient = drugNameClient;
  }
  async searchSPLs(params: AdvancedSPLSearchParams): Promise<PaginatedSPLResponse> {
    const { page = 1, pageSize = 25, query, ...advancedParams } = params;

    validatePaginationParams(page, pageSize);

    // Check if this is a simple query or advanced search
    const hasAdvancedParams = Object.values(advancedParams).some(value => value !== undefined);
    const isSimpleQuery = query && !hasAdvancedParams;

    if (!isSimpleQuery && !hasAdvancedParams) {
      throw new Error("Either 'query' or at least one advanced parameter is required");
    }

    try {
      if (isSimpleQuery) {
        // Use the existing drug name search approach
        return await this.searchSPLsByDrugName(query!, page, pageSize);
      } else {
        // Use advanced DailyMed SPLs API search
        return await this.searchSPLsAdvanced(params);
      }
    } catch (error) {
      throw new Error(
        `Failed to search SPLs: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  private async searchSPLsByDrugName(query: string, page: number, pageSize: number): Promise<PaginatedSPLResponse> {
    // First search for drugs by name or active ingredient
    const drugs = await this.searchDrugNames(query);
    
    // If no drugs found, return empty paginated response
    if (drugs.length === 0) {
      return {
        data: [],
        pagination: {
          page: 1,
          pageSize,
          totalResults: 0,
          totalPages: 0,
          hasNextPage: false,
          hasPreviousPage: false,
        },
      };
    }

    // Collect all unique drug names (including active ingredients)
    const drugQueries = new Set<string>();
    drugs.forEach(drug => {
      drugQueries.add(drug.drugName);
      if (drug.activeIngredient) {
        drugQueries.add(drug.activeIngredient);
      }
    });

    // Search for SPLs using each drug name/ingredient
    const allSPLs = new Map<string, SPLDocument>();
    
    for (const drugQuery of drugQueries) {
      try {
        const response = await this.client.get("/spls.json", {
          params: { drug_name: drugQuery },
        });

        if (
          response.data &&
          response.data.data &&
          Array.isArray(response.data.data)
        ) {
          response.data.data.forEach((item: any) => {
            const setId = item.setid;
            
            // Avoid duplicates by using setId as key
            if (!allSPLs.has(setId)) {
              const splDoc = createSPLDocumentFromItem(item, this.mappingService);
              allSPLs.set(setId, splDoc);
            }
          });
        }
      } catch (error) {
        // Continue with other drug queries if one fails
        console.error(`Failed to search SPLs for "${drugQuery}": ${error instanceof Error ? error.message : "Unknown error"}`);
      }
    }

    // Convert to array and apply pagination
    const allSPLsArray = Array.from(allSPLs.values());
    return paginateResults(allSPLsArray, page, pageSize);
  }

  private async searchSPLsAdvanced(params: AdvancedSPLSearchParams): Promise<PaginatedSPLResponse> {
    const { page = 1, pageSize = 25 } = params;

    // Build query parameters for DailyMed API
    const queryParams: any = {};
    
    // API pagination (DailyMed has max 100 per page)
    const apiPageSize = Math.min(pageSize, 100);
    queryParams.pagesize = apiPageSize;
    queryParams.page = page;

    // Add all advanced search parameters
    if (params.application_number) queryParams.application_number = params.application_number;
    if (params.boxed_warning !== undefined) queryParams.boxed_warning = params.boxed_warning.toString();
    if (params.dea_schedule_code) queryParams.dea_schedule_code = params.dea_schedule_code;
    if (params.doctype) queryParams.doctype = params.doctype;
    if (params.drug_class_code) queryParams.drug_class_code = params.drug_class_code;
    if (params.drug_class_coding_system) queryParams.drug_class_coding_system = params.drug_class_coding_system;
    if (params.drug_name) queryParams.drug_name = params.drug_name;
    if (params.name_type) queryParams.name_type = params.name_type;
    if (params.labeler) queryParams.labeler = params.labeler;
    if (params.manufacturer) queryParams.manufacturer = params.manufacturer;
    if (params.marketing_category_code) queryParams.marketing_category_code = params.marketing_category_code;
    if (params.ndc) queryParams.ndc = params.ndc;
    if (params.published_date) queryParams.published_date = params.published_date;
    if (params.published_date_comparison) queryParams.published_date_comparison = params.published_date_comparison;
    if (params.rxcui) queryParams.rxcui = params.rxcui;
    if (params.setid) queryParams.setid = params.setid;
    if (params.unii_code) queryParams.unii_code = params.unii_code;

    const response = await this.client.get("/spls.json", {
      params: queryParams,
    });

    if (
      response.data &&
      response.data.data &&
      Array.isArray(response.data.data)
    ) {
      const splDocs = response.data.data.map((item: any) => createSPLDocumentFromItem(item, this.mappingService));
      
      // DailyMed API handles pagination for us in advanced mode
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
      throw new Error("Unexpected response structure for advanced SPL search");
    }
  }

  async getSPLBySetId(setId: string): Promise<SPLDocument> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    return new Promise((resolve, reject) => {
      this.client
        .get(`/spls/${setId}.xml`)
        .then((response) => {
          parseString(response.data, (err, result) => {
            if (err) {
              reject(new Error(`Failed to parse XML: ${err.message}`));
              return;
            }

            try {
              // Extract basic information
              const document = result?.document;
              if (!document) {
                throw new Error("Invalid SPL document structure");
              }

              const title = extractTextContent(document.title) || "No title available";
              const effectiveTime = document.effectiveTime?.[0]?.$?.value || "Unknown";
              const versionNumber = document.versionNumber?.[0]?.$?.value || "1";

              // Extract sections
              const sections: SPLSection[] = [];
              const component = document.component?.[0];
              if (component?.structuredBody?.[0]?.component) {
                const bodyComponents = component.structuredBody[0].component;
                for (const comp of bodyComponents) {
                  const section = comp.section?.[0];
                  if (section) {
                    const sectionTitle = extractTextContent(section.title) || "Untitled Section";
                    
                    let content = "";
                    if (section.text?.[0]) {
                      const textElement = section.text[0];
                      
                      // Handle different content types
                      if (textElement.paragraph) {
                        content = textElement.paragraph.map((p: any) => extractTextContent(p)).join("\n\n");
                      } else if (textElement.list) {
                        content = textElement.list.map((list: any) => {
                          if (list.item) {
                            return extractListContent(list.item);
                          }
                          return "";
                        }).join("\n\n");
                      } else if (textElement.table) {
                        content = textElement.table.map((table: any) => extractTableContent(table)).join("\n\n");
                      } else {
                        content = extractTextContent(textElement);
                      }
                    }
                    
                    if (content.trim()) {
                      sections.push({
                        id: section.id?.[0]?.$?.root,
                        title: sectionTitle,
                        content: content.trim(),
                      });
                    }
                  }
                }
              }

              // Get mapping data for this setId
              const rxNormMappings = this.mappingService.getRxNormMappings(setId);
              const pharmacologicClassMappings = this.mappingService.getPharmacologicClassMappings(setId);

              // Filter out redundant fields from mappings
              const filteredRxNormMappings = rxNormMappings.map(mapping => ({
                rxcui: mapping.rxcui,
                rxstring: mapping.rxstring,
                rxtty: mapping.rxtty,
              }));

              const filteredPharmacologicClassMappings = pharmacologicClassMappings.map(mapping => ({
                pharmaSetId: mapping.pharmaSetId,
                pharmaVersion: mapping.pharmaVersion,
              }));

              resolve({
                setId: setId,
                title: title || "No title available",
                effectiveTime: effectiveTime,
                versionNumber: versionNumber,
                sections: sections,
                spl_medguide: undefined,
                spl_patient_package_insert: undefined,
                spl_product_data_elements: undefined,
                rxNormMappings: filteredRxNormMappings.length > 0 ? filteredRxNormMappings : undefined,
                pharmacologicClassMappings: filteredPharmacologicClassMappings.length > 0 ? filteredPharmacologicClassMappings : undefined,
              });
            } catch (parseError) {
              reject(
                new Error(
                  `Failed to extract data from XML: ${parseError instanceof Error ? parseError.message : "Unknown error"}`,
                ),
              );
            }
          });
        })
        .catch((error) => {
          reject(
            new Error(
              `Failed to fetch SPL: ${error instanceof Error ? error.message : "Unknown error"}`,
            ),
          );
        });
    });
  }

  async getSPLHistory(setId: string): Promise<any[]> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    try {
      const response = await this.client.get(`/spls/${setId}/history.json`);

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        return response.data.data.map((item: any) => ({
          setId: item.setid,
          splVersion: item.spl_version,
          effectiveTime: item.effective_time,
          title: item.title,
        }));
      } else {
        throw new Error("Unexpected response structure for SPL history");
      }
    } catch (error) {
      throw new Error(
        `Failed to fetch SPL history: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async getSPLNDCs(setId: string): Promise<any[]> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    try {
      const response = await this.client.get(`/spls/${setId}/ndcs.json`);

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        return response.data.data.map((item: any) => ({
          ndc: item.ndc,
          packageNdc: item.package_ndc,
          productNdc: item.product_ndc,
        }));
      } else {
        throw new Error("Unexpected response structure for SPL NDCs");
      }
    } catch (error) {
      throw new Error(
        `Failed to fetch SPL NDCs: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async getSPLPackaging(setId: string): Promise<any> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    try {
      const response = await this.client.get(`/spls/${setId}/packaging.json`);

      if (response.data && response.data.data) {
        return response.data.data;
      } else {
        throw new Error("Unexpected response structure for SPL packaging");
      }
    } catch (error) {
      throw new Error(
        `Failed to fetch SPL packaging: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async getSPLMedia(setId: string): Promise<any[]> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    try {
      const response = await this.client.get(`/spls/${setId}/media.json`);

      if (
        response.data &&
        response.data.data &&
        Array.isArray(response.data.data)
      ) {
        return response.data.data.map((item: any) => ({
          url: item.url,
          type: item.type,
          name: item.name,
        }));
      } else {
        throw new Error("Unexpected response structure for SPL media");
      }
    } catch (error) {
      throw new Error(
        `Failed to fetch SPL media: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  }

  async downloadSPLZip(setId: string): Promise<string> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    return `${this.baseURL}/spls/${setId}.zip`;
  }

  async downloadSPLPdf(setId: string): Promise<string> {
    if (!setId || typeof setId !== "string") {
      throw new Error("Valid SET ID is required");
    }

    return `${this.baseURL}/spls/${setId}.pdf`;
  }

  private async searchDrugNames(query: string): Promise<Array<{drugName: string, activeIngredient?: string}>> {
    if (!this.drugNameClient) {
      throw new Error("DrugNameClient not set. Call setDrugNameClient() first.");
    }
    return await this.drugNameClient.searchDrugNames(query);
  }
}