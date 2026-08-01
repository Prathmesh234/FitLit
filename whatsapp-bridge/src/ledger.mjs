import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

import { ensurePrivateDirectory } from './config.mjs'

const PROCESSING_STALE_MS = 15 * 60 * 1000

function emptyState() {
  return {
    version: 1,
    inbound: {},
    outbound: {},
  }
}

function assertState(value) {
  if (
    !value
    || value.version !== 1
    || typeof value.inbound !== 'object'
    || Array.isArray(value.inbound)
    || typeof value.outbound !== 'object'
    || Array.isArray(value.outbound)
  ) {
    throw new Error('WhatsApp metadata ledger is malformed')
  }
}

export class MetadataLedger {
  constructor(filePath, maximumIds = 2000) {
    this.filePath = filePath
    this.maximumIds = maximumIds
    ensurePrivateDirectory(path.dirname(filePath))
    if (fs.existsSync(filePath) && fs.lstatSync(filePath).isSymbolicLink()) {
      throw new Error('refusing symlinked WhatsApp metadata ledger')
    }
    this.state = emptyState()
    if (fs.existsSync(filePath)) {
      this.state = JSON.parse(fs.readFileSync(filePath, 'utf8'))
      assertState(this.state)
      fs.chmodSync(filePath, 0o600)
    }
  }

  hasOutbound(messageId) {
    return Boolean(this.state.outbound[messageId])
  }

  shouldSkipInbound(messageId, now = Date.now()) {
    const row = this.state.inbound[messageId]
    if (!row) {
      return false
    }
    if (row.status === 'sent') {
      return true
    }
    return (
      row.status === 'processing'
      && now - Number(row.updatedAt || 0) < PROCESSING_STALE_MS
    )
  }

  reserveInbound(messageId) {
    this.state.inbound[messageId] = {
      status: 'processing',
      updatedAt: Date.now(),
    }
    this.#save()
  }

  finishInbound(messageId, outboundIds) {
    this.state.inbound[messageId] = {
      status: 'sent',
      updatedAt: Date.now(),
      outboundIds: [...outboundIds],
    }
    this.#prune()
    this.#save()
  }

  retryInbound(messageId) {
    this.state.inbound[messageId] = {
      status: 'retryable',
      updatedAt: Date.now(),
    }
    this.#prune()
    this.#save()
  }

  rememberOutbound(messageId) {
    this.state.outbound[messageId] = Date.now()
    this.#prune()
    this.#save()
  }

  #prune() {
    for (const name of ['inbound', 'outbound']) {
      const entries = Object.entries(this.state[name])
      if (entries.length <= this.maximumIds) {
        continue
      }
      entries.sort((left, right) => {
        const leftTime = Number(
          typeof left[1] === 'object' ? left[1].updatedAt : left[1],
        )
        const rightTime = Number(
          typeof right[1] === 'object' ? right[1].updatedAt : right[1],
        )
        return rightTime - leftTime
      })
      this.state[name] = Object.fromEntries(
        entries.slice(0, this.maximumIds),
      )
    }
  }

  #save() {
    const temporary = `${this.filePath}.${process.pid}.${randomUUID()}.tmp`
    const descriptor = fs.openSync(
      temporary,
      fs.constants.O_WRONLY
        | fs.constants.O_CREAT
      | fs.constants.O_EXCL,
      0o600,
    )
    try {
      fs.writeFileSync(
        descriptor,
        JSON.stringify(this.state),
        'utf8',
      )
      fs.fsyncSync(descriptor)
    } finally {
      fs.closeSync(descriptor)
    }
    try {
      fs.renameSync(temporary, this.filePath)
      fs.chmodSync(this.filePath, 0o600)
      const directory = fs.openSync(path.dirname(this.filePath), 'r')
      try {
        fs.fsyncSync(directory)
      } finally {
        fs.closeSync(directory)
      }
    } finally {
      if (fs.existsSync(temporary)) {
        fs.unlinkSync(temporary)
      }
    }
  }
}
