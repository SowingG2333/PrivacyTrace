#!/usr/bin/env node

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { DailyMedClient } from "./dailymed-client.js";
import { dailyMedTools } from "./tools.js";

const ALLOWED_TOOL_NAMES = new Set([
  "search_spls",
  "search_drug_names",
  "get_drug_details",
  "get_drug_history",
  "get_drug_ndcs",
  "get_drug_packaging",
]);
const visibleDailyMedTools = dailyMedTools.filter((tool) =>
  ALLOWED_TOOL_NAMES.has(tool.name),
);

class DailyMedServer {
  private server: Server;
  private client: DailyMedClient;

  constructor() {
    this.server = new Server(
      {
        name: "dailymed-server",
        version: "1.0.0",
      },
      {
        capabilities: {
          tools: {},
        },
      },
    );

    this.client = new DailyMedClient();
    this.setupHandlers();
  }

  private setupHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      return {
        tools: visibleDailyMedTools,
      };
    });

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      try {
        const { name, arguments: args } = request.params;

        if (!ALLOWED_TOOL_NAMES.has(name)) {
          throw new Error(`Tool is not enabled in PrivacyTrace: ${name}`);
        }

        if (!args) {
          throw new Error("Missing arguments");
        }

        if (name === "search_spls" && "query" in args) {
          throw new Error(
            "The shorthand query path is disabled; use the direct drug_name filter",
          );
        }

        switch (name) {
          case "get_dailymed_context":
            const contextInfo = await this.client.getDailyMedContext();
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(contextInfo, null, 2),
                },
              ],
            };


          case "get_drug_details":
            const drugDetails = await this.client.getSPLBySetId(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(drugDetails, null, 2),
                },
              ],
            };

          case "get_drug_history":
            const history = await this.client.getSPLHistory(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(history, null, 2),
                },
              ],
            };

          case "get_drug_ndcs":
            const ndcs = await this.client.getSPLNDCs(args.setId as string);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(ndcs, null, 2),
                },
              ],
            };

          case "get_drug_packaging":
            const packaging = await this.client.getSPLPackaging(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(packaging, null, 2),
                },
              ],
            };

          case "get_drug_media":
            const media = await this.client.getSPLMedia(args.setId as string);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(media, null, 2),
                },
              ],
            };

          case "get_all_drug_names":
            const allDrugNames = await this.client.getAllDrugNames(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allDrugNames, null, 2),
                },
              ],
            };

          case "get_all_drug_classes":
            const allDrugClasses = await this.client.getAllDrugClasses(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allDrugClasses, null, 2),
                },
              ],
            };

          case "get_all_ndcs":
            const allNDCs = await this.client.getAllNDCs(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allNDCs, null, 2),
                },
              ],
            };

          case "get_all_rxcuis":
            const allRxCUIs = await this.client.getAllRxCUIs(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allRxCUIs, null, 2),
                },
              ],
            };

          case "get_all_uniis":
            const allUNIIs = await this.client.getAllUNIIs(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allUNIIs, null, 2),
                },
              ],
            };

          case "get_all_application_numbers":
            const allAppNumbers = await this.client.getAllApplicationNumbers(
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allAppNumbers, null, 2),
                },
              ],
            };

          case "get_download_links":
            const zipLink = await this.client.downloadSPLZip(
              args.setId as string,
            );
            const pdfLink = await this.client.downloadSPLPdf(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(
                    {
                      zipDownload: zipLink,
                      pdfDownload: pdfLink,
                    },
                    null,
                    2,
                  ),
                },
              ],
            };

          case "search_spls":
            const splResults = await this.client.searchSPLs(args as any);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(splResults, null, 2),
                },
              ],
            };

          case "search_rxcuis":
            const rxcuiParams: any = {};
            if (args.rxstring) rxcuiParams.rxstring = args.rxstring as string;
            if (args.rxcui) rxcuiParams.rxcui = args.rxcui as string;
            if (args.rxtty) rxcuiParams.rxtty = args.rxtty as string;
            if (args.page) rxcuiParams.page = args.page as number;
            if (args.pageSize) rxcuiParams.pageSize = args.pageSize as number;

            const rxcuiResults = await this.client.searchRxCUIs(rxcuiParams);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(rxcuiResults, null, 2),
                },
              ],
            };

          case "search_uniis":
            const uniiParams: any = {};
            if (args.active_moiety) uniiParams.active_moiety = args.active_moiety as string;
            if (args.drug_class_code) uniiParams.drug_class_code = args.drug_class_code as string;
            if (args.drug_class_coding_system) uniiParams.drug_class_coding_system = args.drug_class_coding_system as string;
            if (args.rxcui) uniiParams.rxcui = args.rxcui as string;
            if (args.unii_code) uniiParams.unii_code = args.unii_code as string;
            if (args.page) uniiParams.page = args.page as number;
            if (args.pageSize) uniiParams.pageSize = args.pageSize as number;

            const uniiResults = await this.client.searchUNIIs(uniiParams);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(uniiResults, null, 2),
                },
              ],
            };

          case "search_application_numbers":
            const appNumParams: any = {};
            if (args.application_number)
              appNumParams.application_number =
                args.application_number as string;
            if (args.marketing_category_code)
              appNumParams.marketing_category_code =
                args.marketing_category_code as string;
            if (args.setid) appNumParams.setid = args.setid as string;

            // Add pagination parameters
            if (args.page) appNumParams.page = args.page as number;
            if (args.pageSize) appNumParams.pageSize = args.pageSize as number;

            const appNumResults = await this.client.searchApplicationNumbersAdvanced(appNumParams);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(appNumResults, null, 2),
                },
              ],
            };

          case "search_drug_names":
            const drugNameParams: any = {};
            if (args.drug_name) drugNameParams.drug_name = args.drug_name as string;
            if (args.name_type) drugNameParams.name_type = args.name_type as string;
            if (args.manufacturer) drugNameParams.manufacturer = args.manufacturer as string;
            if (args.page) drugNameParams.page = args.page as number;
            if (args.pageSize) drugNameParams.pageSize = args.pageSize as number;

            const drugNameResults = await this.client.searchDrugNamesAdvanced(drugNameParams);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(drugNameResults, null, 2),
                },
              ],
            };

          case "search_drug_classes":
            const drugClassParams: any = {};
            if (args.drug_class_code)
              drugClassParams.drug_class_code = args.drug_class_code as string;
            if (args.drug_class_coding_system)
              drugClassParams.drug_class_coding_system =
                args.drug_class_coding_system as string;
            if (args.class_code_type)
              drugClassParams.class_code_type = args.class_code_type as string;
            if (args.class_name)
              drugClassParams.class_name = args.class_name as string;
            if (args.unii_code)
              drugClassParams.unii_code = args.unii_code as string;

            // Add pagination parameters
            if (args.page) drugClassParams.page = args.page as number;
            if (args.pageSize) drugClassParams.pageSize = args.pageSize as number;

            const drugClassResults = await this.client.searchDrugClassesAdvanced(drugClassParams);
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(drugClassResults, null, 2),
                },
              ],
            };

          case "get_mapping_statistics":
            const mappingStats = await this.client.getMappingStatistics();
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(mappingStats, null, 2),
                },
              ],
            };

          case "search_by_rxnorm_mapping":
            const rxNormSearchResults = await this.client.searchByRxNormMapping(
              args.drugName as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(rxNormSearchResults, null, 2),
                },
              ],
            };

          case "get_rxnorm_mappings_for_setid":
            const rxNormMappings = await this.client.getRxNormMappingsForSetId(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(rxNormMappings, null, 2),
                },
              ],
            };

          case "get_pharmacologic_class_mappings_for_setid":
            const pharmacologicClassMappings = await this.client.getPharmacologicClassMappingsForSetId(
              args.setId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(pharmacologicClassMappings, null, 2),
                },
              ],
            };

          case "get_mappings_by_rxcui":
            const rxcuiMappings = await this.client.getMappingsByRxCUI(
              args.rxcui as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(rxcuiMappings, null, 2),
                },
              ],
            };

          case "get_rxnorm_mappings_by_pharmacologic_class":
            const pharmaClassMappings = await this.client.getRxNormMappingsByPharmacologicClass(
              args.pharmaSetId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(pharmaClassMappings, null, 2),
                },
              ],
            };

          case "get_all_pharmacologic_class_setids":
            const allPharmaSetIds = await this.client.getAllPharmacologicClassSetIds();
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(allPharmaSetIds, null, 2),
                },
              ],
            };

          case "get_pharmacologic_class_details":
            const pharmaClassDetails = await this.client.getPharmacologicClassDetails(
              args.pharmaSetId as string,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(pharmaClassDetails, null, 2),
                },
              ],
            };

          case "search_drugs_by_pharmacologic_class":
            const drugsByClass = await this.client.searchDrugsByPharmacologicClass(
              args.drugClassCode as string,
              args.codingSystem as string | undefined,
              args.page as number,
              args.pageSize as number,
            );
            return {
              content: [
                {
                  type: "text",
                  text: JSON.stringify(drugsByClass, null, 2),
                },
              ],
            };

          default:
            throw new Error(`Unknown tool: ${name}`);
        }
      } catch (error) {
        return {
          content: [
            {
              type: "text",
              text: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
            },
          ],
          isError: true,
        };
      }
    });
  }

  async run() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    console.error("DailyMed MCP server running on stdio");
  }
}

const server = new DailyMedServer();
server.run().catch(console.error);
