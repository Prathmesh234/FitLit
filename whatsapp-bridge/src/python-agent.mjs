import crypto from 'node:crypto'
import path from 'node:path'
import { spawn } from 'node:child_process'

const DOCUMENT_MIME_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
])
const DOCUMENT_EXTENSIONS = new Map([
  [
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xlsx',
  ],
  [
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.docx',
  ],
])

function validateDocument(value, config) {
  if (
    !value
    || typeof value.filename !== 'string'
    || value.filename.length < 1
    || value.filename.length > 255
    || value.filename.includes('\0')
    || path.basename(value.filename) !== value.filename
    || !DOCUMENT_MIME_TYPES.has(value.mime_type)
    || !value.filename.toLowerCase().endsWith(
      DOCUMENT_EXTENSIONS.get(value.mime_type) || '',
    )
    || typeof value.content_base64 !== 'string'
    || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
      value.content_base64,
    )
    || typeof value.sha256 !== 'string'
    || !/^[a-f0-9]{64}$/.test(value.sha256)
    || !Number.isInteger(value.size)
    || value.size < 1
    || value.size > config.maxDocumentBytes
  ) {
    throw new Error('WhatsApp agent returned an invalid document')
  }
  const content = Buffer.from(value.content_base64, 'base64')
  if (content.length !== value.size) {
    throw new Error('WhatsApp agent document size did not match')
  }
  const digest = crypto.createHash('sha256').update(content).digest('hex')
  if (digest !== value.sha256) {
    throw new Error('WhatsApp agent document digest did not match')
  }
  return {
    filename: value.filename,
    mimeType: value.mime_type,
    content,
  }
}

export function draftReply(config, turns) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      config.pythonPath,
      ['-m', 'fitlit.whatsapp_agent'],
      {
        cwd: config.root,
        env: process.env,
        stdio: ['pipe', 'pipe', 'ignore'],
      },
    )
    let output = Buffer.alloc(0)
    let settled = false
    const finish = (callback, value) => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      callback(value)
    }
    const timer = setTimeout(() => {
      child.kill('SIGTERM')
      finish(reject, new Error('WhatsApp agent timed out'))
    }, config.agentTimeoutMs)
    child.stdout.on('data', chunk => {
      output = Buffer.concat([output, chunk])
      if (output.length > config.maxAgentOutputBytes) {
        child.kill('SIGTERM')
        finish(
          reject,
          new Error('WhatsApp agent output exceeded the size limit'),
        )
      }
    })
    child.on('error', () => {
      finish(reject, new Error('WhatsApp agent could not start'))
    })
    child.stdin.on('error', () => {
      finish(reject, new Error('WhatsApp agent input failed'))
    })
    child.on('close', code => {
      if (settled) {
        return
      }
      if (code !== 0) {
        finish(reject, new Error('WhatsApp agent failed'))
        return
      }
      try {
        const value = JSON.parse(output.toString('utf8'))
        if (
          value?.ok !== true
          || typeof value.text !== 'string'
          || value.text.length < 1
          || value.text.length > config.replyMaxChars
          || !Array.isArray(value.documents)
          || value.documents.length > config.maxDocuments
        ) {
          throw new Error('WhatsApp agent returned an invalid response')
        }
        finish(resolve, {
          text: value.text,
          documents: value.documents.map(
            document => validateDocument(document, config),
          ),
        })
      } catch {
        finish(reject, new Error('WhatsApp agent returned invalid JSON'))
      }
    })
    child.stdin.end(JSON.stringify({
      turns,
      now_ms: Date.now(),
    }))
  })
}
