// @vitest-environment node
// (plain Node, not the browser stand-in the other tests use: this test runs a program and reads the file system, and
// under jsdom `import.meta.url` is not a file address, so the folder could not be found)
//
// Guards the web page's production build (`npm run build`), which the Docker image runs in a stage that holds only
// the `frontend/` folder. A build that reads a file outside that folder works on a full checkout and fails in the image,
// which is how 1.12 broke it: a test read `backend/tests/filter_cases.json`, and no test run noticed, because every test
// run had the whole repository. This test lists the files the build compiles and fails if any lies outside `frontend/`.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// the `frontend/` folder (this file is in `frontend/src/`)
const FRONTEND = fileURLToPath(new URL("..", import.meta.url));

// every file the build's type-check reads, as absolute paths (`--listFilesOnly` builds the program and lists its files;
// it does not type-check, so this is quick). Files under node_modules are the installed packages, which the image gets
// from its own `npm ci`, so they are left out.
function buildFiles(): string[] {
  const tsc = path.join(FRONTEND, "node_modules", ".bin", "tsc");
  const out = execFileSync(tsc, ["-p", "tsconfig.build.json", "--listFilesOnly"], { cwd: FRONTEND, encoding: "utf8" });
  return out.split("\n").map((line) => line.trim()).filter((line) => line !== "" && !line.includes(`${path.sep}node_modules${path.sep}`));
}

describe("the production build (tsconfig.build.json)", () => {
  // without this the next test could pass on an empty list (a broken `include` would hide the very thing it guards)
  it("compiles the page itself", () => {
    const files = buildFiles().map((file) => path.relative(FRONTEND, file));
    expect(files).toContain(path.join("src", "main.tsx"));
    expect(files).toContain(path.join("src", "App.tsx"));
  });

  // the property the Docker stage needs: nothing the build reads is outside `frontend/`
  it("reads nothing from outside the frontend folder", () => {
    const outside = buildFiles().filter((file) => path.relative(FRONTEND, file).startsWith(".."));
    expect(outside).toEqual([]);
  });

  // the tests above check the config; this checks that `npm run build` (what the Dockerfile runs) is the one using it.
  // Were the script put back to `tsc -p .`, the file listing would stay clean while the image build broke again.
  it("is the config that `npm run build` type-checks with", () => {
    const pkg = JSON.parse(readFileSync(path.join(FRONTEND, "package.json"), "utf8"));
    expect(pkg.scripts.build).toMatch(/^tsc .*-p tsconfig\.build\.json\b.* && vite build$/);
  });

  // the tests are what reached outside; the image does not run them, so the build leaves them out. (`npm run typecheck`
  // still checks them.)
  it("leaves the tests out", () => {
    const tests = buildFiles().filter((file) => /\.test\.tsx?$/.test(file) || file.endsWith(`${path.sep}testdata.ts`));
    expect(tests).toEqual([]);
  });
}, 30_000);
