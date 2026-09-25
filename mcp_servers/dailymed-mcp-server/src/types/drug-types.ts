export interface DrugName {
  drugName: string;
  routeOfAdministration?: string;
  activeIngredient?: string;
}

export interface NDC {
  ndc: string;
  packageNdc?: string;
  productNdc?: string;
}

export interface ApplicationNumber {
  applicationNumber: string;
  applicationNumberType?: string;
  marketingCategoryCode?: string;
  setId?: string;
}

export interface DrugClass {
  drugClassName: string;
  drugClassCode?: string;
  drugClassCodingSystem?: string;
  classCodeType?: string;
  uniiCode?: string;
}

export interface PharmacologicClassDetails {
  setId: string;
  title?: string;
  classificationInfo?: {
    mechanismOfAction?: string[];
    physiologicEffect?: string[];
    chemicalStructure?: string[];
    establishedPharmacologicClass?: string[];
  };
  fdaContext?: {
    definition: string;
    purpose: string;
    attributes: string[];
    sourceTerminology: string;
  };
}

export interface RxCUI {
  rxcui: string;
  drugName?: string;
  termType?: string;
}

export interface UNII {
  unii: string;
  substanceName?: string;
  uniiType?: string;
}