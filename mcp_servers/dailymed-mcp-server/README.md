# DailyMed MCP Server

A Model Context Protocol (MCP) server that provides access to the DailyMed API for comprehensive drug information.

## About DailyMed

DailyMed is the official FDA database containing drug labeling information for approved prescription/nonprescription drugs, biologics, and medical devices. It provides structured, up-to-date drug information in multiple formats with cross-references to RxNorm and pharmacologic classifications.

## Installation

```bash
git clone <repository-url>
cd dailymed-mcp-server
npm install
npm run build
```

**Optional:** Download mapping files from [DailyMed Mapping Files](https://dailymed.nlm.nih.gov/dailymed/app-support-mapping-files.cfm) and place in project root:
- `pharmacologic_class_mappings.txt`
- `rxnorm_mappings.txt`

## Usage

```bash
npm start              # Production
npm run dev           # Development with hot reload
```

## Key Features

### Search Tools
- **`search_spls`** - Search Structured Product Labels with advanced filtering and pagination
- **`search_drug_names`** - Search drug names with manufacturer and name type filters
- **`search_rxcuis`** - Search RxCUI codes with term type filtering
- **`search_drug_classes`** - Search pharmacologic drug classes
- **`search_application_numbers`** - Search FDA application numbers
- **`search_uniis`** - Search UNII codes with advanced filters

### Drug Information
- **`get_drug_details`** - Complete drug information by SET ID
- **`get_drug_history`** - Version history for drugs
- **`get_drug_ndcs`** - NDC codes for specific drugs
- **`get_drug_packaging`** - Packaging information
- **`get_drug_media`** - Media links (images, documents)

### Database Access
- **`get_all_*`** - Paginated access to drug names, classes, NDCs, RxCUIs, UNIIs, application numbers
- **`get_dailymed_context`** - Database information and capabilities

### Enhanced Mapping (when mapping files present)
- **`search_by_rxnorm_mapping`** - Search RxNorm mappings by drug name
- **`get_*_mappings_for_setid`** - Get mappings for specific drugs
- **`search_drugs_by_pharmacologic_class`** - Find drugs by pharmacologic class

## Advanced SPL Search

### Simple Search
```json
{
  "query": "aspirin",
  "page": 1,
  "pageSize": 25
}
```

### Advanced Search
```json
{
  "manufacturer": "Pfizer",
  "boxed_warning": true,
  "published_date": "2023-01-01",
  "published_date_comparison": "gte",
  "page": 1,
  "pageSize": 50
}
```

**Available filters:** `application_number`, `boxed_warning`, `dea_schedule_code`, `drug_name`, `name_type`, `labeler`, `manufacturer`, `ndc`, `rxcui`, `unii_code`, `published_date`, and more.

## Pagination

All search and list tools support pagination with consistent response format:

```json
{
  "data": [...],
  "pagination": {
    "page": 1,
    "pageSize": 25,
    "totalResults": 150,
    "totalPages": 6,
    "hasNextPage": true,
    "hasPreviousPage": false
  }
}
```

## Claude Desktop Configuration

```json
{
  "mcpServers": {
    "dailymed": {
      "command": "node",
      "args": ["/path/to/dailymed-mcp-server/dist/index.js"],
      "cwd": "/path/to/dailymed-mcp-server"
    }
  }
}
```

## API Information

- **Base URL:** https://dailymed.nlm.nih.gov/dailymed/services/v2
- **Format:** JSON
- **Authentication:** None required
- **Compliance:** Fully compliant with DailyMed API v2 specifications

## Development

```bash
npm run build    # Build TypeScript
npm run dev      # Development mode
```

**Project Structure:**
```
src/
├── clients/           # Modular API clients
├── types/            # TypeScript type definitions
├── utils/            # Utilities and helpers
├── index.ts          # MCP server implementation
└── tools.ts          # Tool definitions
```

## License

MIT License