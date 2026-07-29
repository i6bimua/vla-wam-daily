import { mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawn } from "node:child_process";

const dist = resolve("dist");
const paperRoot = resolve(dist, "papers");
const pagefindOutput = resolve(dist, "pagefind");

async function containsHtml(directory) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }

  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory() && (await containsHtml(path))) return true;
    if (entry.isFile() && entry.name.endsWith(".html")) return true;
  }
  return false;
}

async function runPagefind() {
  await new Promise((resolvePromise, reject) => {
    const child = spawn(
      "pagefind",
      ["--site", "dist", "--glob", "papers/**/*.html"],
      { stdio: "inherit" },
    );
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      reject(
        new Error(
          signal
            ? `Pagefind terminated by signal ${signal}`
            : `Pagefind exited with code ${code}`,
        ),
      );
    });
  });
}

async function writeEmptyPagefind() {
  await rm(pagefindOutput, { recursive: true, force: true });
  await mkdir(pagefindOutput, { recursive: true });
  await Promise.all([
    writeFile(
      resolve(pagefindOutput, "pagefind.js"),
      [
        "export async function options() {}",
        "export async function search() {",
        "  return { results: [], unfilteredResultCount: 0, filters: {} };",
        "}",
        "export async function destroy() {}",
        "",
      ].join("\n"),
      "utf8",
    ),
    writeFile(
      resolve(pagefindOutput, "pagefind-entry.json"),
      `${JSON.stringify({ languages: {} })}\n`,
      "utf8",
    ),
  ]);
}

if (await containsHtml(paperRoot)) {
  await runPagefind();
} else {
  await writeEmptyPagefind();
}
