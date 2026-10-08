import test from "node:test"
import assert from "node:assert/strict"
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { spawnSync } from "node:child_process"

test("CLI dry-run defaults omitted --agents to auto", () => {
  const home = mkdtempSync(join(tmpdir(), "stock-cli-"))
  try {
    const result = spawnSync(process.execPath, ["bin/stock-analysis.js", "install", "--dry-run"], {
      cwd: process.cwd(), encoding: "utf8", env: { ...process.env, STOCK_ANALYSIS_HOME: home }
    })
    assert.equal(result.status, 0)
    assert.equal(JSON.parse(result.stdout).dryRun, true)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test("CLI rejects an option without a value before configuring", () => {
  const result = spawnSync(process.execPath, ["bin/stock-analysis.js", "install", "--agents"], {
    cwd: process.cwd(), encoding: "utf8"
  })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /requires a value/)
})

test("stale runtime metadata falls back to PATH Python", async () => {
  const home = mkdtempSync(join(tmpdir(), "stock-runtime-"))
  const original = process.env.STOCK_ANALYSIS_HOME
  const python = process.env.STOCK_ANALYSIS_PYTHON
  try {
    process.env.STOCK_ANALYSIS_HOME = home
    delete process.env.STOCK_ANALYSIS_PYTHON
    mkdirSync(join(home, ".stock-analysis"))
    writeFileSync(join(home, ".stock-analysis", "runtime.json"), '{"python":"missing-python-file"}')
    const { resolveRuntimePython } = await import("../lib/installer.js")
    assert.equal(resolveRuntimePython(), process.env.EASTMONEY_PYTHON || "python")
    writeFileSync(join(home, ".stock-analysis", "runtime.json"), '{invalid')
    assert.equal(resolveRuntimePython(), process.env.EASTMONEY_PYTHON || "python")
  } finally {
    if (original === undefined) delete process.env.STOCK_ANALYSIS_HOME
    else process.env.STOCK_ANALYSIS_HOME = original
    if (python === undefined) delete process.env.STOCK_ANALYSIS_PYTHON
    else process.env.STOCK_ANALYSIS_PYTHON = python
    rmSync(home, { recursive: true, force: true })
  }
})
