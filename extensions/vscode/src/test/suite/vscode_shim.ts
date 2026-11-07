import { mock } from "bun:test";

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
    Position: class {
      constructor(
        public line: number,
        public character: number
      ) {}
    },
    Selection: class {
      constructor(
        public anchor: any,
        public active: any
      ) {}
      get isEmpty() {
        return true;
      }
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
    Diagnostic: class {
      source?: string;
      code?: string;
      constructor(
        public range: any,
        public message: string,
        public severity: number
      ) {}
    },
    DiagnosticSeverity: {
      Error: 0,
      Warning: 1,
      Information: 2,
      Hint: 3,
    },
    Uri: {
      file: (path: string) => ({ fsPath: path, scheme: "file", toString: () => path }),
      parse: (str: string) => ({ fsPath: str, scheme: "file", toString: () => str }),
      joinPath: (base: { fsPath: string }, ...parts: string[]) => {
        const normalized = [base.fsPath, ...parts].join("/").replace(/\\/g, "/");
        return {
          fsPath: normalized,
          scheme: "file",
          toString: () => normalized,
        };
      },
    },
    workspace: {
      getConfiguration: () => ({
        get: (_key: string, defVal: any) => defVal,
      }),
      onDidSaveTextDocument: () => ({ dispose: () => {} }),
      workspaceFolders: [],
    },
    languages: {
      createDiagnosticCollection: () => ({
        set: () => {},
        delete: () => {},
        clear: () => {},
        dispose: () => {},
      }),
    },
    window: {
      onDidChangeActiveTextEditor: () => ({ dispose: () => {} }),
      activeTextEditor: undefined,
    },
  };
});
