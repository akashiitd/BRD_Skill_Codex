import { spawn } from "node:child_process";
import { copyFile, mkdir, realpath, readFile, stat, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const sourceRoot = process.env.BRD_SOURCE_ROOT;
const jobsRoot = process.env.BRD_JOBS_ROOT;
const codexBin = process.env.CODEX_BIN || "codex";
const maxFilesPerJob = 20;
const maxRunMs = 15 * 60 * 1000;

function requireConfiguration() {
  if (!sourceRoot || !jobsRoot) {
    throw new Error("Set BRD_SOURCE_ROOT and BRD_JOBS_ROOT in .env.local before generating a BRD.");
  }
}

function isWithinRoot(root: string, candidate: string) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

async function approvedFile(rawPath: unknown, allowedRoot: string) {
  if (typeof rawPath !== "string" || !rawPath.trim()) {
    throw new Error("Each source path must be a non-empty string.");
  }

  // realpath prevents a symlink inside the approved folder from escaping it.
  const candidate = await realpath(rawPath);
  if (!isWithinRoot(allowedRoot, candidate)) {
    throw new Error(`Source file is outside BRD_SOURCE_ROOT: ${rawPath}`);
  }

  if (!(await stat(candidate)).isFile()) {
    throw new Error(`Source path is not a file: ${rawPath}`);
  }

  return candidate;
}

function runCodex(jobDirectory: string, finalMessagePath: string) {
  const prompt = [
    "Use $build-brd-from-files to create a traceable BRD from every source file in the input directory.",
    "Use source-manifest.json to preserve the original-file traceability after staging copies.",
    "Save the canonical Markdown BRD exactly at outputs/BRD.md.",
    "Do not modify source files. Flag unclear or unreadable evidence for stakeholder validation.",
  ].join("\n");

  const args = [
    "exec",
    "--cd", jobDirectory,
    "--sandbox", "workspace-write",
    "--skip-git-repo-check",
    "--output-last-message", finalMessagePath,
    prompt,
  ];

  return new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
    const child = spawn(codexBin, args, {
      cwd: jobDirectory,
      shell: false,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => child.kill(), maxRunMs);

    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timeout);
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`Codex exited with code ${code}. ${stderr.slice(-4000)}`));
    });
  });
}

export async function POST(request: NextRequest) {
  try {
    requireConfiguration();
    const body = await request.json() as { paths?: unknown };
    if (!Array.isArray(body.paths) || body.paths.length === 0 || body.paths.length > maxFilesPerJob) {
      return NextResponse.json({ error: `Provide between 1 and ${maxFilesPerJob} source-file paths.` }, { status: 400 });
    }

    const allowedRoot = await realpath(sourceRoot!);
    const sourceFiles = await Promise.all(body.paths.map((item) => approvedFile(item, allowedRoot)));
    const jobId = randomUUID();
    const jobDirectory = path.join(jobsRoot!, jobId);
    const inputDirectory = path.join(jobDirectory, "input");
    const outputDirectory = path.join(jobDirectory, "outputs");
    const brdPath = path.join(outputDirectory, "BRD.md");
    const finalMessagePath = path.join(jobDirectory, "final-message.txt");

    await Promise.all([mkdir(inputDirectory, { recursive: true }), mkdir(outputDirectory, { recursive: true })]);
    const stagedSources = sourceFiles.map((source, index) => ({
      originalPath: source,
      stagedName: `${String(index + 1).padStart(2, "0")}-${path.basename(source)}`,
    }));
    await writeFile(path.join(jobDirectory, "source-manifest.json"), JSON.stringify(stagedSources, null, 2));
    await Promise.all(stagedSources.map(({ originalPath, stagedName }) =>
      copyFile(originalPath, path.join(inputDirectory, stagedName)),
    ));

    await runCodex(jobDirectory, finalMessagePath);
    const finalMessage = await readFile(finalMessagePath, "utf8").catch(() => "Codex completed without a final message.");
    const brdExists = await stat(brdPath).then(() => true).catch(() => false);

    if (!brdExists) {
      return NextResponse.json({ error: "Codex completed but did not create outputs/BRD.md.", jobId, finalMessage }, { status: 502 });
    }

    return NextResponse.json({ jobId, brdPath, finalMessage });
  } catch (error) {
    console.error("BRD generation failed", error);
    return NextResponse.json({ error: error instanceof Error ? error.message : "BRD generation failed." }, { status: 500 });
  }
}
