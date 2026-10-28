import axios from "axios";
import { BookOpen, Layers, RefreshCw, Search, X } from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useState } from "react";
import type { DocCatalog, DocChunk } from "../types";
import { CatalogCard } from "./CatalogCard";
import { DocChunkCard } from "./DocChunkCard";

interface DocsExplorerDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  apiUrl: string;
}

type TabType = "search" | "catalogs";
type ScopeType = "local" | "global" | "all";

export const DocsExplorerDrawer: React.FC<DocsExplorerDrawerProps> = ({
  isOpen,
  onClose,
  apiUrl,
}) => {
  const [activeTab, setActiveTab] = useState<TabType>("search");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchScope, setSearchScope] = useState<ScopeType>("local");
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

  useEffect(() => {
    if (isOpen) {
      fetchCatalogs();
    }
  }, [isOpen, fetchCatalogs]);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!searchQuery.trim()) return;

    try {
      setSearching(true);
      setHasSearched(true);
      const res = await axios.get<DocChunk[]>(`${apiUrl}/api/docs/search`, {
        params: {
          q: searchQuery.trim(),
          scope: searchScope,
          limit: 10,
        },
      });
      setSearchResults(res.data);
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
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

  if (!isOpen) return null;

  return (
    <div
      className="glass-panel"
      style={{
        position: "fixed",
        top: "88px",
        right: "20px",
        bottom: "20px",
        width: "440px",
        maxWidth: "calc(100vw - 40px)",
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxShadow: "0 16px 40px rgba(0, 0, 0, 0.6)",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "16px 18px",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <BookOpen size={18} color="#6366f1" />
          <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main)" }}>
            DOCUMENTATION RAG
          </span>
          <span
            style={{
              fontSize: "11px",
              padding: "2px 6px",
              borderRadius: "4px",
              background: "rgba(99, 102, 241, 0.15)",
              color: "#a5b4fc",
              fontWeight: 600,
            }}
          >
            {catalogs.length} catalogs
          </span>
        </div>

        <button
          type="button"
          onClick={onClose}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-dim)",
            cursor: "pointer",
            padding: "4px",
          }}
        >
          <X size={18} />
        </button>
      </div>

      {/* Navigation Tabs */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border-color)",
          background: "rgba(0, 0, 0, 0.2)",
        }}
      >
        <button
          type="button"
          onClick={() => setActiveTab("search")}
          style={{
            flex: 1,
            padding: "10px",
            background: activeTab === "search" ? "rgba(99, 102, 241, 0.15)" : "transparent",
            border: "none",
            borderBottom: activeTab === "search" ? "2px solid #6366f1" : "2px solid transparent",
            color: activeTab === "search" ? "#fff" : "var(--text-dim)",
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "6px",
          }}
        >
          <Search size={14} />
          Search Docs
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("catalogs")}
          style={{
            flex: 1,
            padding: "10px",
            background: activeTab === "catalogs" ? "rgba(99, 102, 241, 0.15)" : "transparent",
            border: "none",
            borderBottom: activeTab === "catalogs" ? "2px solid #6366f1" : "2px solid transparent",
            color: activeTab === "catalogs" ? "#fff" : "var(--text-dim)",
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "6px",
          }}
        >
          <Layers size={14} />
          Catalogs ({catalogs.length})
        </button>
      </div>

      {/* Tab Body */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: "14px",
        }}
      >
        {activeTab === "search" ? (
          <>
            <form
              onSubmit={handleSearch}
              style={{ display: "flex", flexDirection: "column", gap: "10px" }}
            >
              <div style={{ position: "relative" }}>
                <Search
                  size={15}
                  style={{ position: "absolute", left: 10, top: 10, color: "var(--text-dim)" }}
                />
                <input
                  type="text"
                  placeholder="Query architecture, specs, or guides..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "8px 12px 8px 32px",
                    background: "rgba(0, 0, 0, 0.4)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "6px",
                    color: "var(--text-main)",
                    fontSize: "13px",
                    outline: "none",
                  }}
                />
              </div>

              <div
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
              >
                <div style={{ display: "flex", gap: "6px" }}>
                  {(["local", "global", "all"] as ScopeType[]).map((sc) => (
                    <button
                      key={sc}
                      type="button"
                      onClick={() => setSearchScope(sc)}
                      style={{
                        padding: "3px 8px",
                        fontSize: "11px",
                        fontWeight: 600,
                        borderRadius: "4px",
                        border: "1px solid",
                        cursor: "pointer",
                        background: searchScope === sc ? "rgba(99, 102, 241, 0.25)" : "transparent",
                        borderColor: searchScope === sc ? "#6366f1" : "rgba(255, 255, 255, 0.1)",
                        color: searchScope === sc ? "#fff" : "var(--text-muted)",
                      }}
                    >
                      {sc.toUpperCase()}
                    </button>
                  ))}
                </div>

                <button
                  type="submit"
                  disabled={searching || !searchQuery.trim()}
                  style={{
                    padding: "5px 12px",
                    background: "var(--primary)",
                    border: "none",
                    borderRadius: "5px",
                    color: "#fff",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: searching ? "not-allowed" : "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "5px",
                  }}
                >
                  <Search size={13} />
                  {searching ? "Searching..." : "Search"}
                </button>
              </div>
            </form>

            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {hasSearched && (
                <div style={{ fontSize: "12px", color: "var(--text-dim)", padding: "2px 0" }}>
                  Found{" "}
                  <strong style={{ color: "var(--text-main)" }}>{searchResults.length}</strong>{" "}
                  matching chunks:
                </div>
              )}

              {searchResults.map((chunk) => (
                <DocChunkCard key={chunk.id} chunk={chunk} />
              ))}

              {hasSearched && searchResults.length === 0 && !searching && (
                <div
                  style={{
                    textAlign: "center",
                    color: "var(--text-muted)",
                    fontSize: "13px",
                    padding: "24px 0",
                  }}
                >
                  No documentation matches found in {searchScope} scope.
                </div>
              )}
            </div>
          </>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "12px", color: "var(--text-dim)" }}>
                Configured catalogs:
              </span>
              <button
                type="button"
                onClick={() => handleIndexCatalog()}
                disabled={indexingCatalog !== null}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "5px",
                  background: "rgba(99, 102, 241, 0.2)",
                  border: "1px solid rgba(99, 102, 241, 0.4)",
                  color: "#c7d2fe",
                  padding: "4px 8px",
                  borderRadius: "5px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: indexingCatalog !== null ? "not-allowed" : "pointer",
                }}
              >
                <RefreshCw size={12} className={indexingCatalog === "ALL" ? "spin" : ""} />
                Index All
              </button>
            </div>

            {loadingCatalogs && (
              <div
                style={{
                  textAlign: "center",
                  color: "var(--text-muted)",
                  fontSize: "12px",
                  padding: "12px",
                }}
              >
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
        )}
      </div>
    </div>
  );
};
