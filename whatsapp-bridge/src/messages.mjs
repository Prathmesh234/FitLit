import {
  isJidBroadcast,
  isJidGroup,
  isJidNewsletter,
  isJidStatusBroadcast,
  jidNormalizedUser,
  normalizeMessageContent,
} from 'baileys'

function timestampSeconds(value) {
  if (typeof value === 'number') {
    return value
  }
  if (typeof value === 'bigint') {
    return Number(value)
  }
  if (value && typeof value.toNumber === 'function') {
    return value.toNumber()
  }
  return Number(value || 0)
}

function directJid(jid) {
  return Boolean(
    jid
    && !isJidGroup(jid)
    && !isJidBroadcast(jid)
    && !isJidStatusBroadcast(jid)
    && !isJidNewsletter(jid),
  )
}

function chatJids(message) {
  const key = message.key || {}
  return [
    key.remoteJid,
    key.remoteJidAlt,
  ].filter(Boolean)
}

export function extractText(message) {
  const content = normalizeMessageContent(message.message)
  if (!content) {
    return ''
  }
  return String(
    content.conversation
      ?? content.extendedTextMessage?.text
      ?? content.imageMessage?.caption
      ?? content.videoMessage?.caption
      ?? content.documentMessage?.caption
      ?? '',
  ).trim()
}

export function classifyMessage(
  message,
  {
    trustedDigits,
    ownJids,
    outboundIds,
    startedAtSeconds,
    startupSkewSeconds,
    bodyMaxChars,
    upsertType,
  },
) {
  const messageId = String(message.key?.id || '')
  if (!messageId || outboundIds.has(messageId)) {
    return { accepted: false, reason: 'generated-or-missing-id' }
  }
  if (!['notify', 'append'].includes(upsertType)) {
    return { accepted: false, reason: 'non-live-upsert' }
  }
  if (message.key?.fromMe !== true) {
    return { accepted: false, reason: 'not-self-authored' }
  }
  const candidates = chatJids(message)
  if (!candidates.length || candidates.some(jid => !directJid(jid))) {
    return { accepted: false, reason: 'non-direct-chat' }
  }
  const allowed = new Set([
    `${trustedDigits}@s.whatsapp.net`,
    ...[...ownJids].map(jidNormalizedUser),
  ])
  const trusted = candidates.some(jid => allowed.has(jidNormalizedUser(jid)))
  if (!trusted) {
    return { accepted: false, reason: 'untrusted-chat' }
  }
  const sentAtSeconds = timestampSeconds(message.messageTimestamp)
  if (
    !sentAtSeconds
    || sentAtSeconds < startedAtSeconds - startupSkewSeconds
  ) {
    return { accepted: false, reason: 'pre-start-message' }
  }
  const text = extractText(message)
  if (!text) {
    return {
      accepted: false,
      reason: 'unsupported-message',
      trusted: true,
      messageId,
      chatJid: message.key.remoteJid,
    }
  }
  if (text.length > bodyMaxChars) {
    return {
      accepted: false,
      reason: 'message-too-long',
      trusted: true,
      messageId,
      chatJid: message.key.remoteJid,
    }
  }
  return {
    accepted: true,
    messageId,
    chatJid: message.key.remoteJid,
    sentAtMs: Math.trunc(sentAtSeconds * 1000),
    text,
  }
}
