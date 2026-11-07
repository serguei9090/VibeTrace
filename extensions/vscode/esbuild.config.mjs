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

try {
  if (watch) {
    const ctx = await esbuild.context(buildOptions);
    try {
      await ctx.watch();
      console.log("Watching for changes in extensions/vscode/src...");
    } catch (err) {
      await ctx.dispose();
      throw err;
    }
  } else {
    await esbuild.build(buildOptions);
    console.log("Successfully built extensions/vscode/dist/extension.js");
  }
} catch (err) {
  console.error("esbuild failure in extensions/vscode:", err);
  process.exitCode = 1;
}
