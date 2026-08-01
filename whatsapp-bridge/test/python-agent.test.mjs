import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import { draftReply } from '../src/python-agent.mjs'

function executable(root, response) {
  const script = path.join(root, 'fake-agent')
  const encoded = Buffer.from(JSON.stringify(response)).toString('base64')
  fs.writeFileSync(
    script,
    [
      '#!/usr/bin/env node',
      "process.stdin.resume()",
      "process.stdin.on('end', () => {",
      `  process.stdout.write(Buffer.from('${encoded}', 'base64'))`,
      '})',
      '',
    ].join('\n'),
    { mode: 0o700 },
  )
  return script
}

function config(root, response) {
  return {
    pythonPath: executable(root, response),
    root,
    agentTimeoutMs: 2000,
    maxAgentOutputBytes: 10000,
    maxDocumentBytes: 100,
    maxDocuments: 2,
    replyMaxChars: 100,
  }
}

function response(overrides = {}) {
  const content = Buffer.from('private workbook bytes')
  return {
    ok: true,
    text: 'Grounded answer',
    documents: [{
      filename: 'fitlit-evidence.xlsx',
      mime_type: (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
      ),
      size: content.length,
      sha256: crypto.createHash('sha256').update(content).digest('hex'),
      content_base64: content.toString('base64'),
    }],
    ...overrides,
  }
}

test('accepts hash-checked in-memory documents from Python', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fitlit-wa-agent-'))
  try {
    const value = await draftReply(config(root, response()), [{
      role: 'user',
      content: 'question',
      internal_date_ms: 1,
    }])
    assert.equal(value.text, 'Grounded answer')
    assert.equal(value.documents[0].content.toString(), 'private workbook bytes')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('rejects documents with a mismatched digest', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fitlit-wa-agent-'))
  try {
    const invalid = response()
    invalid.documents[0].sha256 = '0'.repeat(64)
    await assert.rejects(
      draftReply(config(root, invalid), [{
        role: 'user',
        content: 'question',
        internal_date_ms: 1,
      }]),
    )
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
