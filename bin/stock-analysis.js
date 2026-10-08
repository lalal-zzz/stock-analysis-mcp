#!/usr/bin/env node
import { dirname } from "node:path"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { createInterface } from "node:readline/promises"
import { stdin as input, stdout as output } from "node:process"
import { askPostinstall, doctor, install, setup, uninstall } from "../lib/installer.js"
import { appConfigPath, appRoot } from "../lib/paths.js"

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const [command = "help", ...args] = process.argv.slice(2)
const value = (name) => {
  const index = args.indexOf(name)
  if (index < 0) return undefined
  const next = args[index + 1]
  if (!next || next.startsWith("--")) throw new Error(`${name} requires a value`)
  return next
}
const print = (data) => console.log(typeof data === "string" ? data : JSON.stringify(data, null, 2))
async function requestedDataRoot() {
  const supplied = value("--data-root")
  if (supplied) return supplied
  if (!process.stdin.isTTY) throw new Error("请通过 --data-root 指定 SQLite 数据目录。")
  const defaultRoot = join(appRoot(), "data")
  const rl = createInterface({ input, output })
  const answer = await rl.question(`SQLite 数据目录 [${defaultRoot}]：`)
  rl.close()
  return answer.trim() || defaultRoot
}

try {
  if (command === "install") print(await install({ packageRoot: root, agents: value("--agents") || "auto", dryRun: args.includes("--dry-run"), setupDataRoot: args.includes("--dry-run") ? undefined : await requestedDataRoot() }))
  else if (command === "setup") print(await setup({ dataRoot: await requestedDataRoot() }))
  else if (command === "doctor") print(doctor())
  else if (command === "uninstall") print(uninstall(value("--agent")))
  else if (command === "config" && args[0] === "show") print({ path: appConfigPath() })
  else if (command === "postinstall") { const ran = await askPostinstall(root); if (!ran) console.log("[stock-analysis] 安装完成。运行 stock-analysis install --agents auto 配置 Agent。") }
  else print("Usage: stock-analysis <install|setup|doctor|uninstall|config show>\n  install --agents auto|claude-code,codex,cursor,copilot,qoder [--dry-run] [--data-root <dir>]")
} catch (error) { console.error(`[stock-analysis] ${error.message}`); process.exitCode = 1 }
