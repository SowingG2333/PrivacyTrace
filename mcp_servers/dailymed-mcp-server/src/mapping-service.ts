import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

export interface PharmacologicClassMapping {
  splSetId: string;
  splVersion: number;
  pharmaSetId: string;
  pharmaVersion: number;
}

export interface RxNormMapping {
  setId: string;
  splVersion: number;
  rxcui: string;
  rxstring: string;
  rxtty: string;
}

export class MappingService {
  private pharmacologicClassMappings: Map<string, PharmacologicClassMapping[]> = new Map();
  private rxNormMappings: Map<string, RxNormMapping[]> = new Map();
  private rxcuiToMappings: Map<string, RxNormMapping[]> = new Map();
  private pharmaSetIdToSplSetIds: Map<string, string[]> = new Map();
  private projectRoot: string;

  constructor() {
    // Get the project root directory (where package.json is located)
    // This assumes the compiled JS is in dist/ directory
    const currentDir = dirname(fileURLToPath(import.meta.url));
    this.projectRoot = join(currentDir, '..');
    this.loadMappings();
  }

  private loadMappings(): void {
    try {
      this.loadPharmacologicClassMappings();
      this.loadRxNormMappings();
    } catch (error) {
      console.error('Error loading mapping files:', error);
      throw error;
    }
  }

  private loadPharmacologicClassMappings(): void {
    const filePath = join(this.projectRoot, 'pharmacologic_class_mappings.txt');
    if (!existsSync(filePath)) {
      return;
    }
    const content = readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');
    
    // Skip header line
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      
      const [splSetId, splVersion, pharmaSetId, pharmaVersion] = line.split('|');
      
      if (splSetId && splVersion && pharmaSetId && pharmaVersion) {
        const mapping: PharmacologicClassMapping = {
          splSetId,
          splVersion: parseInt(splVersion),
          pharmaSetId,
          pharmaVersion: parseInt(pharmaVersion)
        };
        
        if (!this.pharmacologicClassMappings.has(splSetId)) {
          this.pharmacologicClassMappings.set(splSetId, []);
        }
        this.pharmacologicClassMappings.get(splSetId)!.push(mapping);
        
        // Also build reverse mapping from pharmaSetId to splSetIds
        if (!this.pharmaSetIdToSplSetIds.has(pharmaSetId)) {
          this.pharmaSetIdToSplSetIds.set(pharmaSetId, []);
        }
        if (!this.pharmaSetIdToSplSetIds.get(pharmaSetId)!.includes(splSetId)) {
          this.pharmaSetIdToSplSetIds.get(pharmaSetId)!.push(splSetId);
        }
      }
    }
  }

  private loadRxNormMappings(): void {
    const filePath = join(this.projectRoot, 'rxnorm_mappings.txt');
    if (!existsSync(filePath)) {
      return;
    }
    const content = readFileSync(filePath, 'utf-8');
    const lines = content.split('\n');
    
    // Skip header line
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      
      const [setId, splVersion, rxcui, rxstring, rxtty] = line.split('|');
      
      if (setId && splVersion && rxcui && rxstring && rxtty) {
        const mapping: RxNormMapping = {
          setId,
          splVersion: parseInt(splVersion),
          rxcui,
          rxstring,
          rxtty
        };
        
        // Index by setId
        if (!this.rxNormMappings.has(setId)) {
          this.rxNormMappings.set(setId, []);
        }
        this.rxNormMappings.get(setId)!.push(mapping);
        
        // Index by rxcui
        if (!this.rxcuiToMappings.has(rxcui)) {
          this.rxcuiToMappings.set(rxcui, []);
        }
        this.rxcuiToMappings.get(rxcui)!.push(mapping);
      }
    }
  }

  /**
   * Get pharmacologic class mappings for a given SPL SET ID
   */
  getPharmacologicClassMappings(splSetId: string): PharmacologicClassMapping[] {
    return this.pharmacologicClassMappings.get(splSetId) || [];
  }

  /**
   * Get RxNorm mappings for a given SET ID
   */
  getRxNormMappings(setId: string): RxNormMapping[] {
    return this.rxNormMappings.get(setId) || [];
  }

  /**
   * Get mappings for a given RxCUI
   */
  getMappingsByRxCUI(rxcui: string): RxNormMapping[] {
    return this.rxcuiToMappings.get(rxcui) || [];
  }

  /**
   * Search for RxNorm mappings by drug name (case-insensitive)
   */
  searchRxNormMappingsByName(drugName: string): RxNormMapping[] {
    const searchTerm = drugName.toLowerCase();
    const results: RxNormMapping[] = [];
    
    for (const mappings of this.rxNormMappings.values()) {
      for (const mapping of mappings) {
        if (mapping.rxstring.toLowerCase().includes(searchTerm)) {
          results.push(mapping);
        }
      }
    }
    
    return results;
  }

  /**
   * Get all SET IDs that have RxNorm mappings
   */
  getAllSetIdsWithRxNormMappings(): string[] {
    return Array.from(this.rxNormMappings.keys());
  }

  /**
   * Get all SET IDs that have pharmacologic class mappings
   */
  getAllSetIdsWithPharmacologicClassMappings(): string[] {
    return Array.from(this.pharmacologicClassMappings.keys());
  }

  /**
   * Find RxNorm mappings by pharmacologic class SET ID
   * This combines both mapping files to find drugs that belong to a specific pharmacologic class
   */
  getRxNormMappingsByPharmacologicClass(pharmaSetId: string): {
    pharmaSetId: string;
    splSetIds: string[];
    rxNormMappings: RxNormMapping[];
    fdaContext: {
      definition: string;
      explanation: string;
      classification: string[];
    };
  } {
    const splSetIds = this.pharmaSetIdToSplSetIds.get(pharmaSetId) || [];
    const allRxNormMappings: RxNormMapping[] = [];
    
    for (const splSetId of splSetIds) {
      const rxNormMappings = this.getRxNormMappings(splSetId);
      allRxNormMappings.push(...rxNormMappings);
    }
    
    return {
      pharmaSetId,
      splSetIds,
      rxNormMappings: allRxNormMappings,
      fdaContext: {
        definition: "A pharmacologic class is a group of active moieties that share scientifically documented properties",
        explanation: "According to FDA guidelines, pharmacologic classes provide clinically meaningful and scientifically valid drug classifications based on three key attributes: Mechanism of Action (MOA), Physiologic Effect (PE), and Chemical Structure (CS)",
        classification: [
          "Mechanism of Action (MOA): How the drug works at the molecular level",
          "Physiologic Effect (PE): The body's response to the drug",
          "Chemical Structure (CS): Structural characteristics of the active moiety",
          "Source: National Drug File Reference Terminology (NDF-RT)"
        ]
      }
    };
  }

  /**
   * Search for RxNorm mappings by pharmacologic class name or code
   * This requires querying the DailyMed API to get pharmacologic class info first,
   * but this method can work with known pharma SET IDs
   */
  searchRxNormMappingsByPharmacologicClassSetId(pharmaSetId: string): RxNormMapping[] {
    const result = this.getRxNormMappingsByPharmacologicClass(pharmaSetId);
    return result.rxNormMappings;
  }

  /**
   * Get all pharmacologic class SET IDs that have associated SPL mappings
   */
  getAllPharmacologicClassSetIds(): string[] {
    return Array.from(this.pharmaSetIdToSplSetIds.keys());
  }

  /**
   * Get statistics about the loaded mappings
   */
  getStatistics(): {
    pharmacologicClassMappings: number;
    rxNormMappings: number;
    uniqueSetIds: number;
    uniqueRxCUIs: number;
    uniquePharmacologicClasses: number;
  } {
    let totalPharmaMappings = 0;
    let totalRxNormMappings = 0;
    
    for (const mappings of this.pharmacologicClassMappings.values()) {
      totalPharmaMappings += mappings.length;
    }
    
    for (const mappings of this.rxNormMappings.values()) {
      totalRxNormMappings += mappings.length;
    }
    
    return {
      pharmacologicClassMappings: totalPharmaMappings,
      rxNormMappings: totalRxNormMappings,
      uniqueSetIds: this.rxNormMappings.size,
      uniqueRxCUIs: this.rxcuiToMappings.size,
      uniquePharmacologicClasses: this.pharmaSetIdToSplSetIds.size
    };
  }
}
