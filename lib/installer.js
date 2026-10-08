import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs"
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { createInterface } from "node:readline/promises"
import { stdin as input, stdout as output } from "node:process"
import { basename, dirname, join, resolve, sep } from "node:path"
import { ADAPTERS, manualTemplates } from "./adapters.js"
import { appConfigPath, appRoot, backupDir, installStatePath, runtimeDir, runtimePath } from "./paths.js"

const now = () => new Date().toISOString().replace(/[:.]/g, "-")
const commandFor = (packageRoot) => ({ command: process.execPath, args: [join(packageRoot, "index.js")] })
const readState = () => existsSync(installStatePath()) ? JSON.parse(readFileSync(installStatePath(), "utf8")) : { agents: {} }
const writeState = (state) => { mkdirSync(appRoot(), { recursive: true }); writeFileSync(installStatePath(), `${JSON.stringify(state, null, 2)}\n`) }
const hasUv = () => spawnSync("uv", ["--version"], { encoding: "utf8" }).status === 0
const fileHash = (path) => existsSync(path)
  ? createHash("sha256").update(readFileSync(path)).digest("hex")
  : null

function backup(path) {
  if (!existsSync(path)) return null
  mkdirSync(backupDir(), { recursive: true })
  const destination = join(backupDir(), `${now()}-${path.split(/[\\/]/).pop()}`)
  writeFileSync(destination, readFileSync(path))
  return destination
}

export function writeConfig(dataRoot) {
  mkdirSync(appRoot(), { recursive: true })
  const config = `# Managed by stock-analysis setup\ndata_root = ${JSON.stringify(dataRoot)}\nstock_data_dir = ${JSON.stringify(join(dataRoot, "股票信息"))}\nsector_data_dir = ${JSON.stringify(join(dataRoot, "分析板块"))}\n`
  writeFileSync(appConfigPath(), config)
}

export function setupRuntime(packageRoot) {
  if (!hasUv()) throw new Error("uv 未安装。请先安装 uv：https://docs.astral.sh/uv/ ，然后重新执行 stock-analysis install。")
  const envDir = runtimeDir(); mkdirSync(dirname(envDir), { recursive: true })
  const python = process.platform === "win32" ? join(envDir, "Scripts", "python.exe") : join(envDir, "bin", "python")
  if (!existsSync(python)) {
    const created = spawnSync("uv", ["venv", envDir], { encoding: "utf8" })
    if (created.status !== 0) throw new Error(created.stderr || "uv venv 创建失败")
  }
  const installed = spawnSync("uv", ["pip", "install", "--python", python, `${packageRoot}[chart]`], { encoding: "utf8" })
  if (installed.status !== 0) throw new Error(installed.stderr || "Python 依赖安装失败")
  writeFileSync(runtimePath(), `${JSON.stringify({ python, packageRoot, created_at: new Date().toISOString() }, null, 2)}\n`)
  return python
}

export function resolveRuntimePython() {
  if (process.env.STOCK_ANALYSIS_PYTHON) return process.env.STOCK_ANALYSIS_PYTHON
  if (process.env.EASTMONEY_PYTHON) return process.env.EASTMONEY_PYTHON
  if (existsSync(runtimePath())) {
    try {
      const python = JSON.parse(readFileSync(runtimePath(), "utf8")).python
      if (typeof python === "string" && existsSync(python)) return python
    } catch (_) { /* damaged/stale runtime metadata: use PATH Python */ }
  }
  return "python"
}

export async function setup({ dataRoot, confirm = true }) {
  const root = dataRoot || join(appRoot(), "data")
  if (!confirm) return { dryRun: true, config: appConfigPath(), dataRoot: root }
  writeConfig(root)
  return { config: appConfigPath(), dataRoot: root }
}

export async function install({ packageRoot, agents = "auto", dryRun = false, setupDataRoot }) {
  if (agents !== "auto" && agents.split(",").some((id) => !ADAPTERS[id])) {
    throw new Error(`Unknown agent selection: ${agents}`)
  }
  const selected = agents === "auto" ? Object.values(ADAPTERS).filter((adapter) => adapter.detect()) : agents.split(",").map((id) => ADAPTERS[id]).filter(Boolean)
  const command = commandFor(packageRoot)
  const result = { selected: selected.map((adapter) => adapter.id), manual: manualTemplates(command), dryRun, actions: [] }
  if (dryRun) return result
  if (!hasUv()) throw new Error("uv 未安装。请先安装 uv：https://docs.astral.sh/uv/ ，然后重新执行 stock-analysis install。")
  setupRuntime(packageRoot)
  if (setupDataRoot) await setup({ dataRoot: setupDataRoot })
  const state = readState()
  for (const adapter of selected) {
    const config = adapter.configPath(); const saved = backup(config)
    const installed = adapter.install(packageRoot, command)
    state.agents[adapter.id] = {
      ...installed,
      backup: saved,
      configHash: fileHash(installed.configPath),
      installed_at: new Date().toISOString(),
    }
    result.actions.push({ agent: adapter.id, ...installed, backup: saved })
  }
  writeState(state)
  return result
}

export function doctor() {
  const errors = []
  let state = { agents: {} }, runtime = null
  try { state = readState() } catch (error) { errors.push(`install state: ${error.message}`) }
  try { runtime = existsSync(runtimePath()) ? JSON.parse(readFileSync(runtimePath(), "utf8")) : null } catch (error) { errors.push(`runtime metadata: ${error.message}`) }
  const python = resolveRuntimePython()
  const probe = spawnSync(python, ["-c", "import json,sys; import stock_analysis_mcp.server as s; import matplotlib; print(json.dumps({'python':sys.version,'tools':len(s.TOOL_HANDLERS),'chart':matplotlib.__version__}))"], { encoding: "utf8", timeout: 15000, windowsHide: true })
  return {
    errors,
    python: { executable: python, healthy: probe.status === 0,
      diagnostics: probe.status === 0 ? probe.stdout.trim() : (probe.stderr || probe.error?.message || "Python probe failed") },
    uv: hasUv(), runtime: runtime ? { ...runtime, exists: existsSync(runtime.python) } : null,
    config: { path: appConfigPath(), exists: existsSync(appConfigPath()) },
    agents: Object.fromEntries(Object.entries(ADAPTERS).map(([id, adapter]) => [id, { detected: adapter.detect(), configured: Boolean(state.agents[id]), config: adapter.configPath() }])),
  }
}

export function uninstall(agent) {
  const state = readState(); const ids = agent ? [agent] : Object.keys(state.agents)
  const removed = []
  let skillsRemoved = 0
  for (const id of ids) {
    const record = state.agents[id]; if (!record) continue
    // Restore the backup only if the config is unchanged since installation.
    // Otherwise remove just our section so later user edits are preserved.
    const restored = Boolean(
      record.backup && existsSync(record.backup)
      && record.configHash && fileHash(record.configPath) === record.configHash
    )
    if (restored) writeFileSync(record.configPath, readFileSync(record.backup))
    const adapter = ADAPTERS[id]
    const adapterResult = adapter ? adapter.uninstall(!restored) : null
    // Same-process adapters may remove their skill files themselves; count those
    // removals as well as the recorded paths needed for cross-process cleanup.
    skillsRemoved += adapterResult?.skills?.length || 0
    if (adapter?.skillRoot) {
      const skillRoot = resolve(adapter.skillRoot())
      for (const skillPath of record.skills || []) {
        const resolved = resolve(skillPath)
        if (basename(resolved) === "SKILL.md" && resolved.startsWith(`${skillRoot}${sep}`)) {
          if (existsSync(resolved)) { rmSync(resolved, { force: true }); skillsRemoved += 1 }
        }
      }
    }
    delete state.agents[id]; removed.push(id)
  }
  if (Object.keys(state.agents).length === 0 && existsSync(runtimeDir())) rmSync(runtimeDir(), { recursive: true, force: true })
  writeState(state)
  return { removed, skillsRemoved, dataPreserved: true }
}

export async function askPostinstall(packageRoot) {
  if (!process.stdin.isTTY || process.env.CI) return false
  const rl = createInterface({ input, output })
  const answer = await rl.question("[stock-analysis] 自动配置已检测到的 Agent（Claude Code / Codex / Cursor / Copilot / Qoder）和本地数据目录？ [y/N] ")
  if (!/^y(es)?$/i.test(answer.trim())) { rl.close(); return false }
  const defaultRoot = join(appRoot(), "data")
  const dataRoot = (await rl.question(`[stock-analysis] SQLite 数据目录 [${defaultRoot}]：`)).trim() || defaultRoot
  rl.close()
  await install({ packageRoot, agents: "auto", setupDataRoot: dataRoot })
  return true
}
