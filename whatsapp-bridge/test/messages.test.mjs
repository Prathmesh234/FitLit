import test from 'node:test'
import assert from 'node:assert/strict'

import { classifyMessage, extractText } from '../src/messages.mjs'

const TRUSTED = '14092392410'
const CHAT = `${TRUSTED}@s.whatsapp.net`
const jid = digits => `${digits}@s.whatsapp.net`

function message(overrides = {}) {
  return {
    key: {
      id: 'message-one',
      remoteJid: CHAT,
      fromMe: true,
      ...(overrides.key || {}),
    },
    messageTimestamp: overrides.messageTimestamp ?? 200,
    message: overrides.message ?? { conversation: 'How did I sleep?' },
  }
}

function classify(value, overrides = {}) {
  return classifyMessage(value, {
    trustedDigits: TRUSTED,
    ownJids: new Set([CHAT]),
    outboundIds: new Set(),
    startedAtSeconds: 190,
    startupSkewSeconds: 5,
    bodyMaxChars: 2000,
    upsertType: 'notify',
    ...overrides,
  })
}

test('accepts one live trusted self-chat text message', () => {
  assert.deepEqual(classify(message()), {
    accepted: true,
    messageId: 'message-one',
    chatJid: CHAT,
    sentAtMs: 200000,
    text: 'How did I sleep?',
  })
})

test('extracts extended text without quoted content', () => {
  const value = message({
    message: {
      extendedTextMessage: {
        text: 'Latest question',
        contextInfo: {
          quotedMessage: { conversation: 'Old private content' },
        },
      },
    },
  })
  assert.equal(extractText(value), 'Latest question')
})

test('rejects generated, untrusted, group, and old messages', () => {
  assert.equal(classify(message(), {
    outboundIds: new Set(['message-one']),
  }).accepted, false)
  assert.equal(classify(message({
    key: { remoteJid: jid('15551234567') },
  })).accepted, false)
  assert.equal(classify(message({
    key: {
      remoteJid: jid('15551234567'),
      participant: CHAT,
    },
  })).accepted, false)
  assert.equal(classify(message({
    key: { remoteJid: '12345' + '@g.us' },
  })).accepted, false)
  assert.equal(classify(message({
    messageTimestamp: 100,
  })).accepted, false)
  assert.equal(classify(message({
    key: { fromMe: false },
  })).accepted, false)
})

test('identifies trusted unsupported and oversized messages', () => {
  const unsupported = classify(message({ message: { imageMessage: {} } }))
  assert.equal(unsupported.trusted, true)
  assert.equal(unsupported.reason, 'unsupported-message')

  const oversized = classify(
    message({ message: { conversation: 'x'.repeat(101) } }),
    { bodyMaxChars: 100 },
  )
  assert.equal(oversized.trusted, true)
  assert.equal(oversized.reason, 'message-too-long')
})
