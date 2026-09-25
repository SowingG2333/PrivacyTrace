export function extractTextContent(element: any): string {
  if (!element) return "";
  
  if (typeof element === "string") {
    return element.trim();
  }
  
  if (Array.isArray(element)) {
    return element.map(item => extractTextContent(item)).join(" ").trim();
  }
  
  if (typeof element === "object") {
    if (element._) {
      return element._.trim();
    }
    
    let text = "";
    for (const key in element) {
      if (key !== "$" && element.hasOwnProperty(key)) {
        text += " " + extractTextContent(element[key]);
      }
    }
    return text.trim();
  }
  
  return "";
}

export function extractListContent(list: any[]): string {
  if (!Array.isArray(list)) return "";
  
  return list.map((item, index) => {
    const content = extractTextContent(item);
    return content ? `${index + 1}. ${content}` : "";
  }).filter(item => item).join("\n");
}

export function extractTableContent(table: any): string {
  if (!table || !table.tbody || !Array.isArray(table.tbody)) return "";
  
  let tableText = "";
  table.tbody.forEach((tbody: any) => {
    if (tbody.tr && Array.isArray(tbody.tr)) {
      tbody.tr.forEach((row: any) => {
        if (row.td && Array.isArray(row.td)) {
          const cells = row.td.map((cell: any) => extractTextContent(cell)).join(" | ");
          if (cells.trim()) {
            tableText += cells + "\n";
          }
        }
      });
    }
  });
  
  return tableText.trim();
}