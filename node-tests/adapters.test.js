import test from "node:test"
import assert from "node:assert/strict"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { createHash } from "node:crypto"

const home = mkdtempSync(join(tmpdir(), "stock-analysis-test-"))
process.env.STOCK_ANALYSIS_HOME = home
const { ADAPTERS } = await import("../lib/adapters.js")
const { install, uninstall } = await import("../lib/installer.js")
const { installStatePath } = await import("../lib/paths.js")

test.after(() => rmSync(home, { recursive: true, force: true }))

test("dry run never writes files", async () => {
  const result = await install({ packageRoot: join(process.cwd()), agents: "codex", dryRun: true })
  assert.equal(result.dryRun, true)
  assert.equal(existsSync(join(home, ".codex", "config.toml")), false)
})

test("Codex adapter preserves unrelated configuration and is idempotent", () => {
  const adapter = ADAPTERS.codex
  const path = adapter.configPath()
  mkdirSync(join(home, ".codex"), { recursive: true })
  writeFileSync(path, '[model]\nname = "example"\n')
  const command = { command: "node", args: ["server.js"] }
  adapter.install(process.cwd(), command)
  adapter.install(process.cwd(), command)
  const content = readFileSync(path, "utf8")
  assert.match(content, /\[model\]/)
  assert.equal((content.match(/\[mcp_servers\.stock-analysis\]/g) || []).length, 1)
})

test("Cursor adapter preserves other servers, registers no skills", () => {
  const adapter = ADAPTERS.cursor
  const path = adapter.configPath()
  mkdirSync(join(home, ".cursor"), { recursive: true })
  writeFileSync(path, JSON.stringify({ mcpServers: { other: { command: "x", args: [] } } }))
  const command = { command: "node", args: ["server.js"] }
  const first = adapter.install(process.cwd(), command)
  const second = adapter.install(process.cwd(), command)
  assert.equal(first.changed, true)
  assert.equal(second.changed, false)
  assert.equal(first.skills.length, 0)
  const config = JSON.parse(readFileSync(path, "utf8"))
  assert.deepEqual(config.mcpServers["stock-analysis"], command)
  assert.ok(config.mcpServers.other)
  adapter.uninstall()
  assert.equal("stock-analysis" in JSON.parse(readFileSync(path, "utf8")).mcpServers, false)
})

test("Copilot adapter writes VS Code servers format with stdio type", () => {
  const adapter = ADAPTERS.copilot
  const command = { command: "node", args: ["server.js"] }
  adapter.install(process.cwd(), command)
  const config = JSON.parse(readFileSync(adapter.configPath(), "utf8"))
  assert.deepEqual(config.servers["stock-analysis"], { type: "stdio", ...command })
})

test("Qoder adapter installs mcp server and skills", () => {
  const adapter = ADAPTERS.qoder
  mkdirSync(join(home, ".qoder"), { recursive: true })
  assert.equal(adapter.detect(), true)
  const command = { command: "node", args: ["server.js"] }
  const result = adapter.install(process.cwd(), command)
  const skillIds = readdirSync(join(process.cwd(), "skills"), { withFileTypes: true }).filter(entry => entry.isDirectory()).map(entry => entry.name)
  assert.equal(result.skills.length, skillIds.length)
  for (const id of skillIds) assert.ok(existsSync(join(home, ".qoder", "skills", id, "SKILL.md")))
  assert.ok(existsSync(join(home, ".qoder", "skills", "stock-analysis", "SKILL.md")))
  const config = JSON.parse(readFileSync(adapter.configPath(), "utf8"))
  assert.deepEqual(config.mcpServers["stock-analysis"], command)
  adapter.uninstall()
  assert.equal(existsSync(join(home, ".qoder", "skills", "stock-analysis")), false)
})

test("uninstall preserves later config edits and removes recorded skills", () => {
  const adapter = ADAPTERS.codex
  const configPath = adapter.configPath()
  mkdirSync(join(home, ".codex"), { recursive: true })
  writeFileSync(configPath, '[model]\nname = "example"\n')
  const backup = join(home, "backup-config.toml")
  writeFileSync(backup, readFileSync(configPath))
  adapter.install(process.cwd(), { command: "node", args: ["server.js"] })
  const installed = readFileSync(configPath, "utf8")
  const configHash = createHash("sha256").update(installed).digest("hex")
  writeFileSync(configPath, `${installed}\n[extra]\nvalue = true\n`)
  const skillPath = join(home, ".codex", "skills", "stock-analysis", "SKILL.md")
  mkdirSync(join(home, ".codex", "skills", "stock-analysis"), { recursive: true })
  writeFileSync(skillPath, "managed skill")
  mkdirSync(join(home, ".stock-analysis"), { recursive: true })
  writeFileSync(installStatePath(), `${JSON.stringify({ agents: { codex: { configPath, backup, configHash, skills: [skillPath] } } })}\n`)

  const result = uninstall("codex")
  const content = readFileSync(configPath, "utf8")
  assert.match(content, /\[extra\]/)
  assert.doesNotMatch(content, /mcp_servers\.stock-analysis/)
  assert.equal(existsSync(skillPath), false)
  assert.ok(result.skillsRemoved >= 1)
})
