import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
)

function integer(env, name, fallback, minimum, maximum) {
  const raw = env[name] ?? String(fallback)
  if (!/^\d+$/.test(raw)) {
    throw new Error(`${name} must be an integer`)
  }
  return Math.max(minimum, Math.min(maximum, Number(raw)))
}

function boolean(env, name, fallback = false) {
  const raw = String(env[name] ?? fallback).toLowerCase()
  return ['1', 'true', 'yes', 'on'].includes(raw)
}

export function normalizeE164(value) {
  const raw = String(value ?? '').trim()
  if (!/^\+[1-9]\d{7,14}$/.test(raw)) {
    throw new Error(
      'FITLIT_WHATSAPP_TRUSTED_USER_E164 must be one E.164 number',
    )
  }
  return raw
}

export function loadConfig(env = process.env) {
  const trustedE164 = normalizeE164(
    env.FITLIT_WHATSAPP_TRUSTED_USER_E164,
  )
  return {
    root: ROOT,
    enabled: boolean(env, 'FITLIT_WHATSAPP_ENABLED'),
    trustedE164,
    trustedDigits: trustedE164.slice(1),
    authDir: path.resolve(
      env.FITLIT_WHATSAPP_AUTH_DIR
        ?? path.join(ROOT, 'data', 'state', 'whatsapp-auth'),
    ),
    ledgerPath: path.resolve(
      env.FITLIT_WHATSAPP_LEDGER_PATH
        ?? path.join(ROOT, 'data', 'state', 'whatsapp-ledger.json'),
    ),
    pythonPath: path.resolve(
      env.FITLIT_WHATSAPP_PYTHON
        ?? path.join(ROOT, '.venv', 'bin', 'python'),
    ),
    contextMessages: integer(
      env,
      'FITLIT_WHATSAPP_CONTEXT_MESSAGES',
      5,
      1,
      5,
    ),
    bodyMaxChars: integer(
      env,
      'FITLIT_WHATSAPP_BODY_MAX_CHARS',
      2000,
      100,
      10000,
    ),
    replyMaxChars: integer(
      env,
      'FITLIT_WHATSAPP_REPLY_MAX_CHARS',
      12000,
      500,
      60000,
    ),
    agentTimeoutMs: integer(
      env,
      'FITLIT_WHATSAPP_AGENT_TIMEOUT_SECONDS',
      240,
      30,
      900,
    ) * 1000,
    maxAgentOutputBytes: integer(
      env,
      'FITLIT_WHATSAPP_AGENT_MAX_OUTPUT_BYTES',
      10000000,
      1000000,
      30000000,
    ),
    maxDocumentBytes: integer(
      env,
      'FITLIT_WHATSAPP_MAX_DOCUMENT_BYTES',
      5000000,
      1000,
      20000000,
    ),
    maxDocuments: integer(
      env,
      'FITLIT_WHATSAPP_MAX_DOCUMENTS',
      2,
      0,
      4,
    ),
    maxLedgerIds: integer(
      env,
      'FITLIT_WHATSAPP_MAX_LEDGER_IDS',
      2000,
      100,
      10000,
    ),
    startupSkewSeconds: integer(
      env,
      'FITLIT_WHATSAPP_STARTUP_SKEW_SECONDS',
      5,
      0,
      60,
    ),
  }
}

export function ensurePrivateDirectory(directory) {
  if (fs.existsSync(directory)) {
    if (fs.lstatSync(directory).isSymbolicLink()) {
      throw new Error(`refusing symlinked private directory: ${directory}`)
    }
    if (!fs.statSync(directory).isDirectory()) {
      throw new Error(`private path is not a directory: ${directory}`)
    }
  } else {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  }
  fs.chmodSync(directory, 0o700)
}
