import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");

const buildOptions = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  external: ["vscode"],
  format: "cjs",
  platform: "node",
  target: "node18",
  sourcemap: true,
  minify: false,
  logLevel: "info",
};

if (watch) {
  const ctx = await esbuild.context(buildOptions);
  await ctx.watch();
  console.log("Watching for changes in extensions/vscode/src...");
} else {
  await esbuild.build(buildOptions);
  console.log("Successfully built extensions/vscode/dist/extension.js");
}
