import { Tool } from "@modelcontextprotocol/sdk/types.js";

export const dailyMedTools: Tool[] = [
  {
    name: "get_dailymed_context",
    description: "Get comprehensive information about DailyMed database, its purpose, content types, and when to use it",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "get_drug_details",
    description: "Get detailed information about a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get details for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_drug_history",
    description: "Get version history for a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get history for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_drug_ndcs",
    description: "Get NDC codes for a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get NDCs for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_drug_packaging",
    description: "Get packaging information for a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get packaging info for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_drug_media",
    description:
      "Get media links (images, documents) for a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get media for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_all_drug_names",
    description: "Get all available drug names in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_all_drug_classes",
    description: "Get all available drug classes in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_all_ndcs",
    description: "Get all available NDC codes in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_all_rxcuis",
    description: "Get all available RxCUI codes in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_all_uniis",
    description: "Get all available UNII codes in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_all_application_numbers",
    description: "Get all available FDA application numbers in the DailyMed database with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_download_links",
    description:
      "Get download links for ZIP and PDF files of a specific drug by its SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID of the drug to get download links for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "search_spls",
    description: "Search official DailyMed Structured Product Labels (SPLs) with direct API filters such as drug_name, manufacturer, NDC, RxCUI, or SET ID. Supports pagination.",
    inputSchema: {
      type: "object",
      properties: {
        // Advanced DailyMed SPLs API parameters
        application_number: {
          type: "string",
          description: "NDA number (e.g., 'NDA012345')",
        },
        boxed_warning: {
          type: "boolean",
          description: "Filter by presence of boxed warning",
        },
        dea_schedule_code: {
          type: "string",
          description: "DEA schedule code (e.g., 'none', 'C48672')",
        },
        doctype: {
          type: "string",
          description: "FDA document type",
        },
        drug_class_code: {
          type: "string",
          description: "Pharmacologic drug class code",
        },
        drug_class_coding_system: {
          type: "string",
          description: "Drug class coding system (default: '2.16.840.1.113883.3.26.1.5')",
        },
        drug_name: {
          type: "string",
          description: "Generic or brand drug name for direct API search",
        },
        name_type: {
          type: "string",
          description: "Name type filter",
          enum: ["g", "generic", "b", "brand", "both"],
        },
        labeler: {
          type: "string",
          description: "Labeler name",
        },
        manufacturer: {
          type: "string",
          description: "Manufacturer name",
        },
        marketing_category_code: {
          type: "string",
          description: "FDA marketing category code",
        },
        ndc: {
          type: "string",
          description: "National Drug Code",
        },
        published_date: {
          type: "string",
          description: "Published date in YYYY-MM-DD format",
        },
        published_date_comparison: {
          type: "string",
          description: "Date comparison operator",
          enum: ["lt", "lte", "gt", "gte", "eq"],
        },
        rxcui: {
          type: "string",
          description: "RxNorm Concept Unique Identifier",
        },
        setid: {
          type: "string",
          description: "Label Set ID",
        },
        unii_code: {
          type: "string",
          description: "Unique Ingredient Identifier",
        },
        // Pagination parameters
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100 for advanced queries, max: 200 for simple queries)",
          minimum: 1,
          maximum: 200,
        },
      },
      required: [],
    },
  },
  {
    name: "search_rxcuis",
    description: "Search for RxCUI codes using various parameters with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        rxstring: {
          type: "string",
          description: "RxString value of an RxConcept (drug name/description)",
        },
        rxcui: {
          type: "string",
          description: "Specific RxCUI identifier to search for",
        },
        rxtty: {
          type: "string",
          description: "Term Type of RxConcept",
          enum: ["PSN", "SBD", "SCD", "BPCK", "GPCK", "SY"],
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "search_drug_names",
    description: "Search for drug names using various parameters with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        drug_name: {
          type: "string",
          description: "Generic or brand name of drug",
        },
        name_type: {
          type: "string",
          description: "Specify name type",
          enum: ["g", "generic", "b", "brand", "both"],
        },
        manufacturer: {
          type: "string",
          description: "Name of drug manufacturer",
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "search_uniis",
    description: "Search for UNII codes using various parameters with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        active_moiety: {
          type: "string",
          description: "Active moiety filter",
        },
        drug_class_code: {
          type: "string",
          description: "Drug class code filter",
        },
        drug_class_coding_system: {
          type: "string",
          description: "Drug class coding system filter",
        },
        rxcui: {
          type: "string",
          description: "RxCUI filter",
        },
        unii_code: {
          type: "string",
          description: "Specific UNII code to search for",
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "search_application_numbers",
    description: "Search for FDA application numbers (NDA, ANDA, etc.) using various parameters with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        application_number: {
          type: "string",
          description: "Specific drug application number (e.g., NDA022527)",
        },
        marketing_category_code: {
          type: "string",
          description: "Marketing category code for a drug",
        },
        setid: {
          type: "string",
          description: "Set ID of a drug label",
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "search_drug_classes",
    description: "Search for pharmacologic drug classes using various parameters with pagination support",
    inputSchema: {
      type: "object",
      properties: {
        drug_class_code: {
          type: "string",
          description: "Code representing a pharmacologic drug class",
        },
        drug_class_coding_system: {
          type: "string",
          description: "Coding system (default: National Drug File Reference Terminology)",
        },
        class_code_type: {
          type: "string",
          description: "Type of pharmacologic drug class",
          enum: ["all", "epc", "moa", "pe", "ci"],
        },
        class_name: {
          type: "string",
          description: "Name of the drug class",
        },
        unii_code: {
          type: "string",
          description: "Unique Ingredient Identifier code",
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 100, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
    },
  },
  {
    name: "get_mapping_statistics",
    description: "Get statistics about loaded mapping files",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "search_by_rxnorm_mapping",
    description: "Search for RxNorm mappings by drug name",
    inputSchema: {
      type: "object",
      properties: {
        drugName: {
          type: "string",
          description: "Drug name to search for in RxNorm mappings",
        },
      },
      required: ["drugName"],
    },
  },
  {
    name: "get_rxnorm_mappings_for_setid",
    description: "Get RxNorm mappings for a specific SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID to get RxNorm mappings for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_pharmacologic_class_mappings_for_setid",
    description: "Get pharmacologic class mappings for a specific SET ID",
    inputSchema: {
      type: "object",
      properties: {
        setId: {
          type: "string",
          description: "The SET ID to get pharmacologic class mappings for",
        },
      },
      required: ["setId"],
    },
  },
  {
    name: "get_mappings_by_rxcui",
    description: "Get mappings for a specific RxCUI",
    inputSchema: {
      type: "object",
      properties: {
        rxcui: {
          type: "string",
          description: "The RxCUI to get mappings for",
        },
      },
      required: ["rxcui"],
    },
  },
  {
    name: "get_rxnorm_mappings_by_pharmacologic_class",
    description: "Find RxNorm mappings for drugs that belong to a specific pharmacologic class SET ID",
    inputSchema: {
      type: "object",
      properties: {
        pharmaSetId: {
          type: "string",
          description: "The pharmacologic class SET ID to find RxNorm mappings for",
        },
      },
      required: ["pharmaSetId"],
    },
  },
  {
    name: "get_all_pharmacologic_class_setids",
    description: "Get all pharmacologic class SET IDs that have associated drug mappings",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "get_pharmacologic_class_details",
    description: "Get detailed information about a pharmacologic class including FDA context and classification attributes (uses mapping file data)",
    inputSchema: {
      type: "object",
      properties: {
        pharmaSetId: {
          type: "string",
          description: "The pharmacologic class SET ID to get details for",
        },
      },
      required: ["pharmaSetId"],
    },
  },
  {
    name: "search_drugs_by_pharmacologic_class",
    description: "Search for drugs using DailyMed drug class codes (from the drug classes API). Supports pagination for large result sets.",
    inputSchema: {
      type: "object",
      properties: {
        drugClassCode: {
          type: "string",
          description: "The drug class code (e.g., N0000175605 for Kinase Inhibitor) from DailyMed drug classes API",
        },
        codingSystem: {
          type: "string",
          description: "The coding system for the drug class code (defaults to 2.16.840.1.113883.6.345 which matches the drug classes API)",
        },
        page: {
          type: "number",
          description: "Page number for pagination (1-based, default: 1)",
          minimum: 1,
        },
        pageSize: {
          type: "number",
          description: "Number of results per page (default: 25, max: 100)",
          minimum: 1,
          maximum: 100,
        },
      },
      required: ["drugClassCode"],
    },
  },
];
