import axios, { AxiosInstance } from "axios";
import type { MappingService } from "../mapping-service.js";

export abstract class BaseClient {
  protected client: AxiosInstance;
  protected mappingService: MappingService;
  protected baseURL = "https://dailymed.nlm.nih.gov/dailymed/services/v2";

  constructor(mappingService: MappingService) {
    this.mappingService = mappingService;
    this.client = axios.create({
      baseURL: this.baseURL,
      headers: {
        Accept: "application/json",
        "User-Agent": "MCP-DailyMed-Server/1.0.0",
      },
      timeout: 30000,
    });
  }
}