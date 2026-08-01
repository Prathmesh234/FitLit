#!/usr/bin/env node
import qrcode from 'qrcode-terminal'
import pino from 'pino'
import makeWASocket, {
  DisconnectReason,
  generateMessageIDV2,
  isJidBroadcast,
  isJidGroup,
  isJidNewsletter,
  isJidStatusBroadcast,
  useMultiFileAuthState,
} from 'baileys'

import {
  ensurePrivateDirectory,
  loadConfig,
} from './config.mjs'
import { MetadataLedger } from './ledger.mjs'
import { assertTrustedAccount, ownJids } from './identity.mjs'
import { classifyMessage } from './messages.mjs'
import { draftReply } from './python-agent.mjs'

process.umask(0o077)

const logger = pino({ level: 'silent' })
const startedAtSeconds = Math.floor(Date.now() / 1000)
const CONFIGURATION_EXIT = 78

function disallowedJid(jid) {
  return Boolean(
    isJidGroup(jid)
    || isJidBroadcast(jid)
    || isJidStatusBroadcast(jid)
    || isJidNewsletter(jid),
  )
}

function makeSocket(auth) {
  return makeWASocket({
    auth,
    logger,
    emitOwnEvents: true,
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    enableRecentMessageCache: false,
    getMessage: async () => undefined,
    shouldIgnoreJid: jid => disallowedJid(jid),
  })
}

function disconnectStatus(lastDisconnect) {
  return Number(
    lastDisconnect?.error?.output?.statusCode
      ?? lastDisconnect?.error?.data?.statusCode
      ?? 0,
  )
}

async function pair(config) {
  ensurePrivateDirectory(config.authDir)
  const { state, saveCreds } = await useMultiFileAuthState(config.authDir)
  const socket = makeSocket(state)
  socket.ev.on('creds.update', () => {
    void saveCreds().catch(() => {
      console.error('WhatsApp pairing credential update failed.')
    })
  })
  let lastQr = ''

  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error('WhatsApp pairing timed out')),
        5 * 60 * 1000,
      )
      socket.ev.on('connection.update', update => {
        if (update.qr && update.qr !== lastQr) {
          lastQr = update.qr
          console.log(
            '\nScan this QR in WhatsApp: Settings > Linked Devices > Link a device\n',
          )
          qrcode.generate(update.qr, { small: true })
        }
        if (update.connection === 'open') {
          clearTimeout(timer)
          try {
            assertTrustedAccount(socket.user, config.trustedDigits)
            console.log('\nWhatsApp linked-device pairing completed.')
            resolve()
          } catch (error) {
            reject(error)
          }
        }
        if (
          update.connection === 'close'
          && disconnectStatus(update.lastDisconnect)
            === DisconnectReason.loggedOut
        ) {
          clearTimeout(timer)
          reject(new Error('WhatsApp rejected or logged out the linked device'))
        }
      })
    })
    await saveCreds()
  } finally {
    await socket.end(undefined)
  }
}

function appendContext(context, turn, maximum, bodyMaximum) {
  context.push({
    role: turn.role,
    content: turn.content.slice(0, bodyMaximum),
    internal_date_ms: turn.internal_date_ms,
  })
  while (context.length > maximum) {
    context.shift()
  }
}

async function sendText(socket, ledger, chatJid, text, quoted) {
  const messageId = generateMessageIDV2(socket.user?.id)
  ledger.rememberOutbound(messageId)
  const sent = await socket.sendMessage(
    chatJid,
    { text },
    { quoted, messageId },
  )
  const sentId = String(sent?.key?.id || messageId)
  if (sentId !== messageId) {
    ledger.rememberOutbound(sentId)
  }
  return sentId
}

async function sendReply(socket, ledger, chatJid, reply, quoted) {
  const outboundIds = [
    await sendText(socket, ledger, chatJid, reply.text, quoted),
  ]
  let documentFailure = false
  for (const document of reply.documents) {
    try {
      const messageId = generateMessageIDV2(socket.user?.id)
      ledger.rememberOutbound(messageId)
      const sent = await socket.sendMessage(
        chatJid,
        {
          document: document.content,
          mimetype: document.mimeType,
          fileName: document.filename,
        },
        { messageId },
      )
      const sentId = String(sent?.key?.id || messageId)
      if (sentId !== messageId) {
        ledger.rememberOutbound(sentId)
      }
      outboundIds.push(sentId)
    } catch {
      documentFailure = true
      break
    }
  }
  if (documentFailure) {
    try {
      outboundIds.push(await sendText(
        socket,
        ledger,
        chatJid,
        'A requested document could not be delivered.',
        undefined,
      ))
    } catch {
      // The main grounded reply was already delivered; do not duplicate it.
    }
  }
  return outboundIds
}

async function sendRejection(
  socket,
  ledger,
  message,
  classification,
) {
  const text = classification.reason === 'message-too-long'
    ? 'That message is too long for the private FitLit channel.'
    : 'FitLit currently accepts text messages in this self-chat.'
  ledger.reserveInbound(classification.messageId)
  try {
    const outboundIds = [
      await sendText(
        socket,
        ledger,
        classification.chatJid,
        text,
        message,
      ),
    ]
    ledger.finishInbound(classification.messageId, outboundIds)
  } catch {
    ledger.retryInbound(classification.messageId)
  }
}

async function handleMessage(
  socket,
  ledger,
  context,
  config,
  message,
  upsertType,
) {
  const classification = classifyMessage(message, {
    trustedDigits: config.trustedDigits,
    ownJids: ownJids(socket.user, config.trustedDigits),
    outboundIds: { has: id => ledger.hasOutbound(id) },
    startedAtSeconds,
    startupSkewSeconds: config.startupSkewSeconds,
    bodyMaxChars: config.bodyMaxChars,
    upsertType,
  })
  if (!classification.accepted) {
    if (
      classification.trusted
      && !ledger.shouldSkipInbound(classification.messageId)
    ) {
      await sendRejection(socket, ledger, message, classification)
    }
    return
  }
  if (ledger.shouldSkipInbound(classification.messageId)) {
    return
  }
  ledger.reserveInbound(classification.messageId)
  const turns = [
    ...context,
    {
      role: 'user',
      content: classification.text,
      internal_date_ms: classification.sentAtMs,
    },
  ].slice(-config.contextMessages)
  try {
    const reply = await draftReply(config, turns)
    const outboundIds = await sendReply(
      socket,
      ledger,
      classification.chatJid,
      reply,
      message,
    )
    ledger.finishInbound(classification.messageId, outboundIds)
    appendContext(
      context,
      {
        role: 'user',
        content: classification.text,
        internal_date_ms: classification.sentAtMs,
      },
      config.contextMessages,
      config.bodyMaxChars,
    )
    appendContext(
      context,
      {
        role: 'assistant',
        content: reply.text,
        internal_date_ms: Date.now(),
      },
      config.contextMessages,
      config.bodyMaxChars,
    )
    try {
      await socket.readMessages([message.key])
    } catch {
      // Read receipts are optional and do not affect reply delivery.
    }
    console.log('Processed one trusted WhatsApp self-chat message.')
  } catch {
    try {
      const outboundIds = [
        await sendText(
          socket,
          ledger,
          classification.chatJid,
          'FitLit could not prepare a grounded reply. Please try again.',
          message,
        ),
      ]
      ledger.finishInbound(classification.messageId, outboundIds)
    } catch {
      ledger.retryInbound(classification.messageId)
    }
  }
}

async function run(config) {
  if (!config.enabled) {
    throw new Error('FITLIT_WHATSAPP_ENABLED must be true')
  }
  ensurePrivateDirectory(config.authDir)
  const { state, saveCreds } = await useMultiFileAuthState(config.authDir)
  if (!state.creds.registered) {
    throw new Error(
      'WhatsApp is not paired; run: npm --prefix whatsapp-bridge run pair',
    )
  }
  assertTrustedAccount(state.creds.me, config.trustedDigits)
  const ledger = new MetadataLedger(
    config.ledgerPath,
    config.maxLedgerIds,
  )
  const context = []
  let socket
  let stopped = false
  let reconnectAttempt = 0
  let reconnectTimer
  let queue = Promise.resolve()
  let connectionGeneration = 0

  await new Promise(resolve => {
    const failRuntime = (message, exitCode = 1) => {
      if (stopped) {
        return
      }
      stopped = true
      connectionGeneration += 1
      clearTimeout(reconnectTimer)
      console.error(message)
      process.exitCode = exitCode
      void socket?.end(undefined).catch(() => {})
      resolve()
    }

    const connect = () => {
      if (stopped) {
        return
      }
      const generation = ++connectionGeneration
      const currentSocket = makeSocket(state)
      socket = currentSocket
      currentSocket.ev.on('creds.update', () => {
        if (generation !== connectionGeneration) {
          return
        }
        void saveCreds().catch(() => {
          failRuntime('WhatsApp credential update failed.')
        })
      })
      currentSocket.ev.on('messages.upsert', event => {
        if (generation !== connectionGeneration) {
          return
        }
        for (const message of event.messages || []) {
          queue = queue
            .then(() => handleMessage(
              currentSocket,
              ledger,
              context,
              config,
              message,
              event.type,
            ))
            .catch(() => {
              console.error('WhatsApp message processing failed.')
            })
        }
      })
      currentSocket.ev.on('connection.update', update => {
        if (generation !== connectionGeneration) {
          return
        }
        if (update.connection === 'open') {
          try {
            assertTrustedAccount(currentSocket.user, config.trustedDigits)
          } catch {
            failRuntime(
              'Paired WhatsApp account does not match trusted configuration.',
              CONFIGURATION_EXIT,
            )
            return
          }
          reconnectAttempt = 0
          console.log(
            `FitLit WhatsApp self-chat listener started with `
            + `${config.contextMessages}-message context.`,
          )
          return
        }
        if (update.connection !== 'close' || stopped) {
          return
        }
        if (
          disconnectStatus(update.lastDisconnect)
          === DisconnectReason.loggedOut
        ) {
          failRuntime(
            'WhatsApp linked device was logged out; pairing is required.',
            CONFIGURATION_EXIT,
          )
          return
        }
        reconnectAttempt += 1
        const delay = Math.min(60000, 2000 * 2 ** (reconnectAttempt - 1))
        clearTimeout(reconnectTimer)
        reconnectTimer = setTimeout(connect, delay)
      })
    }

    const stop = async () => {
      if (stopped) {
        return
      }
      stopped = true
      clearTimeout(reconnectTimer)
      try {
        await socket?.end(undefined)
      } catch {
        // The transport may already be closed.
      }
      resolve()
    }
    process.once('SIGTERM', stop)
    process.once('SIGINT', stop)
    connect()
  })
}

async function main() {
  const mode = process.argv[2]
  if (!['pair', 'run'].includes(mode)) {
    throw new Error('usage: bridge.mjs pair|run')
  }
  const config = loadConfig()
  if (mode === 'pair') {
    await pair(config)
    return
  }
  await run(config)
}

main().catch(error => {
  console.error(`error: ${error.message}`)
  process.exitCode = process.argv[2] === 'run' ? CONFIGURATION_EXIT : 1
})
