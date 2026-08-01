import test from 'node:test'
import assert from 'node:assert/strict'

import { assertTrustedAccount, ownJids } from '../src/identity.mjs'

const DIGITS = '15551234567'
const pn = value => `${value}@s.whatsapp.net`

test('accepts the configured phone identity when the primary ID is a LID', () => {
  assert.doesNotThrow(() => assertTrustedAccount({
    id: '123456789@lid',
    phoneNumber: pn(DIGITS),
  }, DIGITS))
})

test('rejects a paired account with a different phone identity', () => {
  assert.throws(() => assertTrustedAccount({
    id: pn('15557654321'),
  }, DIGITS))
})

test('self-chat identity set includes PN and LID forms', () => {
  const values = ownJids({
    id: '123456789@lid',
    lid: '123456789@lid',
    phoneNumber: pn(DIGITS),
  }, DIGITS)
  assert.equal(values.has(pn(DIGITS)), true)
  assert.equal(values.has('123456789@lid'), true)
})
