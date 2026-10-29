import { describe, expect, it, mock } from "bun:test";

// Mock vscode module before imports
mock.module("vscode", () => {
  return {
    EventEmitter: class {
      event = () => {};
      fire() {}
    },
    Range: class {
      constructor(
        public startLine: number,
        public startChar: number,
        public endLine: number,
        public endChar: number
      ) {}
    },
    CodeLens: class {
      command?: any;
      constructor(
        public range: any,
        command?: any
      ) {
        this.command = command;
      }
    },
    workspace: {
      getConfiguration: () => ({
        get: (_key: string, defVal: any) => defVal,
      }),
    },
  };
});

import { VibeTraceApiClient } from "../../client/api_client.js";
import { VibeTraceWsClient } from "../../client/ws_client.js";
import { VibeTraceCodeLensProvider } from "../../providers/codelens_provider.js";

describe("VibeTraceApiClient", () => {
  it("normalizes trailing slashes in base URL", () => {
    const client1 = new VibeTraceApiClient("http://127.0.0.1:8000/");
    expect(client1.getBaseUrl()).toBe("http://127.0.0.1:8000");

    const client2 = new VibeTraceApiClient("http://127.0.0.1:8000///");
    expect(client2.getBaseUrl()).toBe("http://127.0.0.1:8000");

    client2.updateBaseUrl("http://localhost:9000/");
    expect(client2.getBaseUrl()).toBe("http://localhost:9000");
  });

  it("handles offline daemon gracefully with null or fallback", async () => {
    // Unused port
    const client = new VibeTraceApiClient("http://127.0.0.1:59999");
    const isAvail = await client.isAvailable();
    expect(isAvail).toBe(false);

    const guard = await client.getCallerGuard("nonexistent_symbol");
    expect(guard).toBeNull();

    const slice = await client.getCodeSlice("nonexistent_symbol");
    expect(slice).toBeNull();

    const bundle = await client.getTaskBundle("some intent");
    expect(bundle).toBeNull();

    const links = await client.getProjectLinks();
    expect(links).toEqual([]);
  });
});

describe("VibeTraceWsClient", () => {
  it("registers and triggers refresh listeners", () => {
    const wsClient = new VibeTraceWsClient("ws://127.0.0.1:59999/ws");
    let called = false;
    const unsubscribe = wsClient.onRefresh(() => {
      called = true;
    });

    const listeners = (wsClient as any).refreshListeners;
    expect(listeners.size).toBe(1);

    for (const l of listeners) {
      l();
    }
    expect(called).toBe(true);

    unsubscribe();
    expect(listeners.size).toBe(0);
    wsClient.dispose();
  });

  it("registers and triggers agent focus listeners", () => {
    const wsClient = new VibeTraceWsClient("ws://127.0.0.1:59999/ws");
    let capturedEvent: any = null;
    const unsubscribe = wsClient.onAgentFocus((p) => {
      capturedEvent = p;
    });

    const listeners = (wsClient as any).agentFocusListeners;
    expect(listeners.size).toBe(1);

    for (const l of listeners) {
      l({
        event_type: "impact_analysis",
        node_ids: ["n1", "n2"],
        summary: "Analyzed checkout impact",
        from_symbol: "checkout",
      });
    }

    expect(capturedEvent).not.toBeNull();
    expect(capturedEvent.event_type).toBe("impact_analysis");
    expect(capturedEvent.node_ids).toEqual(["n1", "n2"]);

    unsubscribe();
    expect(listeners.size).toBe(0);
    wsClient.dispose();
  });
});

describe("VibeTraceCodeLensProvider declaration scanner", () => {
  it("detects Python function and class declarations", () => {
    const client = new VibeTraceApiClient("http://127.0.0.1:8000");
    const provider = new VibeTraceCodeLensProvider(client);

    const fakeDoc: any = {
      languageId: "python",
      getText: () => `
# Comment
def process_order(order_id: str):
    pass

async def handle_payment():
    return True

class OrderService:
    def __init__(self):
        pass
`,
    };

    const symbols = (provider as any).findDeclarations(fakeDoc);
    const names = symbols.map((s: any) => s.name);
    expect(names).toContain("process_order");
    expect(names).toContain("handle_payment");
    expect(names).toContain("OrderService");
  });

  it("detects TypeScript/JavaScript function and class declarations", () => {
    const client = new VibeTraceApiClient("http://127.0.0.1:8000");
    const provider = new VibeTraceCodeLensProvider(client);

    const fakeDoc: any = {
      languageId: "typescript",
      getText: () => `
export function fetchUser(id: string) {
  return null;
}

export const submitForm = async () => {
  return true;
};

export class UserStore {
}
`,
    };

    const symbols = (provider as any).findDeclarations(fakeDoc);
    const names = symbols.map((s: any) => s.name);
    expect(names).toContain("fetchUser");
    expect(names).toContain("submitForm");
    expect(names).toContain("UserStore");
  });

  it("detects Go and Rust function declarations", () => {
    const client = new VibeTraceApiClient("http://127.0.0.1:8000");
    const provider = new VibeTraceCodeLensProvider(client);

    const goDoc: any = {
      languageId: "go",
      getText: () => `
func ProcessItem(id string) error {
    return nil
}
func (s *Server) HandleReq() {
}
`,
    };
    const goSymbols = (provider as any).findDeclarations(goDoc);
    expect(goSymbols.map((s: any) => s.name)).toContain("ProcessItem");
    expect(goSymbols.map((s: any) => s.name)).toContain("HandleReq");

    const rustDoc: any = {
      languageId: "rust",
      getText: () => `
pub async fn execute_task() -> Result<(), Error> {
    Ok(())
}
pub struct TaskManager {
}
`,
    };
    const rustSymbols = (provider as any).findDeclarations(rustDoc);
    expect(rustSymbols.map((s: any) => s.name)).toContain("execute_task");
    expect(rustSymbols.map((s: any) => s.name)).toContain("TaskManager");
  });
});
