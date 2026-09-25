import { MappingService } from "../mapping-service.js";
import { SPLClient } from "./spl-client.js";
import { DrugNameClient } from "./drug-name-client.js";
import { DrugClassClient } from "./drug-class-client.js";
import { NDCClient } from "./ndc-client.js";
import { RxNormClient } from "./rxnorm-client.js";
import { UNIIClient } from "./unii-client.js";
import { ApplicationNumberClient } from "./application-number-client.js";

// Re-export all types for backward compatibility
export * from "../types/index.js";

export class DailyMedClient {
  private splClient: SPLClient;
  private drugNameClient: DrugNameClient;
  private drugClassClient: DrugClassClient;
  private ndcClient: NDCClient;
  private rxNormClient: RxNormClient;
  private uniiClient: UNIIClient;
  private applicationNumberClient: ApplicationNumberClient;
  private mappingService: MappingService;

  constructor() {
    this.mappingService = new MappingService();
    
    // Initialize specialized clients
    this.drugNameClient = new DrugNameClient(this.mappingService);
    this.drugClassClient = new DrugClassClient(this.mappingService);
    this.ndcClient = new NDCClient(this.mappingService);
    this.rxNormClient = new RxNormClient(this.mappingService);
    this.uniiClient = new UNIIClient(this.mappingService);
    this.applicationNumberClient = new ApplicationNumberClient(this.mappingService);
    this.splClient = new SPLClient(this.mappingService);
    
    // Set up dependencies
    this.splClient.setDrugNameClient(this.drugNameClient);
  }

  // Context method
  async getDailyMedContext() {
    return {
      service: "DailyMed",
      version: "2.0",
      description: "DailyMed is the official provider of FDA label information (package inserts) for approved drug products",
      baseUrl: "https://dailymed.nlm.nih.gov/dailymed/services/v2",
      purpose: "Provide comprehensive, up-to-date drug labeling information",
      contentTypes: [
        "Structured Product Labels (SPLs)",
        "Drug names and active ingredients", 
        "NDC codes",
        "FDA application numbers",
        "Drug classification information",
        "RxNorm mappings",
        "Pharmacologic class mappings"
      ],
      keyFeatures: [
        "Official FDA-submitted drug labeling information",
        "Cross-references with RxNorm and pharmacologic classifications", 
        "Multiple data formats (JSON, XML, PDF)",
        "Free public access with regular updates",
        "Comprehensive search and filtering capabilities"
      ],
      dataFreshness: "Updated daily with new FDA submissions",
      apiCapabilities: [
        "Search SPLs by drug name, manufacturer, NDC, RxCUI, etc.",
        "Advanced filtering with multiple parameters",
        "Pagination support for large result sets",
        "Full SPL document retrieval with structured sections",
        "Mapping data linking SPLs to external terminologies"
      ],
      useCases: [
        "Healthcare professionals researching drug information",
        "Patients seeking official drug labeling information", 
        "Researchers conducting pharmaceutical studies",
        "AI systems providing drug information and recommendations",
        "Regulatory compliance and drug safety monitoring"
      ]
    };
  }

  // SPL methods - delegate to SPLClient
  async searchSPLs(params: any) {
    return this.splClient.searchSPLs(params);
  }

  async getSPLBySetId(setId: string) {
    return this.splClient.getSPLBySetId(setId);
  }

  async getSPLHistory(setId: string) {
    return this.splClient.getSPLHistory(setId);
  }

  async getSPLNDCs(setId: string) {
    return this.splClient.getSPLNDCs(setId);
  }

  async getSPLPackaging(setId: string) {
    return this.splClient.getSPLPackaging(setId);
  }

  async getSPLMedia(setId: string) {
    return this.splClient.getSPLMedia(setId);
  }

  async downloadSPLZip(setId: string) {
    return this.splClient.downloadSPLZip(setId);
  }

  async downloadSPLPdf(setId: string) {
    return this.splClient.downloadSPLPdf(setId);
  }

  // Drug name methods - delegate to DrugNameClient  
  async searchDrugNames(query: string) {
    return this.drugNameClient.searchDrugNames(query);
  }

  async getAllDrugNames(page?: number, pageSize?: number) {
    return this.drugNameClient.getAllDrugNames(page, pageSize);
  }

  async searchDrugNamesAdvanced(params: any) {
    return this.drugNameClient.searchDrugNamesAdvanced(params);
  }

  // Drug class methods - delegate to DrugClassClient
  async getAllDrugClasses(page?: number, pageSize?: number) {
    return this.drugClassClient.getAllDrugClasses(page, pageSize);
  }

  async searchDrugClasses(params: any) {
    return this.drugClassClient.searchDrugClasses(params);
  }

  async searchDrugClassesAdvanced(params: any) {
    return this.drugClassClient.searchDrugClassesAdvanced(params);
  }

  async searchDrugsByPharmacologicClass(drugClassCode: string, codingSystem?: string, page?: number, pageSize?: number) {
    return this.drugClassClient.searchDrugsByPharmacologicClass(drugClassCode, codingSystem, page, pageSize);
  }

  // Mapping service methods
  async getMappingStatistics() {
    return this.mappingService.getStatistics();
  }

  async searchByRxNormMapping(drugName: string) {
    return this.mappingService.searchRxNormMappingsByName(drugName);
  }

  async getRxNormMappingsForSetId(setId: string) {
    return this.mappingService.getRxNormMappings(setId);
  }

  async getPharmacologicClassMappingsForSetId(setId: string) {
    return this.mappingService.getPharmacologicClassMappings(setId);
  }

  async getMappingsByRxCUI(rxcui: string) {
    return this.mappingService.getMappingsByRxCUI(rxcui);
  }

  async getRxNormMappingsByPharmacologicClass(pharmaSetId: string) {
    return this.mappingService.getRxNormMappingsByPharmacologicClass(pharmaSetId);
  }

  async getAllPharmacologicClassSetIds() {
    return this.mappingService.getAllPharmacologicClassSetIds();
  }

  async getPharmacologicClassDetails(pharmaSetId: string) {
    const result = this.mappingService.getRxNormMappingsByPharmacologicClass(pharmaSetId);
    return {
      setId: pharmaSetId,
      title: `Pharmacologic Class ${pharmaSetId}`,
      relatedDrugs: result.rxNormMappings.length,
      splSetIds: result.splSetIds,
      classificationInfo: {
        mechanismOfAction: [],
        physiologicEffect: [],
        chemicalStructure: [],
        establishedPharmacologicClass: [],
      },
      fdaContext: result.fdaContext,
    };
  }

  // NDC methods - delegate to NDCClient
  async getAllNDCs(page?: number, pageSize?: number) {
    return this.ndcClient.getAllNDCs(page, pageSize);
  }

  // RxNorm methods - delegate to RxNormClient
  async getAllRxCUIs(page?: number, pageSize?: number) {
    return this.rxNormClient.getAllRxCUIs(page, pageSize);
  }

  async searchRxCUIs(params: any) {
    return this.rxNormClient.searchRxCUIs(params);
  }

  // UNII methods - delegate to UNIIClient
  async getAllUNIIs(page?: number, pageSize?: number) {
    return this.uniiClient.getAllUNIIs(page, pageSize);
  }

  async searchUNIIs(params: any) {
    return this.uniiClient.searchUNIIs(params);
  }

  // Application Number methods - delegate to ApplicationNumberClient
  async getAllApplicationNumbers(page?: number, pageSize?: number) {
    return this.applicationNumberClient.getAllApplicationNumbers(page, pageSize);
  }

  async searchApplicationNumbers(params: any) {
    return this.applicationNumberClient.searchApplicationNumbers(params);
  }

  async searchApplicationNumbersAdvanced(params: any) {
    return this.applicationNumberClient.searchApplicationNumbersAdvanced(params);
  }
}