import axios from "axios";
import { BookOpen, Layers, RefreshCw, Search } from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useState } from "react";
import type { DocCatalog, DocChunk } from "../types";
import { CatalogCard } from "./CatalogCard";
import { DocChunkCard } from "./DocChunkCard";

interface DocsWorkspaceViewProps {
  apiUrl: string;
}

type ScopeType = "local" | "global" | "all";

export const DocsWorkspaceView: React.FC<DocsWorkspaceViewProps> = ({ apiUrl }) => {
  const [searchQuery, setSearchQuery] = useState("architecture");
  const [searchScope, setSearchScope] = useState<ScopeType>("all");
  const [searchResults, setSearchResults] = useState<DocChunk[]>([]);
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  const [catalogs, setCatalogs] = useState<DocCatalog[]>([]);
  const [loadingCatalogs, setLoadingCatalogs] = useState(false);
  const [indexingCatalog, setIndexingCatalog] = useState<string | null>(null);

  const fetchCatalogs = useCallback(async () => {
    try {
      setLoadingCatalogs(true);
      const res = await axios.get<DocCatalog[]>(`${apiUrl}/api/docs/catalogs`);
      setCatalogs(res.data);
    } catch {
      setCatalogs([]);
    } finally {
      setLoadingCatalogs(false);
    }
  }, [apiUrl]);

  const executeSearch = useCallback(
    async (queryText: string, scopeVal: ScopeType) => {
      if (!queryText.trim()) return;
      try {
        setSearching(true);
        setHasSearched(true);
        const res = await axios.get<DocChunk[]>(`${apiUrl}/api/docs/search`, {
          params: {
            q: queryText.trim(),
            scope: scopeVal,
            limit: 16,
          },
        });
        setSearchResults(res.data);
      } catch {
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    },
    [apiUrl]
  );

  useEffect(() => {
    fetchCatalogs();
    executeSearch("architecture", "all");
  }, [fetchCatalogs, executeSearch]);

  const handleSearch = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    executeSearch(searchQuery, searchScope);
  };

  const handleIndexCatalog = async (catalogName?: string, isGlobal = false) => {
    const targetKey = catalogName || "ALL";
    try {
      setIndexingCatalog(targetKey);
      await axios.post(`${apiUrl}/api/docs/index`, {
        catalog: catalogName || null,
        global_scope: isGlobal,
        force: false,
      });
      await fetchCatalogs();
    } finally {
      setIndexingCatalog(null);
    }
  };

  return (
    <main
      aria-label="Documentation RAG Workspace"
      style={{
        position: "absolute",
        top: 86,
        left: 16,
        right: 16,
        bottom: 16,
        display: "grid",
        gridTemplateColumns: "360px 1fr",
        gap: 16,
        overflow: "hidden",
      }}
    >
      {/* Left Pane: Catalogs & Sync Manager */}
      <aside
        className="glass-panel"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "16px 18px",
            borderBottom: "1px solid var(--border-color)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Layers size={17} color="#a855f7" />
            <strong style={{ fontSize: 13.5 }}>RAG Catalogs ({catalogs.length})</strong>
          </div>
          <button
            type="button"
            className="vibe-btn vibe-btn-sm vibe-btn-primary"
            onClick={() => handleIndexCatalog()}
            disabled={indexingCatalog !== null}
          >
            <RefreshCw size={12} className={indexingCatalog === "ALL" ? "spin" : ""} />
            Sync All
          </button>
        </div>

        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {loadingCatalogs && (
            <div style={{ fontSize: 12, color: "var(--text-dim)", textAlign: "center" }}>
              Loading catalogs...
            </div>
          )}
          {catalogs.map((cat) => (
            <CatalogCard
              key={`${cat.scope}-${cat.name}`}
              catalog={cat}
              indexing={indexingCatalog === cat.name || indexingCatalog === "ALL"}
              onIndex={(name, isGlobal) => handleIndexCatalog(name, isGlobal)}
            />
          ))}
        </div>
      </aside>

      {/* Right Pane: Semantic Search & Full-Width Chunk Reader */}
      <section
        className="glass-panel"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <form
          onSubmit={handleSearch}
          style={{
            padding: "14px 18px",
            borderBottom: "1px solid var(--border-color)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            background: "rgba(0, 0, 0, 0.2)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <BookOpen size={18} color="#6366f1" />
            <strong style={{ fontSize: 14, whiteSpace: "nowrap" }}>Documentation RAG</strong>
          </div>

          <div style={{ position: "relative", flex: 1 }}>
            <Search
              size={15}
              style={{ position: "absolute", left: 12, top: 10, color: "var(--text-dim)" }}
            />
            <input
              type="text"
              placeholder="Search architecture docs, ADRs, security guides, or API contracts..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="vibe-input"
              style={{ width: "100%" }}
            />
          </div>

          <div className="vibe-segmented-control">
            {(["all", "local", "global"] as ScopeType[]).map((sc) => (
              <button
                key={sc}
                type="button"
                className={`vibe-segment-btn ${searchScope === sc ? "active" : ""}`}
                onClick={() => {
                  setSearchScope(sc);
                  executeSearch(searchQuery, sc);
                }}
              >
                {sc.toUpperCase()}
              </button>
            ))}
          </div>

          <button
            type="submit"
            className="vibe-btn vibe-btn-primary"
            disabled={searching || !searchQuery.trim()}
          >
            <Search size={14} />
            {searching ? "Searching..." : "Search"}
          </button>
        </form>

        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: 18,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))",
            gap: 14,
            alignContent: "start",
          }}
        >
          {searchResults.map((chunk) => (
            <DocChunkCard key={chunk.id} chunk={chunk} />
          ))}

          {hasSearched && searchResults.length === 0 && !searching && (
            <div
              style={{
                gridColumn: "1 / -1",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: 13,
                padding: "48px 0",
              }}
            >
              No documentation chunks matched "{searchQuery}" in {searchScope.toUpperCase()} scope.
            </div>
          )}
        </div>
      </section>
    </main>
  );
};
