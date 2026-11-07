import axios from "axios";
import { Check, Copy, Sparkles } from "lucide-react";
import type React from "react";
import { useState } from "react";

interface VibeBundleResponse {
  intent: string;
  target_symbol?: string;
  token_budget: number;
  estimated_tokens: number;
  formatted_prompt: string;
}

interface VibeBundleButtonProps {
  apiUrl: string;
  symbolName: string;
}

const VIBE_BUNDLE_BUDGET = 1500;

export const VibeBundleButton: React.FC<VibeBundleButtonProps> = ({ apiUrl, symbolName }) => {
  const [loading, setLoading] = useState(false);
  const [copiedTokens, setCopiedTokens] = useState<number | null>(null);

  const handleCopyBundle = async () => {
    if (!symbolName || loading) return;
    setLoading(true);

    try {
      const res = await axios.get<VibeBundleResponse>(`${apiUrl}/api/vibe/bundle`, {
        params: {
          intent: symbolName,
          symbol: symbolName,
          budget: VIBE_BUNDLE_BUDGET,
        },
      });

      if (res.data?.formatted_prompt) {
        try {
          await navigator.clipboard.writeText(res.data.formatted_prompt);
          setCopiedTokens(res.data.estimated_tokens || 1000);
          setTimeout(() => setCopiedTokens(null), 2500);
        } catch (clipboardErr) {
          console.warn("Failed to copy bundle prompt to clipboard:", clipboardErr);
        }
      }
    } catch (err) {
      console.error("Vibe bundle API error:", err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopyBundle}
      disabled={loading}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        padding: "5px 10px",
        borderRadius: 6,
        background: copiedTokens !== null ? "rgba(16, 185, 129, 0.2)" : "rgba(99, 102, 241, 0.15)",
        border: `1px solid ${copiedTokens !== null ? "rgba(16, 185, 129, 0.4)" : "rgba(99, 102, 241, 0.35)"}`,
        color: copiedTokens !== null ? "#34d399" : "#a5b4fc",
        fontSize: "11px",
        fontWeight: 600,
        cursor: loading ? "wait" : "pointer",
        transition: "all 0.15s ease",
      }}
    >
      {copiedTokens !== null ? (
        <>
          <Check size={13} />
          <span>Copied Bundle ({copiedTokens} tokens)</span>
        </>
      ) : loading ? (
        <>
          <Sparkles size={13} className="spin" />
          <span>Packing Bundle...</span>
        </>
      ) : (
        <>
          <Copy size={13} />
          <span>Copy Vibe Bundle</span>
        </>
      )}
    </button>
  );
};
