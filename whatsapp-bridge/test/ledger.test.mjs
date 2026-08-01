import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import { MetadataLedger } from '../src/ledger.mjs'

test('stores metadata-only deduplication state with private modes', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fitlit-wa-ledger-'))
  try {
    const ledgerPath = path.join(root, 'state', 'ledger.json')
    const ledger = new MetadataLedger(ledgerPath, 10)
    ledger.reserveInbound('inbound-one')
    ledger.rememberOutbound('outbound-one')
    ledger.finishInbound('inbound-one', ['outbound-one'])

    assert.equal(ledger.shouldSkipInbound('inbound-one'), true)
    assert.equal(ledger.hasOutbound('outbound-one'), true)
    assert.equal(fs.statSync(path.dirname(ledgerPath)).mode & 0o777, 0o700)
    assert.equal(fs.statSync(ledgerPath).mode & 0o777, 0o600)

    const serialized = fs.readFileSync(ledgerPath, 'utf8')
    assert.equal(serialized.includes('message body'), false)
    assert.equal(serialized.includes('question'), false)
    assert.deepEqual(
      Object.keys(JSON.parse(serialized)).sort(),
      ['inbound', 'outbound', 'version'],
    )
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('retryable and stale processing rows can run again', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fitlit-wa-ledger-'))
  try {
    const ledgerPath = path.join(root, 'ledger.json')
    const ledger = new MetadataLedger(ledgerPath, 10)
    ledger.reserveInbound('retry')
    ledger.retryInbound('retry')
    assert.equal(ledger.shouldSkipInbound('retry'), false)

    const state = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'))
    state.inbound.stale = {
      status: 'processing',
      updatedAt: 1,
    }
    fs.writeFileSync(ledgerPath, JSON.stringify(state), { mode: 0o600 })
    const reloaded = new MetadataLedger(ledgerPath, 10)
    assert.equal(reloaded.shouldSkipInbound('stale', Date.now()), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
