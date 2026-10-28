export interface DocCatalog {
  name: string;
  scope: "local" | "global";
  paths: string[];
  doc_count: number;
  chunk_count: number;
}

export interface DocChunk {
  id: string;
  catalog: string;
  file_path: string;
  heading_path: string;
  start_line: number;
  end_line: number;
  content: string;
  score: number;
  scope: "local" | "global";
  match_sources: string[];
}
