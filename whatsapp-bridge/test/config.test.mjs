import test from 'node:test'
import assert from 'node:assert/strict'

import { loadConfig, normalizeE164 } from '../src/config.mjs'

const NUMBER = '+' + '15551234567'

test('loads bounded private bridge defaults', () => {
  const config = loadConfig({
    FITLIT_WHATSAPP_TRUSTED_USER_E164: NUMBER,
  })
  assert.equal(config.trustedDigits, NUMBER.slice(1))
  assert.equal(config.contextMessages, 5)
  assert.equal(config.maxDocuments, 2)
  assert.equal(config.maxDocumentBytes, 5000000)
  assert.equal(config.replyMaxChars, 12000)
})

test('rejects malformed trusted numbers and clamps numeric settings', () => {
  assert.throws(() => normalizeE164('555-1234'))
  const config = loadConfig({
    FITLIT_WHATSAPP_TRUSTED_USER_E164: NUMBER,
    FITLIT_WHATSAPP_CONTEXT_MESSAGES: '99',
    FITLIT_WHATSAPP_MAX_DOCUMENTS: '99',
  })
  assert.equal(config.contextMessages, 5)
  assert.equal(config.maxDocuments, 4)
})
